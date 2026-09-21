import numpy as np

import app
import model


def _guitar_song():
    track = model.Track(index=0, name="g", program=30, is_drums=False,
                        channel=0, low_midi=40, tuning=[40] * 6)
    events = [
        model.Event(track=0, pitch=52 + i, onset_beat=float(i),
                    duration_beats=0.5, tempo=120.0, velocity=100)
        for i in range(4)
    ]
    return model.Song(slug="t", title="t", artist="t",
                      tracks=[track], events=events)


def test_render_smoke_is_stereo_and_finite():
    audio, rate = app.render_song(_guitar_song())
    assert rate == 44100
    assert audio.ndim == 2 and audio.shape[1] == 2
    assert np.isfinite(audio).all()
    assert float(np.max(np.abs(audio))) > 0.0


def test_empty_song_falls_back_to_tone():
    song = model.Song(slug="e", title="e", artist="e", tracks=[], events=[])
    audio, rate = app.render_song(song)
    assert audio.ndim == 1
    assert len(audio) == 5 * rate


def test_bass_amp_stays_finite_and_bounded():
    from amp import bass_amp, bass_cabinet_ir
    x = np.random.default_rng(0).standard_normal(44100).astype(np.float32) * 0.1
    y = bass_amp(x, 44100, cab=bass_cabinet_ir(44100))
    assert np.isfinite(y).all()
    assert float(np.max(np.abs(y))) <= 1.0 + 1e-6
