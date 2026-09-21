import numpy as np

from di import _apply_bends


def test_apply_bends_empty_returns_input():
    sample = np.ones(1000, dtype=np.float32)
    assert _apply_bends(sample, []) is sample


def test_apply_bends_preserves_length_and_is_finite():
    sample = np.sin(np.linspace(0, 40, 4000)).astype(np.float32)
    out = _apply_bends(sample, [(0.0, 0.0), (30.0, 200.0), (60.0, 0.0)])
    assert len(out) == len(sample)
    assert np.isfinite(out).all()


def test_apply_bends_changes_signal():
    sample = np.sin(np.linspace(0, 200, 8000)).astype(np.float32)
    flat = _apply_bends(sample, [])
    bent = _apply_bends(sample, [(0.0, 0.0), (60.0, 1200.0)])
    assert not np.allclose(flat, bent)
