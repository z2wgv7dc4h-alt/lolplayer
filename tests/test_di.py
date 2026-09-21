import pytest

from di import _pick, index


def _require():
    if not index():
        pytest.skip("no DI samples available")


def test_repitch_distance_is_bounded_within_sample_range():
    _require()
    pitches = [m for m, _s, _p in index()]
    lo, hi = min(pitches), max(pitches)
    for pitch in range(lo, hi + 1):
        key, _path = _pick(pitch, 3)
        assert abs(pitch - key) <= 3, (pitch, key)


def test_low_note_on_high_string_does_not_octave_jump():
    _require()
    # Regression: preferring the same string picked B3 (59) for C2 (36) -> -23.
    key, _path = _pick(36, 5)
    assert abs(36 - key) <= 3
