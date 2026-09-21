from midi import beat_to_seconds, seconds_to_beat


def test_tempo_map_integration():
    points = [(0.0, 120.0), (4.0, 60.0), (8.0, 120.0)]
    assert beat_to_seconds(0.0, points) == 0.0
    assert abs(beat_to_seconds(4.0, points) - 2.0) < 1e-9
    assert abs(beat_to_seconds(8.0, points) - 6.0) < 1e-9
    assert abs(beat_to_seconds(12.0, points) - 8.0) < 1e-9


def test_seconds_to_beat_roundtrip():
    points = [(0.0, 120.0), (4.0, 60.0), (8.0, 90.0)]
    for beat in (0.0, 1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 8.0, 10.0):
        seconds = beat_to_seconds(beat, points)
        assert abs(seconds_to_beat(seconds, points) - beat) < 1e-6
