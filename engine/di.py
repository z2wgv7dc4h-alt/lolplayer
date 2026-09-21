"""
di.py -- sampled dry-DI electric guitar.

Plays the CC0 `electric-guitar-FSBS-direct` samples in `assets/di/`
(`<Note>_s<string>_<take>.wav`) as a real, dry DI signal. For each guitar event
the nearest recorded pitch is chosen (preferring the same string), then repitched
by the semitone difference -- i.e. a normal sampler. The result is a clean DI
that the NAM amp + real cab IR can actually work with, unlike a soundfont patch.
"""
from __future__ import annotations

import re
from fractions import Fraction
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import soundfile as sf
from scipy.signal import resample_poly

from assets import assets_root
from midi import beat_to_seconds, tempo_points
from model import Song

DI_DIR = assets_root() / "di"
_NAME_RE = re.compile(r"^([A-Ga-g#b]+)(-?\d+)_s(\d+)_(\d+)\.wav$")
_SEMI = {"C": 0, "D": 2, "E": 4, "F": 5, "G": 7, "A": 9, "B": 11}

_CACHE: Dict[str, np.ndarray] = {}
_INDEX: Optional[List[Tuple[int, int, str]]] = None
MAX_SECONDS = 2.0      # recorded notes are ~34 s; a couple of seconds is plenty


def _note_midi(token: str, octave: str) -> Optional[int]:
    m = re.match(r"([A-Ga-g])([#b]*)", token)
    if not m:
        return None
    pc = _SEMI[m.group(1).upper()]
    for a in m.group(2):
        pc += 1 if a == "#" else -1
    return 12 * (int(octave) + 1) + pc


def index() -> List[Tuple[int, int, str]]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    out: List[Tuple[int, int, str]] = []
    if DI_DIR.exists():
        for path in sorted(DI_DIR.glob("*.wav")):
            m = _NAME_RE.match(path.name)
            if not m:
                continue
            midi = _note_midi(m.group(1), m.group(2))
            if midi is None:
                continue
            out.append((midi, int(m.group(3)), str(path)))
    _INDEX = sorted(out)
    return _INDEX


def available() -> bool:
    return bool(index())


def _load(path: str, rate: int) -> np.ndarray:
    key = f"{path}@{rate}"
    x = _CACHE.get(key)
    if x is not None:
        return x
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    cap = int(sr * MAX_SECONDS)
    if len(data) > cap:
        data = data[:cap].copy()
    if sr != rate:
        from math import gcd
        g = gcd(int(sr), int(rate))
        data = resample_poly(data, rate // g, sr // g)
    x = data.astype(np.float32)
    _CACHE[key] = x
    return x


def _pick(midi: int, string: Optional[float]) -> Optional[Tuple[int, str]]:
    idx = index()
    if not idx:
        return None
    same = [e for e in idx if string is not None and e[1] == int(string)]
    pool = same or idx
    best = min(pool, key=lambda e: abs(e[0] - midi))
    return best[0], best[2]


def _repitch(x: np.ndarray, semitones: float) -> np.ndarray:
    if abs(semitones) < 1e-6:
        return x
    ratio = 2.0 ** (semitones / 12.0)
    fr = Fraction(ratio).limit_denominator(2000)
    return resample_poly(x, fr.numerator, fr.denominator).astype(np.float32)


def render(song: Song, rate: int = 22050, gain: float = 0.5,
           track_indices=None) -> Optional[np.ndarray]:
    if not available():
        return None
    points = tempo_points(song)
    dur = beat_to_seconds(song.duration_beats(), points) if song.duration_beats() else 0.0
    return _render(song, points, rate, gain, 0.0, int((dur + 2.0) * rate),
                   track_indices)


def render_window(song: Song, start_sec: float, end_sec: float, rate: int = 22050,
                  gain: float = 0.5, track_indices=None) -> Optional[np.ndarray]:
    if not available():
        return None
    points = tempo_points(song)
    return _render(song, points, rate, gain, start_sec,
                   max(1, int((end_sec - start_sec) * rate)), track_indices)


def _render(song: Song, points, rate: int, gain: float, start_sec: float,
            length: int, track_indices) -> np.ndarray:
    if track_indices is None:
        from synth import category_of
        track_indices = {t.index for t in song.tracks if category_of(t) == "guitar"}
    track_indices = set(track_indices)
    buf = np.zeros(length, dtype=np.float32)
    end_sec = start_sec + length / rate
    for e in song.events:
        if e.track not in track_indices or e.dead:
            continue
        sec = beat_to_seconds(e.onset_beat, points)
        if sec < start_sec - 0.05 or sec >= end_sec:
            continue
        pick = _pick(e.pitch, e.string)
        if pick is None:
            continue
        key, path = pick
        sample = _repitch(_load(path, rate), e.pitch - key)
        vel = max(1, min(127, e.velocity)) / 127.0
        amp = gain * (vel ** 1.3)
        if e.palm_mute:
            amp *= 0.8
        note_sec = e.duration_beats * (60.0 / max(1e-6, e.tempo))
        hold = int((note_sec + (0.05 if e.palm_mute else 0.12)) * rate)
        if 0 < hold < len(sample):
            sample = sample[:hold].copy()
            fade = min(int(0.012 * rate), len(sample))
            if fade > 1:
                sample[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        start = int((sec - start_sec) * rate)
        if start < 0:
            sample = sample[-start:]
            start = 0
        count = min(len(sample), length - start)
        if count > 0:
            buf[start:start + count] += sample[:count] * amp
    return buf
