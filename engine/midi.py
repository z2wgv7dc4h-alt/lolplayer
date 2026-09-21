from __future__ import annotations

import threading
import time
from typing import List, Optional, Tuple

import mido

from model import Song

TPQ = 480
DEFAULT_PORT = "Microsoft GS Wavetable Synth 0"
BEND_RANGE = 12          # semitones of pitch-bend range we advertise (RPN 0)
DRUM_CHANNEL = 9

_SHARED: dict = {"port": None, "key": None, "name": None}
_ACTIVE: Optional["Player"] = None


def get_output(port_name: Optional[str] = None):
    key = port_name or DEFAULT_PORT
    if _SHARED["port"] is not None:
        return _SHARED["port"]
    names = mido.get_output_names()
    if not names:
        raise RuntimeError("no MIDI output ports available")
    order = [n for n in names if key in n] + [n for n in names if key not in n]
    last = None
    for name in order:
        try:
            port = mido.open_output(name)
            _SHARED.update(port=port, key=key, name=name)
            return port
        except Exception as exc:  # noqa: BLE001
            last = exc
    raise RuntimeError(f"no usable MIDI output port ({last})")


# --------------------------------------------------------------------------- #
# tempo map
# --------------------------------------------------------------------------- #

def tempo_points(song: Song) -> List[Tuple[float, float]]:
    points: List[Tuple[float, float]] = [(0.0, song.events[0].tempo if song.events else 120.0)]
    for e in sorted(song.events, key=lambda ev: ev.onset_beat):
        if abs(e.tempo - points[-1][1]) > 1e-6:
            points.append((e.onset_beat, e.tempo))
    return points


def beat_to_seconds(beat: float, points: List[Tuple[float, float]]) -> float:
    seconds = 0.0
    cursor = 0.0
    bpm = points[0][1] if points else 120.0
    for point_beat, point_bpm in points:
        if point_beat <= cursor:
            bpm = point_bpm
            continue
        if point_beat >= beat:
            break
        seconds += (point_beat - cursor) * 60.0 / bpm
        cursor = point_beat
        bpm = point_bpm
    return seconds + (beat - cursor) * 60.0 / bpm


def seconds_to_beat(seconds: float, points: List[Tuple[float, float]]) -> float:
    cursor = 0.0
    beat = 0.0
    bpm = points[0][1] if points else 120.0
    for point_beat, point_bpm in points:
        duration = (point_beat - beat) * 60.0 / bpm
        if cursor + duration >= seconds:
            return beat + (seconds - cursor) * bpm / 60.0
        cursor += duration
        beat = point_beat
        bpm = point_bpm
    return beat + (seconds - cursor) * bpm / 60.0


def all_notes_off(port) -> None:
    for channel in range(16):
        try:
            port.send(mido.Message("control_change", channel=channel,
                                   control=123, value=0))
            port.send(mido.Message("control_change", channel=channel,
                                   control=64, value=0))
        except Exception:
            pass


# --------------------------------------------------------------------------- #
# expression + message building (shared by file export and live playback)
# --------------------------------------------------------------------------- #

def _clamp_note(pitch: int) -> int:
    return max(0, min(127, int(round(pitch))))


def _velocity(event) -> int:
    v = float(event.velocity)
    if event.palm_mute:
        v *= 0.72
    if event.ghost:
        v *= 0.6
    if event.dead:
        v *= 0.45
    if event.hammer:
        v *= 0.82
    if event.accentuated:
        v *= 1.18
    return max(1, min(127, int(round(v))))


def _duration_beats(event) -> float:
    d = event.duration_beats
    if event.dead:
        d *= 0.25
    elif event.staccato:
        d *= 0.5
    if event.palm_mute:
        d *= 0.9
    return max(0.03, d)


def _bend_wheel(semitones: float) -> int:
    return max(-8192, min(8191, int(round(semitones / BEND_RANGE * 8191))))


def _track_program(track, guitar_program):
    if guitar_program is None or track.is_drums:
        return track.program
    if 24 <= track.program <= 31:      # anything in the GM guitar family
        return guitar_program
    return track.program


def build_messages(song: Song, mix: bool = True,
                   guitar_program: Optional[int] = None) -> List[Tuple[float, mido.Message]]:
    """(onset_beat, message) for the whole song, expression baked in:
    program changes, per-channel pitch-bend range, note on/off with shaped
    velocity/duration, and pitch-wheel from the real bend curves.

    `mix=True` adds per-track channel volume/pan (CC7/CC10) -- Songsterr's own
    balance; `mix=False` leaves everything flat."""
    out: List[Tuple[float, int, mido.Message]] = []

    for track in song.tracks:
        ch = track.channel
        if mix:
            volume = max(0, min(127, int(round(127 * max(0.0, min(1.27, track.volume))))))
            balance = max(-1.0, min(1.0, track.balance))
            pan = max(0, min(127, int(round(64 + balance * 63))))
            out.append((0.0, -4, mido.Message("control_change", channel=ch,
                                              control=7, value=volume)))
            out.append((0.0, -4, mido.Message("control_change", channel=ch,
                                              control=10, value=pan)))
        if track.is_drums:
            continue
        out.append((0.0, -3, mido.Message(
            "program_change", channel=ch,
            program=_track_program(track, guitar_program))))
        for control, value in ((101, 0), (100, 0), (6, BEND_RANGE), (38, 0)):
            out.append((0.0, -2, mido.Message("control_change", channel=ch,
                                              control=control, value=value)))

    for e in song.events:
        track = song.track(e.track)
        ch = track.channel if track else 0
        note = _clamp_note(e.pitch)
        vel = _velocity(e)
        onset = e.onset_beat
        end = onset + _duration_beats(e)
        out.append((onset, 0, mido.Message("note_on", channel=ch, note=note,
                                           velocity=vel)))
        out.append((end, 2, mido.Message("note_off", channel=ch, note=note,
                                         velocity=0)))
        if e.bends and not (track and track.is_drums):
            span = max(1e-6, end - onset)
            for position, tone in e.bends:
                frac = min(1.0, max(0.0, float(position) / 60.0))
                wheel = _bend_wheel(float(tone) / 100.0)
                out.append((onset + frac * span, 1,
                            mido.Message("pitchwheel", channel=ch, pitch=wheel)))
            out.append((end, 1, mido.Message("pitchwheel", channel=ch, pitch=0)))
    out.sort(key=lambda item: (item[0], item[1]))
    return [(beat, msg) for beat, _order, msg in out]


