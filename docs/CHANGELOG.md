# Changelog

All notable changes to the Riffer Renderer app are recorded here.

## 2026-09-21 (tone: boost, gate, mix staging)

### Added
- `engine/tone.py`: `tube_distort` (asymmetric tube waveshaper) and `noise_gate`
  ported from `ww-forge-prior-attempt/engine/riff_engine.py` (itself from rust-beats,
  MIT); plus a Tube-Screamer-style `boost` (low cut + mid hump + asymmetric clip +
  tone), `master_limit`, and `mix_buses`.
- `namamp.process(..., drive=dB)`: extra NAM input gain.
- UI: **Boost (overdrive)** toggle (default on). `Drive` now drives the boost when NAM
  is on, and the numpy amp otherwise.

### Changed
- `_guitar_bus` runs the DI through the noise gate + optional boost before the amp.
- `render_song` sums buses at fixed relative gains (drums 0.95 / guitar 0.85 / bass 1.0
  / other 0.6) and applies a master limiter instead of independently peak-normalizing
  each bus via `synth._sum_buses`.

### Fixed
- The "noboost" captures + no boost stage made guitar thin; there is now a real
  overdrive stage in front, and the bass is no longer buried under independently
  normalized buses.

## 2026-09-21 (DI pitch-selection fix)

### Fixed
- `di._pick` preferred the same string number outright, so any tab whose string
  numbering didn't match the DI library's tuning grabbed a wildly wrong sample. On a
  real song **78% of guitar events were detuned by >=4 semitones, up to -23/+31**, which
  is why guitar (especially through NAM) sounded like garbage. Selection now takes the
  globally nearest recorded pitch and uses string only as a tie-breaker; max detune is
  now +/-2 semitones (0 events >=4). Regression tests added in `tests/test_di.py`.

### Changed
- README/TROUBLESHOOTING/STATUS: corrected the startup note — Flask binds quickly, it is
  the **first page request** (engine-status check) that initializes torch/CUDA and takes
  ~30 s.

## 2026-09-21 (docs)

### Added
- `docs/TROUBLESHOOTING.md`: stale-process/port-5000 trap, ~30 s startup, engine-stage
  footer meanings, asset overrides, CUDA torch install, render serialization.

### Changed
- README "Running" documents the startup delay and port-conflict check; docs index links
  TROUBLESHOOTING.
- STATUS records the live server verification (200, 44,168 bytes, 354 options, all engine
  stages on).

## 2026-09-21 (expression, bass, preview, tests)

### Added
- Expression rendering: `_attach_expression` pulls bend curves + staccato/accent from
  `raw/song.json`; `di.py` applies bends (`_apply_bends`, time-varying resampling),
  palm-mute (low-pass + shorter hold), dead notes (short low-passed chug), ghost/hammer
  gain shaping and staccato.
- Bass amp stage: `amp.bass_amp` + `amp.bass_cabinet_ir`, wired via `_bass_bus`; bass is
  split out of the "others" bus and run through it.
- Preview renders: a UI **Length** control trims the song to the first 30/45/60 s
  (`synth.trim_song`).
- `tests/` pytest suite (15 tests): security/traversal, corpus load, tempo map, bend
  rendering, render smoke tests. `pytest.ini`, `requirements-dev.txt`.

### Changed
- `render_song` now splits non-guitar/non-drum tracks into bass and other buses.
- UI gains a Length selector; README/ARCHITECTURE/STATUS updated.

## 2026-09-21 (engine integration)

### Added
- Full stem synthesis engine ported into `engine/`: `model.py`, `midi.py`, `di.py`,
  `drums.py`, `amp.py`, `namamp.py`, `fastsynth.py`, `synth.py`, plus `assets.py`.
- Real drum rendering (multisampled, velocity layers, round-robin, bus treatment).
- Sampled dry-DI guitar -> NAM amp + real cab IR (or numpy amp fallback).
- FluidSynth (`libfluidsynth` via ctypes) rendering of non-guitar tracks.
- Configurable asset/tool roots via `RIFFER_ASSETS` / `RIFFER_TOOLS` with auto-detection
  of `~/Desktop/god-tier-metal/{assets,tools}`.
- CUDA support in the NAM stage: torch model and chunks run on the GPU when available.
- UI controls for NAM toggle, amp capture, cab IR, drive and gain; engine-status footer.
- `requirements.txt`.
- `mido` dependency (tempo map / message building).

### Changed
- `render_song` now produces a stereo stem mix instead of a per-event sine oscillator.
- `app.py` uses the engine's `model.Song`/`Track`/`Event` instead of local dataclasses.
- `engine/namamp_integrated.py` removed; superseded by the ported `engine/namamp.py`.
- Torch upgraded from `2.14.0+cpu` to `2.14.0+cu130` for GPU NAM.

### Fixed
- `/audio/<filename>` returned 404 for long slugs because the name was sanitized and
  truncated twice (once when written, again when served). The route now validates the
  literal name instead of re-truncating it.

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
