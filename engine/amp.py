"""
amp.py -- tiny guitar amp + cabinet simulator (numpy/scipy only).

GM guitar patches sound like toys. This takes a clean guitar DI and pushes it
through a tube-ish waveshaper, a tone stage and a synthesized cabinet impulse
response, which is what actually makes an electric guitar sound like a guitar.
No plugins, no downloads.
"""
from __future__ import annotations

import numpy as np
from scipy.signal import butter, fftconvolve, sosfilt, tf2sos


def _hp(x, sr, f, order=2):
    return sosfilt(butter(order, f / (sr / 2), btype="high", output="sos"), x)


def _lp(x, sr, f, order=2):
    return sosfilt(butter(order, min(f, sr / 2 - 1) / (sr / 2), btype="low",
                          output="sos"), x)


def _peak(x, sr, f, q, gain_db):
    """A peaking EQ band (mid scoop / presence)."""
    a = 10 ** (gain_db / 40.0)
    w = 2 * np.pi * f / sr
    alpha = np.sin(w) / (2 * q)
    b = [1 + alpha * a, -2 * np.cos(w), 1 - alpha * a]
    a_coef = [1 + alpha / a, -2 * np.cos(w), 1 - alpha / a]
    return sosfilt(tf2sos(b, a_coef), x)


def cabinet_ir(sr: int, seed: int = 0, seconds: float = 0.05) -> np.ndarray:
    n = max(64, int(sr * seconds))
    rng = np.random.default_rng(seed)
    ir = rng.standard_normal(n).astype(np.float32) * np.exp(-20.0 * np.linspace(0, 1, n))
    ir = _hp(ir, sr, 75)
    ir = _lp(ir, sr, 6500)
    ir = _peak(ir, sr, 2200, 1.0, 4.0)
    return (ir / (np.max(np.abs(ir)) + 1e-9)).astype(np.float32)


def amp(di: np.ndarray, sr: int, drive: float = 14.0,
        cab: np.ndarray | None = None) -> np.ndarray:
    """Clean DI -> distorted, cab-filtered guitar (float32, peak <= 1)."""
    x = di.astype(np.float32).copy()
    x = _hp(x, sr, 85)
    x = np.tanh(x * drive)                       # tube-ish clipping
    x = _hp(x, sr, 95)
    x = _lp(x, sr, 7000)
    x = _peak(x, sr, 700, 0.9, -3.0)             # mid scoop
    x = _peak(x, sr, 3500, 1.2, 3.0)             # presence
    if cab is not None:
        wet = fftconvolve(x, cab)[:len(x)]
        x = 0.65 * x + 1.6 * wet
    peak = float(np.max(np.abs(x)) + 1e-9)
    return (x / peak * 0.9).astype(np.float32)
