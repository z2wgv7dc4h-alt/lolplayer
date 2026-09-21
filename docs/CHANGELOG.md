# Changelog

All notable changes to the Riffer Renderer app are recorded here.

## 2026-09-21

### Added
- `README.md`, `docs/ARCHITECTURE.md`, `docs/CORPUS.md`, `docs/CHANGELOG.md`,
  `docs/STATUS.md`.
- `.gitignore` excluding virtualenvs, `out/`, caches, logs and user-supplied audio
  assets.
- Corpus discovery, per-song loading and the tempo-mapped render pipeline in `app.py`.

### Changed
- Corpus root is now anchored to the app file
  (`APP_DIR.parents[1]/bulk/songs`) instead of the current working directory, and the
  loader filters out `(old)` copies. This matches how the sibling `riffer` tool finds
  songs.
- Songs are loaded from the canonical `notes.json` event stream rather than a
  `tracks[].notes` structure in `raw/song.json` (which does not exist in this corpus).
- Rendering now uses absolute `onset_ms`/`duration_ms` timing when available and falls
  back to a cumulative `beat -> seconds` tempo map. The output buffer is sized from the
  real end of the last event, so no events are dropped at the end of a song.
- Results are emitted only to the requesting Socket.IO client (`to=sid`) instead of
  broadcasting to every connected client.
- `/audio/<filename>` serves byte ranges (`conditional=True`) and confines requests to
  `out/`.
- The NAM engine import is optional; a missing `torch`/`nam` no longer prevents the app
  from starting. Its asset directories are repointed to absolute paths.
- HTML output escapes artist/title/slug values.

### Fixed
- `songs[0]` crash when the corpus was empty.
- The song `<select>` sent a hardcoded first artist for every song.
- Path traversal via `artist`/`slug` and via the `/audio` route.
- Mono sine `np.linspace` endpoint off-by-one in the empty-song fallback.

### Known limitations
- Audio is a placeholder oscillator per note; it is timing/pitch/velocity correct but
  not an instrument model.
- NAM over a full-length song is slow and serialized through one worker thread.
- The Amp Drive slider is not wired to the render.
- No automated tests yet.
