"""
fastsynth.py -- persistent FluidSynth renderer via libfluidsynth (ctypes).

Launching the fluidsynth CLI costs ~5 s each time just to load the soundfont.
Here the font is loaded once per process and songs are rendered offline with
fluid_synth_write_float, so a full song renders in well under a second.
Falls back to the CLI if the DLL is unavailable.
"""
from __future__ import annotations

import ctypes
import os
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import soundfile as sf

from assets import tools_root
from midi import beat_to_seconds, tempo_points
from model import Song

DLL_CANDIDATES = [
    str(tools_root() / "fluidsynth" / "bin" / "libfluidsynth-3.dll"),
]
BLOCK = 4096
TAIL_SECONDS = 2.0


def _find_dll() -> Optional[str]:
    for cand in DLL_CANDIDATES:
        if Path(cand).exists():
            return cand
    return None


class _Synth:
    def __init__(self, dll_path: str, soundfont: str, rate: int = 44100):
        bin_dir = str(Path(dll_path).parent)
        if hasattr(os, "add_dll_directory"):
            os.add_dll_directory(bin_dir)
        lib = ctypes.CDLL(dll_path)

        lib.new_fluid_settings.restype = ctypes.c_void_p
        lib.new_fluid_synth.restype = ctypes.c_void_p
        lib.new_fluid_synth.argtypes = [ctypes.c_void_p]
        lib.fluid_settings_setnum.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_double]
        lib.fluid_settings_setint.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.fluid_settings_setstr.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_char_p]
        lib.fluid_synth_sfload.argtypes = [ctypes.c_void_p, ctypes.c_char_p, ctypes.c_int]
        lib.fluid_synth_sfload.restype = ctypes.c_int
        lib.fluid_synth_write_float.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_int, ctypes.c_int,
            ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_noteon.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_noteoff.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_cc.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_program_change.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_pitch_bend.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
        lib.fluid_synth_set_reverb_on.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.fluid_synth_set_reverb.argtypes = [ctypes.c_void_p, ctypes.c_double,
                                               ctypes.c_double, ctypes.c_double, ctypes.c_double]
        lib.fluid_synth_set_gain.argtypes = [ctypes.c_void_p, ctypes.c_float]
        lib.fluid_synth_all_notes_off.argtypes = [ctypes.c_void_p, ctypes.c_int]
        lib.fluid_synth_all_sounds_off.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib = lib

        self.settings = lib.new_fluid_settings()
        lib.fluid_settings_setnum(self.settings, b"synth.sample-rate", ctypes.c_double(rate))
        lib.fluid_settings_setint(self.settings, b"synth.polyphony", 256)
        self.synth = lib.new_fluid_synth(self.settings)
        self.sfont = lib.fluid_synth_sfload(self.synth, str(soundfont).encode(), 1)
        if self.sfont < 0:
            raise RuntimeError("fluid_synth_sfload failed")

    def reset(self) -> None:
        for ch in range(16):
            self.lib.fluid_synth_all_notes_off(self.synth, ch)
            self.lib.fluid_synth_all_sounds_off(self.synth, ch)

    def dispatch(self, msg) -> None:
        lib, s = self.lib, self.synth
        t = msg.type
        if t == "note_on":
            if msg.velocity > 0:
                lib.fluid_synth_noteon(s, msg.channel, msg.note, msg.velocity)
            else:
                lib.fluid_synth_noteoff(s, msg.channel, msg.note)
        elif t == "note_off":
            lib.fluid_synth_noteoff(s, msg.channel, msg.note)
        elif t == "program_change":
            lib.fluid_synth_program_change(s, msg.channel, msg.program)
        elif t == "control_change":
            lib.fluid_synth_cc(s, msg.channel, msg.control, msg.value)
        elif t == "pitchwheel":
            lib.fluid_synth_pitch_bend(s, msg.channel, max(0, min(16383, msg.pitch + 8192)))

    def write(self, left: np.ndarray, right: np.ndarray, frames: int) -> None:
        self.lib.fluid_synth_write_float(
            self.synth, frames,
            left.ctypes.data_as(ctypes.c_void_p), 0, 1,
            right.ctypes.data_as(ctypes.c_void_p), 0, 1)


_CACHE: dict = {}          # (dll, soundfont, rate) -> _Synth


def available() -> bool:
    return _find_dll() is not None


def warm(soundfont: str, rate: int = 44100) -> bool:
    """Load the soundfont for this rate now, so the first real render is fast."""
    dll = _find_dll()
    if not dll:
        return False
    try:
        _get(dll, soundfont, rate)
        return True
    except Exception:  # noqa: BLE001
        return False


def _get(dll: str, soundfont: str, rate: int) -> _Synth:
    key = (dll, soundfont, rate)
    engine = _CACHE.get(key)
    if engine is None:
        engine = _Synth(dll, soundfont, rate)
        _CACHE[key] = engine
    return engine


def render_array(song: Song, soundfont: str, mix: bool = True, rate: int = 44100,
                 gain: float = 0.5, guitar_program: Optional[int] = None,
                 reverb: Optional[bool] = None,
                 normalize: bool = True) -> np.ndarray:
    """Render to a float32 (frames, 2) array -- no disk round-trip."""
    dll = _find_dll()
    if not dll:
        raise RuntimeError("libfluidsynth not found")
    engine = _get(dll, soundfont, rate)
    engine.reset()
    use_reverb = mix if reverb is None else reverb
    engine.lib.fluid_synth_set_reverb_on(engine.synth, 1 if use_reverb else 0)
    engine.lib.fluid_synth_set_reverb(engine.synth, 0.4, 0.6, 0.7, 0.25)
    engine.lib.fluid_synth_set_gain(engine.synth, gain)

    from midi import build_messages
    points = tempo_points(song)
    messages = [(beat_to_seconds(beat, points), msg)
                for beat, msg in build_messages(song, mix=mix,
                                                guitar_program=guitar_program)]
    messages.sort(key=lambda item: item[0])
    total_seconds = max((beat_to_seconds(song.duration_beats(), points)
                         if song.duration_beats() else 0.0), 0.0) + TAIL_SECONDS
    total_frames = int(total_seconds * rate)
    left = np.zeros(total_frames, dtype=np.float32)
    right = np.zeros(total_frames, dtype=np.float32)

    idx = 0
    pos = 0
    while pos < total_frames:
        frames = min(BLOCK, total_frames - pos)
        while idx < len(messages) and messages[idx][0] * rate <= pos:
            engine.dispatch(messages[idx][1])
            idx += 1
        engine.write(left[pos:pos + frames], right[pos:pos + frames], frames)
        pos += frames

    data = np.stack([left, right], axis=1)
    if normalize:
        peak = float(np.max(np.abs(data)) + 1e-9)
        data = (data * (0.95 / peak)).astype(np.float32)
    return data


def render(song: Song, out_wav, soundfont: str, mix: bool = True,
           rate: int = 44100, gain: float = 0.5,
           guitar_program: Optional[int] = None,
           reverb: Optional[bool] = None) -> str:
    data = render_array(song, soundfont, mix=mix, rate=rate, gain=gain,
                        guitar_program=guitar_program, reverb=reverb)
    out_wav = Path(out_wav)
    out_wav.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(out_wav), data, rate, subtype="PCM_16")
    return str(out_wav)