def song_to_midi(song: Song, mix: bool = True,
                 guitar_program: Optional[int] = None) -> mido.MidiFile:
    midi = mido.MidiFile(type=1, ticks_per_beat=TPQ)
    conductor = mido.MidiTrack()
    midi.tracks.append(conductor)
    last_tick = 0
    for beat, bpm in tempo_points(song):
        tick = int(round(beat * TPQ))
        conductor.append(mido.MetaMessage("set_tempo", tempo=mido.bpm2tempo(bpm),
                                          time=max(0, tick - last_tick)))
        last_tick = tick

    per_channel: dict = {}
    for beat, msg in build_messages(song, mix=mix, guitar_program=guitar_program):
        per_channel.setdefault(msg.channel, []).append((int(round(beat * TPQ)), msg))
    for channel in sorted(per_channel):
        mt = mido.MidiTrack()
        midi.tracks.append(mt)
        prev = 0
        for tick, msg in sorted(per_channel[channel], key=lambda x: x[0]):
            msg.time = max(0, tick - prev)
            prev = tick
            mt.append(msg)
    return midi


def save_midi(song: Song, path) -> str:
    song_to_midi(song).save(str(path))
    return str(path)


def play_song(song: Song, port_name: Optional[str] = None) -> None:
    port = get_output(port_name)
    points = tempo_points(song)
    schedule = [(beat_to_seconds(beat, points), msg) for beat, msg in build_messages(song)]
    schedule.sort(key=lambda item: item[0])
    start = time.perf_counter()
    for when, msg in schedule:
        delay = when - (time.perf_counter() - start)
        if delay > 0:
            time.sleep(delay)
        port.send(msg)
    time.sleep(0.2)


class Player:
    def __init__(self, song: Song, port_name: Optional[str] = None):
        self.song = song
        self.port_name = port_name
        self.points = tempo_points(song)
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._wall0 = 0.0
        self._offset_sec = 0.0
        self._port = None
        self.error: Optional[str] = None
        self._pending_seek: Optional[float] = None
        self._schedule: List[Tuple[float, mido.Message]] = []
        self._idx = 0

    def _open(self):
        self._port = get_output(self.port_name)
        return _SHARED["name"]

    def start(self, start_beat: float = 0.0) -> None:
        global _ACTIVE
        if _ACTIVE is not None and _ACTIVE is not self:
            _ACTIVE.stop()
        _ACTIVE = self
        self._stop = threading.Event()
        self._pending_seek = None
        self._offset_sec = beat_to_seconds(start_beat, self.points)
        self._wall0 = time.perf_counter()
        self._thread = threading.Thread(target=self._run, args=(start_beat,),
                                        daemon=True)
        self._thread.start()

    def _build_schedule(self, start_beat: float) -> None:
        schedule = [(beat_to_seconds(beat, self.points), msg)
                    for beat, msg in build_messages(self.song)
                    if beat >= start_beat - 1e-6]
        schedule.sort(key=lambda item: item[0])
        self._schedule = schedule
        self._idx = 0

    def seek(self, beat: float) -> None:
        self._pending_seek = max(0.0, beat)

    def _run(self, start_beat: float) -> None:
        self._build_schedule(start_beat)
        try:
            self._open()
        except Exception as exc:
            self.error = f"MIDI output failed: {exc}"
            return
        while not self._stop.is_set():
            if self._pending_seek is not None:
                beat = self._pending_seek
                self._pending_seek = None
                all_notes_off(self._port)
                self._offset_sec = beat_to_seconds(beat, self.points)
                self._wall0 = time.perf_counter()
                self._build_schedule(beat)
                continue
            if self._idx >= len(self._schedule):
                break
            when, msg = self._schedule[self._idx]
            elapsed = self._offset_sec + (time.perf_counter() - self._wall0)
            delay = when - elapsed
            if delay > 0:
                self._stop.wait(min(delay, 0.02))
                continue
            self._idx += 1
            try:
                self._port.send(msg)
            except Exception:
                break
        all_notes_off(self._port)

    def stop(self) -> None:
        self._stop.set()
        port = self._port or _SHARED.get("port")
        if port is not None:
            all_notes_off(port)

    def join(self, timeout: float = 1.5) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    def position_beat(self) -> float:
        if self._wall0 == 0.0:
            return 0.0
        elapsed = self._offset_sec + (time.perf_counter() - self._wall0)
        return seconds_to_beat(elapsed, self.points)

    def is_playing(self) -> bool:
        return self._thread is not None and self._thread.is_alive()
