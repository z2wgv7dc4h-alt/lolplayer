import pytest

import app


def test_load_songs_returns_list():
    songs = app.load_songs(force=True)
    assert isinstance(songs, list)
    for s in songs:
        assert set(("slug", "artist", "title", "path")) <= set(s)


def test_every_discovered_song_loads():
    songs = app.load_songs()
    if not songs:
        pytest.skip("no corpus available")
    for s in songs[:25]:
        song = app.load_song(s["artist"], s["slug"])
        assert song is not None, s["slug"]
        assert song.slug == s["slug"]


def test_first_song_shape():
    songs = app.load_songs()
    if not songs:
        pytest.skip("no corpus available")
    s = songs[0]
    song = app.load_song(s["artist"], s["slug"])
    assert len(song.tracks) >= 1
    assert song.events == sorted(song.events, key=lambda e: e.onset_beat)
    for e in song.events:
        assert 0 <= e.velocity <= 127
        assert 0 <= e.pitch <= 127
