"""tone.py -- guitar front-end and master mix utilities.

The ``_tube_distort`` / noise-gate shapes are ported from the sibling
``ww-forge-prior-attempt/engine/riff_engine.py`` (itself adapted from
rust-beats' metal_dsp.rs, MIT). The NAM captures here are "noboost", so a
boost/overdrive stage in front is what makes them sound like a metal amp
instead of a thin clean tone.
"""
from __future__ import annotations

import numpy as np
from scipy.ndimage import uniform_filter1d
from scipy.signal import lfilter

from amp import _hp, _lp, _peak

try:
    from pedalboard import Compressor, HighpassFilter, Limiter, Pedalboard
    _HAVE_PEDALBOARD = True
except Exception:  # noqa: BLE001
    _HAVE_PEDALBOARD = False

_MASTER_BOARD = None


def _master_board():
    global _MASTER_BOARD
    if _MASTER_BOARD is None and _HAVE_PEDALBOARD:
        _MASTER_BOARD = Pedalboard([
            HighpassFilter(cutoff_frequency_hz=28.0),
            Compressor(threshold_db=-18.0, ratio=2.5),
            Limiter(threshold_db=-1.0),
        ])
    return _MASTER_BOARD


def tube_distort(x: np.ndarray, drive: float = 8.0,
                 asymmetry: float = 0.3) -> np.ndarray:
    """Asymmetric tube waveshaper (positive/negative halves driven differently)."""
    x = np.asarray(x, dtype=np.float32)
    driven = x * drive
    pos = np.tanh(driven * (1.0 + asymmetry))
    neg = np.tanh(driven * (1.0 - asymmetry * 0.5))
    return np.where(driven >= 0.0, pos, neg).astype(np.float32)


def noise_gate(x: np.ndarray, sr: int, threshold: float = 0.02,
               ratio: float = 0.0, attack: float = 0.0005,
               release: float = 0.03) -> np.ndarray:
    """Envelope-follower gate: kills hiss/ring between staccato chugs."""
    x = np.asarray(x, dtype=np.float32)
    win = max(1, int(attack * 2 * sr))
    env = uniform_filter1d(np.abs(x), win)
    gain = np.where(env > threshold, 1.0, ratio).astype(np.float32)
    coeff = np.exp(-1.0 / max(1.0, release * sr))
    gain = lfilter([1.0 - coeff], [1.0, -coeff], gain).astype(np.float32)
    return (x * gain).astype(np.float32)


def boost(x: np.ndarray, sr: int, drive: float = 14.0,
          tone: float = 0.5) -> np.ndarray:
    """Tube-Screamer-style boost: low cut, mid hump, asymmetric clip, tone."""
    y = _hp(np.asarray(x, dtype=np.float32), sr, 720)
    y = _peak(y, sr, 720, 0.7, 6.0)
    y = tube_distort(y, drive=drive)
    y = _lp(y, sr, 3500 + 4000 * float(np.clip(tone, 0.0, 1.0)))
    peak = float(np.max(np.abs(y)) + 1e-9)
    return (y / peak).astype(np.float32)


def master_limit(x: np.ndarray, sr: int = 44100, ceiling: float = 0.95,
                 release: float = 0.08) -> np.ndarray:
    """Feed-forward peak limiter with a short release."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    win = max(1, int(0.0015 * sr))
    env = uniform_filter1d(np.abs(x), win, axis=0)
    gain = np.minimum(1.0, ceiling / (env + 1e-9))
    coeff = np.exp(-1.0 / max(1.0, release * sr))
    gain = lfilter([1.0 - coeff], [1.0, -coeff], gain, axis=0).astype(np.float32)
    out = x * gain
    peak = float(np.max(np.abs(out)) + 1e-9)
    if peak > ceiling:
        out = out * (ceiling / peak)
    return out.astype(np.float32)


def master_glue(x: np.ndarray, sr: int = 44100, ceiling: float = 0.95) -> np.ndarray:
    """Master bus: high-pass + gentle glue compression + limiter (pedalboard),
    falling back to the numpy limiter when pedalboard is unavailable."""
    x = np.asarray(x, dtype=np.float32)
    if x.ndim == 1:
        x = x[:, None]
    board = _master_board()
    if board is None:
        return master_limit(x, sr=sr, ceiling=ceiling)
    y = np.asarray(board(x, sr), dtype=np.float32)
    peak = float(np.max(np.abs(y)) + 1e-9)
    if peak > ceiling:
        y = y * (ceiling / peak)
    return y.astype(np.float32)


def mix_buses(buses, gains, sr: int = 44100, ceiling: float = 0.95) -> np.ndarray:
    """Sum pre-rendered buses at fixed relative gains, then master-glue."""
    n = max((len(b) for b in buses), default=0)
    if n == 0:
        return np.zeros((1, 2), dtype=np.float32)
    out = np.zeros((n, 2), dtype=np.float32)
    for bus, gain in zip(buses, gains):
        if bus is None or not len(bus):
            continue
        b = bus if bus.ndim > 1 else np.repeat(bus[:, None], 2, axis=1)
        out[:len(b)] += b * gain
    return master_glue(out, sr=sr, ceiling=ceiling)
