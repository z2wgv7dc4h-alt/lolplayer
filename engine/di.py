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
from scipy.signal import butter, resample_poly, sosfilt

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
    """Nearest recorded pitch, globally; prefer the same string only as a
    tie-breaker. (Preferring the string outright detunes badly whenever the
    tab's string numbering doesn't match the sample library's tuning.)"""
    idx = index()
    if not idx:
        return None
    want = int(string) if string is not None else None
    best = min(idx, key=lambda e: (abs(e[0] - midi),
                                   0 if e[1] == want else 1))
    return best[0], best[2]


def _repitch(x: np.ndarray, semitones: float) -> np.ndarray:
    if abs(semitones) < 1e-6:
        return x
    ratio = 2.0 ** (semitones / 12.0)
    fr = Fraction(ratio).limit_denominator(2000)
    return resample_poly(x, fr.numerator, fr.denominator).astype(np.float32)


def _lowpass(x: np.ndarray, rate: int, cutoff: float) -> np.ndarray:
    sos = butter(2, min(cutoff, rate / 2 - 1) / (rate / 2), btype="low",
                 output="sos")
    return sosfilt(sos, x).astype(np.float32)


def _apply_bends(sample: np.ndarray, bends) -> np.ndarray:
    """Warp the sample to follow a bend contour (position in 1/60 beat, tone in
    cents); implemented as a time-varying resample read position."""
    points = sorted((max(0.0, float(p)), float(t)) for p, t in bends)
    if not points or len(sample) == 0:
        return sample
    fracs = [0.0] + [min(1.0, p / 60.0) for p, _t in points]
    semis = [0.0] + [t / 100.0 for _p, t in points]
    n = len(sample)
    grid = np.linspace(0.0, 1.0, n, dtype=np.float32)
    contour = np.interp(grid, fracs, semis).astype(np.float32)
    ratio = (2.0 ** (contour / 12.0)).astype(np.float32)
    pos = np.cumsum(ratio)
    pos -= pos[0]
    idx = np.arange(n, dtype=np.float32)
    return np.interp(pos, idx, sample, left=0.0, right=0.0).astype(np.float32)


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
        if e.track not in track_indices:
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
        if e.ghost:
            amp *= 0.6
        if e.hammer:
            amp *= 0.82
        if e.dead:
            amp *= 0.5
            hold = int(0.08 * rate)
            if 0 < hold < len(sample):
                sample = _lowpass(sample[:hold].copy(), rate, 900)
            fade = min(int(0.01 * rate), len(sample))
            if fade > 1:
                sample[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
        else:
            if e.palm_mute:
                amp *= 0.8
                sample = _lowpass(sample, rate, 1500)
            note_sec = e.duration_beats * (60.0 / max(1e-6, e.tempo))
            if e.palm_mute:
                note_sec *= 0.6
                tail = 0.015
            else:
                tail = 0.06
            hold = int((note_sec + tail) * rate)
            if 0 < hold < len(sample):
                sample = sample[:hold].copy()
                fade = min(int(0.012 * rate), len(sample))
                if fade > 1:
                    sample[-fade:] *= np.linspace(1.0, 0.0, fade, dtype=np.float32)
            if getattr(e, "bends", None):
                sample = _apply_bends(sample, e.bends)
        if getattr(e, "staccato", False):
            sample = sample[:int(len(sample) * 0.5)]
        start = int((sec - start_sec) * rate)
        if start < 0:
            sample = sample[-start:]
            start = 0
        count = min(len(sample), length - start)
        if count > 0:
            buf[start:start + count] += sample[:count] * amp
    return buf
