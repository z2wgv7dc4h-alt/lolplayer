import numpy as np

from tone import boost, master_glue, master_limit, mix_buses, noise_gate, tube_distort


def test_tube_distort_bounded_and_asymmetric():
    x = np.linspace(-1, 1, 1000, dtype=np.float32)
    y = tube_distort(x, drive=8.0)
    assert np.isfinite(y).all()
    assert float(np.max(np.abs(y))) <= 1.0
    pos = float(tube_distort(np.array([0.5], dtype=np.float32))[0])
    neg = float(tube_distort(np.array([-0.5], dtype=np.float32))[0])
    assert abs(pos + neg) > 1e-3  # asymmetric halves


def test_noise_gate_attenuates_silence():
    sr = 44100
    x = (np.random.default_rng(0).standard_normal(sr) * 1e-5).astype(np.float32)
    y = noise_gate(x, sr)
    assert float(np.max(np.abs(y))) < 1e-4


def test_boost_is_bounded():
    sr = 44100
    x = (np.random.default_rng(0).standard_normal(sr) * 0.2).astype(np.float32)
    y = boost(x, sr)
    assert np.isfinite(y).all()
    assert float(np.max(np.abs(y))) <= 1.0 + 1e-6


def test_master_limit_respects_ceiling():
    sr = 44100
    x = (np.random.default_rng(0).standard_normal((sr, 2)) * 5).astype(np.float32)
    y = master_limit(x, sr=sr, ceiling=0.9)
    assert float(np.max(np.abs(y))) <= 0.9 + 1e-6


def test_master_glue_bounded():
    sr = 44100
    x = (np.random.default_rng(0).standard_normal((sr, 2)) * 0.3).astype(np.float32)
    y = master_glue(x, sr=sr, ceiling=0.95)
    assert y.shape == (sr, 2)
    assert np.isfinite(y).all()
    assert float(np.max(np.abs(y))) <= 0.96


def test_mix_buses_shapes_and_gains():
    a = np.ones((100, 2), dtype=np.float32)
    b = np.zeros((50, 2), dtype=np.float32)
    out = mix_buses([a, b], [0.5, 0.95], sr=44100, ceiling=0.95)
    assert out.shape == (100, 2)
    assert np.isfinite(out).all()
