"""
drums.py -- multisampled drum engine.

Plays `assets/drums/*.wav` (5 velocity takes per articulation, named
`<midi>-<kit><instrument>-<take>.wav`) with velocity layers + round-robin,
anti-aliased rate conversion, per-instrument gain/pan, then a drum-bus
treatment (parallel compression + short room). Replaces the GM drum channel.
"""
from __future__ import annotations

import random
import re
from math import gcd
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import soundfile as sf
from scipy.ndimage import uniform_filter1d
from scipy.signal import butter, fftconvolve, resample_poly, sosfilt

from assets import assets_root
from midi import beat_to_seconds, tempo_points
from model import Song

DRUM_DIR = assets_root() / "drums"
KIT_PREFERENCE = ["Pearl", "Ludwig"]     # Black Pearl first
_NAME_RE = re.compile(r"^(\d+)-(.+?)(?:-(\d+))?\.wav$", re.I)

_AUDIO_CACHE: Dict[str, np.ndarray] = {}
_INDEX: Optional[Dict[int, Dict[str, List[str]]]] = None
_ROOM_CACHE: Dict[int, np.ndarray] = {}


def _gain_pan(midi: int) -> tuple:
    if midi in (35, 36):
        return 1.00, 0.0
    if midi in (38, 40):
        return 0.90, 0.0
    if midi == 37:
        return 0.70, 0.0
    if midi in (42, 44, 46, 48):
        return 0.50, 0.22
    if midi in (41, 43, 45, 47):
        return 0.80, (-0.30 if midi in (41, 43) else 0.30)
    if midi in (49, 50, 57, 58):
        return 0.60, (0.40 if midi in (49, 57) else -0.40)
    if midi in (51, 52, 53, 59):
        return 0.60, 0.35
    if midi in (54, 55, 56):
        return 0.50, 0.25
    return 0.70, 0.0


def _kit_key(name: str) -> str:
    for kit in KIT_PREFERENCE:
        if name.lower().startswith(kit.lower()):
            return kit
    return "other"


def index() -> Dict[int, Dict[str, List[str]]]:
    global _INDEX
    if _INDEX is not None:
        return _INDEX
    out: Dict[int, Dict[str, List[str]]] = {}
    if DRUM_DIR.exists():
        for path in sorted(DRUM_DIR.glob("*.wav")):
            m = _NAME_RE.match(path.name)
            if not m:
                continue
            midi = int(m.group(1))
            take = int(m.group(3)) if m.group(3) else 0
            kit = _kit_key(m.group(2))
            out.setdefault(midi, {}).setdefault(kit, []).append((take, str(path)))
    for kits in out.values():
        for kit in kits:
            kits[kit] = [p for _t, p in sorted(kits[kit])]
    _INDEX = out
    return out


def available() -> bool:
    return bool(index())


def _samples_for(midi: int) -> Optional[List[str]]:
    kits = index().get(midi)
    if not kits:
        return None
    for kit in KIT_PREFERENCE:
        if kit in kits:
            return kits[kit]
    return next(iter(kits.values()))


def _load(path: str, rate: int) -> np.ndarray:
    key = f"{path}@{rate}"
    cached = _AUDIO_CACHE.get(key)
    if cached is not None:
        return cached
    data, sr = sf.read(path, dtype="float32", always_2d=False)
    if data.ndim > 1:
        data = data.mean(axis=1)
    if sr != rate:
        g = gcd(int(sr), int(rate))
        data = resample_poly(data, rate // g, sr // g).astype(np.float32)
    _AUDIO_CACHE[key] = data
    return data


def _room_ir(rate: int, seconds: float = 0.35) -> np.ndarray:
    cached = _ROOM_CACHE.get(rate)
    if cached is not None:
        return cached
    n = max(64, int(rate * seconds))
    rng = np.random.default_rng(3)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-8.0 * np.linspace(0, 1, n))
    ir = sosfilt(butter(2, 6000 / (rate / 2), btype="low", output="sos"), ir)
    ir = (ir / (np.max(np.abs(ir)) + 1e-9)).astype(np.float32)
    _ROOM_CACHE[rate] = ir
    return ir


def _compress(x: np.ndarray, rate: int, threshold: float = 0.5,
              ratio: float = 4.0) -> np.ndarray:
    win = max(1, int(rate * 0.004))
    env = np.sqrt(uniform_filter1d(x ** 2, size=win, axis=0) + 1e-12)
    over = env > threshold
    gain = np.ones_like(env, dtype=np.float32)
    gain[over] = (threshold + (env[over] - threshold) / ratio) / env[over]
    return x * gain


def _bus_treatment(buf: np.ndarray, rate: int) -> np.ndarray:
    if not len(buf):
        return buf
    comp = _compress(buf, rate)
    buf = 0.75 * buf + 0.45 * comp          # parallel compression
    room = fftconvolve(buf.mean(axis=1), _room_ir(rate))[:len(buf)].astype(np.float32)
    buf = buf + np.stack([room, room], axis=1) * 0.18
    peak = float(np.max(np.abs(buf)) + 1e-9)
    return (buf / peak).astype(np.float32)


def render(song: Song, rate: int = 22050, gain: float = 0.9,
           seed: int = 0) -> Optional[np.ndarray]:
    if not available():
        return None
    points = tempo_points(song)
    dur = beat_to_seconds(song.duration_beats(), points) if song.duration_beats() else 0.0
    total = int((dur + 2.0) * rate)
    buf = _render_events(song, points, rate, gain, seed, 0.0, total)
    return _bus_treatment(buf, rate)


def render_window(song: Song, start_sec: float, end_sec: float, rate: int = 22050,
                  gain: float = 0.9, seed: int = 0) -> Optional[np.ndarray]:
    if not available():
        return None
    points = tempo_points(song)
    length = max(1, int((end_sec - start_sec) * rate))
    return _render_events(song, points, rate, gain, seed, start_sec, length)


def _render_events(song: Song, points, rate: int, gain: float, seed: int,
                   start_sec: float, length: int) -> np.ndarray:
    buf = np.zeros((length, 2), dtype=np.float32)
    rng = random.Random(seed)
    round_robin: Dict[int, int] = {}
    drum_tracks = {t.index for t in song.tracks if t.is_drums}
    if not drum_tracks:
        return buf
    end_sec = start_sec + length / rate
    for e in song.events:
        if e.track not in drum_tracks:
            continue
        sec = beat_to_seconds(e.onset_beat, points)
        if sec < start_sec - 0.05 or sec >= end_sec:
            continue
        paths = _samples_for(e.pitch)
        if not paths:
            continue
        vel = max(1, min(127, e.velocity)) / 127.0
        n = len(paths)
        layer = int(round(vel * (n - 1)))
        rr = round_robin.get(e.pitch, 0)
        round_robin[e.pitch] = rr + 1
        sample = _load(paths[(layer + rr) % n], rate) * (vel ** 1.4)
        g, pan = _gain_pan(e.pitch)
        start = int((sec - start_sec) * rate)
        if start < 0:
            sample = sample[-start:]
            start = 0
        count = min(len(sample), length - start)
        if count <= 0:
            continue
        left = np.clip(0.5 * (1 - pan), 0, 1) ** 0.5
        right = np.clip(0.5 * (1 + pan), 0, 1) ** 0.5
        amp = gain * g
        buf[start:start + count, 0] += sample[:count] * amp * left
        buf[start:start + count, 1] += sample[:count] * amp * right
    return buf
