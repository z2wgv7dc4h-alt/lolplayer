"""
synth.py -- render a Song to WAV through FluidSynth (single fast pass).

The mix (per-track volume/pan) is baked into the MIDI as channel CC7/CC10, the
way Songsterr does it, so one FluidSynth invocation produces the whole mix.
`mix=True` adds FluidSynth's own reverb; `mix=False` is dry.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

from dataclasses import replace

import numpy as np
import soundfile as sf

from amp import amp as amp_process
from amp import cabinet_ir
from assets import tools_root
from midi import beat_to_seconds, seconds_to_beat, song_to_midi, tempo_points
from model import Song, Track

NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RENDER_VERSION = "6"   # bump when the render pipeline changes (cache key)

FLUIDSYNTH_CANDIDATES = [
    str(tools_root() / "fluidsynth" / "bin" / "fluidsynth.exe"),
    "fluidsynth",
]
SOUNDFONT_CANDIDATES = [
    os.environ.get("RIFFER_SOUNDFONT", ""),
    str(tools_root() / "soundfonts" / "MuseScore_General.sf3"),
    str(tools_root() / "soundfonts" / "FluidR3_GM.sf2"),
    str(tools_root() / "soundfonts" / "GeneralUser-GS.sf2"),
]


def find_fluidsynth() -> Optional[str]:
    override = os.environ.get("RIFFER_FLUIDSYNTH")
    for cand in ([override] if override else []) + FLUIDSYNTH_CANDIDATES:
        if not cand:
            continue
        if Path(cand).exists():
            return cand
        found = shutil.which(cand)
        if found:
            return found
    return None


def find_soundfont() -> Optional[str]:
    for cand in SOUNDFONT_CANDIDATES:
        if cand and Path(cand).exists():
            return cand
    return None


def category_of(track: Track) -> str:
    if track is None:
        return "other"
    if track.is_drums:
        return "drums"
    p = track.program
    if 24 <= p <= 31:
        return "guitar"
    if 32 <= p <= 39:
        return "bass"
    if 0 <= p <= 7:
        return "keys"
    return "other"


def render(song: Song, out_wav, mix: bool = True, quality: str = "final",
           soundfont: Optional[str] = None, fluidsynth: Optional[str] = None,
           gain: float = 0.5, rate: Optional[int] = None,
           normalize: bool = True, guitar_program: Optional[int] = None,
           amp: bool = False) -> str:
    preview = quality == "preview"
    eff_rate = rate or (22050 if preview else 44100)
    if amp:
        return render_amped(song, out_wav, mix=mix, soundfont=soundfont,
                            fluidsynth=fluidsynth, gain=gain, rate=eff_rate,
                            guitar_program=27 if guitar_program is None
                            else guitar_program)
    exe = fluidsynth or find_fluidsynth()
    sf2 = soundfont or find_soundfont()
    if not exe:
        raise RuntimeError("fluidsynth not found (set RIFFER_FLUIDSYNTH)")
    if not sf2:
        raise RuntimeError("no soundfont found (set RIFFER_SOUNDFONT)")

    cores = max(1, min(4, (os.cpu_count() or 1)))

    try:
        import drums
        import fastsynth
        if fastsynth.available():
            if amp or drums.available():
                data = _render_stems_full(song, mix=mix, rate=eff_rate, gain=gain,
                                          amp=amp,
                                          guitar_program=guitar_program)
                return _write_array(data, out_wav, eff_rate)
            return fastsynth.render(song, out_wav, sf2, mix=mix, rate=eff_rate,
                                    gain=gain, guitar_program=guitar_program)
    except Exception:
        pass

    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(suffix=".mid", delete=False) as tmp:
        mid = tmp.name
    try:
        song_to_midi(song, mix=mix, guitar_program=guitar_program).save(mid)
        cmd = [exe, "-ni", "-g", str(gain), "-r", str(eff_rate),
               "-o", f"synth.cpu-cores={cores}",
               "-o", "synth.chorus.active=0",
               "-o", f"synth.reverb.active={1 if mix else 0}",
               "-o", "synth.reverb.room-size=0.4",
               "-o", "synth.reverb.damp=0.6",
               "-o", "synth.reverb.width=0.7",
               "-o", "synth.reverb.level=0.25",
               "-F", str(out_wav), sf2, mid]
        proc = subprocess.run(cmd, capture_output=True, timeout=600,
                              creationflags=NO_WINDOW)
        if proc.returncode != 0 or not out_wav.exists():
            raise RuntimeError(proc.stderr.decode("utf-8", "replace")[:300])
    finally:
        try:
            os.remove(mid)
        except OSError:
            pass

    if normalize:
        data, sr = sf.read(str(out_wav), always_2d=True, dtype="float32")
        peak = float(np.max(np.abs(data)) + 1e-9)
        if peak > 0:
            data = (data * (0.95 / peak)).astype(np.float32)
            sf.write(str(out_wav), data, sr, subtype="PCM_16")
    return str(out_wav)


def trim_song(song: Song, seconds: float) -> Song:
    end_beat = seconds_to_beat(seconds, tempo_points(song))
    events = [e for e in song.events if e.onset_beat < end_beat]
    return Song(slug=song.slug, title=song.title, artist=song.artist,
                tracks=song.tracks, events=events, source=None)


def _subsong(song: Song, tracks) -> Song:
    keep = {t.index for t in tracks}
    return Song(slug=song.slug, title=song.title, artist=song.artist,
                tracks=tracks, source=None,
                events=[e for e in song.events if e.track in keep])


def _sum_buses(buses, normalize: bool = True) -> np.ndarray:
    buses = [b for b in buses if b is not None and len(b)]
    if not buses:
        return np.zeros((1, 2), dtype=np.float32)
    n = max(len(b) for b in buses)
    out = np.zeros((n, 2), dtype=np.float32)
    for b in buses:
        out[:len(b)] += b
    if not normalize:
        return (out * 0.6).astype(np.float32)
    peak = float(np.max(np.abs(out)) + 1e-9)
    return (out / peak * 0.95).astype(np.float32)


def _stem_tracks(song: Song):
    drums = [t for t in song.tracks if t.is_drums]
    guitars = [t for t in song.tracks if category_of(t) == "guitar"]
    others = [t for t in song.tracks
              if not t.is_drums and category_of(t) != "guitar"]
    return drums, guitars, others


def _guitar_bus(di: np.ndarray, rate: int, drive: float = 14.0,
                amp_name: Optional[str] = None,
                cab_name: Optional[str] = None) -> np.ndarray:
    mono = di.mean(axis=1) if di.ndim > 1 else di
    proc = None
    try:
        import namamp
        if namamp.available():
            proc = namamp.process(mono, rate, name=amp_name, cab_name=cab_name)
    except Exception:  # noqa: BLE001
        proc = None
    if proc is None:
        proc = amp_process(mono, rate, drive=drive, cab=cabinet_ir(rate))
    d = int(0.008 * rate)
    right = np.concatenate([np.zeros(d, dtype=np.float32), proc])[:len(proc)]
    return np.stack([proc, right], axis=1).astype(np.float32)


def _render_stems_full(song: Song, mix=True, rate=44100, gain=0.5, amp=True,
                       guitar_program=None, reverb=None) -> np.ndarray:
    import drums
    import fastsynth
    sf2 = find_soundfont()
    dtracks, gtracks, otracks = _stem_tracks(song)
    buses = []
    if otracks:
        buses.append(fastsynth.render_array(_subsong(song, otracks), sf2, mix=mix,
                                            rate=rate, gain=gain, reverb=reverb,
                                            normalize=False))
    if gtracks:
        if amp:
            gp = 27 if guitar_program is None else guitar_program
            di_bus = None
            try:
                import di as disrc
                if disrc.available():
                    di_bus = disrc.render(song, rate=rate, gain=gain,
                                          track_indices={t.index for t in gtracks})
            except Exception:  # noqa: BLE001
                di_bus = None
            if di_bus is None:
                di_bus = fastsynth.render_array(_subsong(song, gtracks), sf2,
                                                mix=False, rate=rate, gain=gain,
                                                guitar_program=gp, reverb=False,
                                                normalize=False)
            buses.append(_guitar_bus(di_bus, rate))
        else:
            buses.append(fastsynth.render_array(_subsong(song, gtracks), sf2,
                                                mix=mix, rate=rate, gain=gain,
                                                guitar_program=guitar_program,
                                                reverb=reverb, normalize=False))
    if dtracks and drums.available():
        dbus = drums.render(song, rate=rate, gain=0.9)
        if dbus is not None and len(dbus):
            buses.append(dbus)
    return _sum_buses(buses)


def _render_stems_window(song: Song, start_sec: float, end_sec: float,
                         rate=22050, mix=True, gain=0.5, amp=True,
                         guitar_program=None, lookback=1.5):
    import drums
    import fastsynth
    sf2 = find_soundfont()
    points = tempo_points(song)
    begin_beat = seconds_to_beat(max(0.0, start_sec - lookback), points)
    end_beat = seconds_to_beat(end_sec, points)
    if end_beat <= begin_beat:
        return None
    length = int((end_sec - start_sec) * rate)
    dtracks, gtracks, otracks = _stem_tracks(song)
    buses = []

    def span(tracks, use_mix, gp):
        data = fastsynth.render_array(
            _shift_span(song, tracks, begin_beat, end_beat), sf2, mix=use_mix,
            rate=rate, gain=gain, reverb=False, guitar_program=gp,
            normalize=False)
        begin_sec = beat_to_seconds(begin_beat, points)
        drop = int((start_sec - begin_sec) * rate)
        return data[drop:drop + length]

    if otracks:
        buses.append(span(otracks, False, None))
    if gtracks:
        if amp:
            gp = 27 if guitar_program is None else guitar_program
            di_bus = None
            try:
                import di as disrc
                if disrc.available():
                    di_bus = disrc.render_window(
                        song, start_sec, end_sec, rate=rate, gain=gain,
                        track_indices={t.index for t in gtracks})
            except Exception:  # noqa: BLE001
                di_bus = None
            if di_bus is None:
                di_bus = span(gtracks, False, gp)
            buses.append(_guitar_bus(di_bus, rate))
        else:
            buses.append(span(gtracks, False, guitar_program))
    if dtracks and drums.available():
        dbus = drums.render_window(song, start_sec, end_sec, rate=rate, gain=0.9)
        if dbus is not None:
            buses.append(dbus)
    return _sum_buses(buses, normalize=False)


def _write_array(data: np.ndarray, out_wav, rate: int) -> str:
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), data, rate, subtype="PCM_16")
    return str(out_wav)


def _amped_array(song: Song, mix: bool = True, rate: int = 44100,
                 gain: float = 0.5, guitar_program: int = 27,
                 drive: float = 14.0, reverb: bool = False) -> np.ndarray:
    """Guitars as clean DI -> amp+cab; everything else rendered normally."""
    import fastsynth
    sf2 = find_soundfont()
    gtracks = [t for t in song.tracks if category_of(t) == "guitar"]
    if not gtracks:
        return fastsynth.render_array(song, sf2, mix=mix, rate=rate, gain=gain,
                                      guitar_program=guitar_program,
                                      reverb=reverb)
    gset = {t.index for t in gtracks}
    guitar_song = Song(slug=song.slug, title=song.title, artist=song.artist,
                       tracks=gtracks, source=None,
                       events=[e for e in song.events if e.track in gset])
    other_tracks = [t for t in song.tracks if t.index not in gset]
    other_song = Song(slug=song.slug, title=song.title, artist=song.artist,
                      tracks=other_tracks, source=None,
                      events=[e for e in song.events if e.track not in gset])

    di = fastsynth.render_array(guitar_song, sf2, mix=False, rate=rate, gain=gain,
                                guitar_program=guitar_program, reverb=False,
                                normalize=False)
    sr = rate
    mono = di.mean(axis=1)
    processed = amp_process(mono, sr, drive=drive, cab=cabinet_ir(sr))
    delay = int(0.008 * sr)
    right = np.concatenate([np.zeros(delay, dtype=np.float32), processed])[:len(processed)]
    guitar_bus = np.stack([processed, right], axis=1).astype(np.float32)

    if other_song.events:
        other = fastsynth.render_array(other_song, sf2, mix=mix, rate=rate,
                                       gain=gain, reverb=reverb, normalize=False)
        n = max(len(guitar_bus), len(other))
        buf = np.zeros((n, 2), dtype=np.float32)
        buf[:len(guitar_bus)] += guitar_bus
        buf[:len(other)] += other
    else:
        buf = guitar_bus
    peak = float(np.max(np.abs(buf)) + 1e-9)
    return (buf / peak * 0.95).astype(np.float32)


def render_amped(song: Song, out_wav, mix: bool = True,
                 soundfont: Optional[str] = None,
                 fluidsynth: Optional[str] = None, gain: float = 0.5,
                 rate: Optional[int] = None, guitar_program: int = 27,
                 drive: float = 14.0) -> str:
    buf = _amped_array(song, mix=mix, rate=rate or 44100, gain=gain,
                       guitar_program=guitar_program, drive=drive, reverb=mix)
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), buf, rate or 44100, subtype="PCM_16")
    return str(out_wav)


def _shift_span(song: Song, tracks, begin_beat: float, end_beat: float) -> Song:
    keep = {t.index for t in tracks}
    events = [replace(e, onset_beat=e.onset_beat - begin_beat, measure=0)
              for e in song.events
              if e.track in keep and e.onset_beat < end_beat
              and e.end_beat() > begin_beat]
    return Song(slug=song.slug, title=song.title, artist=song.artist,
                tracks=tracks, events=events, source=None)


def render_span_array(song: Song, start_sec: float, end_sec: float,
                      rate: int = 22050, mix: bool = True, gain: float = 0.5,
                      amp: bool = True, guitar_program: Optional[int] = None,
                      lookback: float = 1.5) -> Optional[np.ndarray]:
    """Render a time window (with a short lookback for sustaining notes) as a
    float32 (frames, 2) array; used for streaming playback. No reverb."""
    try:
        import fastsynth
        if not fastsynth.available():
            return None
    except Exception:  # noqa: BLE001
        return None
    return _render_stems_window(song, start_sec, end_sec, rate=rate, mix=mix,
                                gain=gain, amp=amp, guitar_program=guitar_program,
                                lookback=lookback)


def render_head(song: Song, out_wav, seconds: float = 45.0, mix: bool = True,
                soundfont: Optional[str] = None,
                fluidsynth: Optional[str] = None) -> str:
    return render(trim_song(song, seconds), out_wav, mix=mix,
                  quality="preview", soundfont=soundfont, fluidsynth=fluidsynth,
                  normalize=False)


def render_simple(song: Song, out_wav, soundfont: Optional[str] = None,
                  fluidsynth: Optional[str] = None, gain: float = 0.55,
                  rate: int = 44100) -> str:
    return render(song, out_wav, mix=False, soundfont=soundfont,
                  fluidsynth=fluidsynth, gain=gain, rate=rate)


def render_mix(song: Song, out_wav, soundfont: Optional[str] = None,
               fluidsynth: Optional[str] = None, gain: float = 0.55,
               rate: int = 44100) -> str:
    return render(song, out_wav, mix=True, soundfont=soundfont,
                  fluidsynth=fluidsynth, gain=gain, rate=rate)


def render_wav(song: Song, out_wav, soundfont: Optional[str] = None,
               fluidsynth: Optional[str] = None, gain: float = 0.55,
               rate: int = 44100) -> str:
    return render(song, out_wav, mix=True, soundfont=soundfont,
                  fluidsynth=fluidsynth, gain=gain, rate=rate)
