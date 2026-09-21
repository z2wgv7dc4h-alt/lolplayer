import app


def test_safe_song_dir_rejects_traversal():
    assert app._safe_song_dir("..", "..") is None
    assert app._safe_song_dir("After The Burial", "../../../etc") is None
    assert app._safe_song_dir("a/b", "c") is None
    assert app._safe_song_dir("a\\b", "c") is None
    assert app._safe_song_dir("", "x") is None
    assert app._safe_song_dir("x", "x\x00y") is None
    assert app._safe_song_dir(None, "x") is None


def test_safe_song_dir_accepts_corpus_path():
    songs = app.load_songs()
    if not songs:
        return
    s = songs[0]
    got = app._safe_song_dir(s["artist"], s["slug"])
    assert got is not None
    assert got.is_relative_to(app.CORPUS_ROOT.resolve())


def test_audio_route_rejects_traversal():
    client = app.app.test_client()
    assert client.get("/audio/..").status_code == 404
    assert client.get("/audio/../../../etc/passwd").status_code == 404
    assert client.get("/audio/does_not_exist.wav").status_code == 404


def test_sanitize_filename_strips_separators():
    clean = app._sanitize_filename("../../evil name!.wav")
    assert "/" not in clean and "\\" not in clean and " " not in clean
