# Architecture

`app.py` is the web layer + corpus loader + worker. The audio engine lives in
`engine/` and is a port of the sibling `riffer` project's synthesis stack.

## Data flow

```
browser --socket: render_request--> on_render() --job--> render_queue
                                                          |
                                        worker_render() (single thread)
                                                          |
                                load_song() -> notes.json -> model.Song
                                                          |
                                render_song() -> stem buses -> mix (Nx2 float32)
                                                          |
                                soundfile.write() -> out/<file>.wav
                                                          |
browser <--socket: render_complete {filename}-------------+
        <--socket: render_error    {error,traceback}-----+
        --GET /audio/<filename> (range)--+
```

## Engine modules (`engine/`)

Ported from `riffer`, with asset/tool paths made configurable via `engine/assets.py`.

| Module | Responsibility |
| --- | --- |
| `model.py` | `Song` / `Track` / `Event` dataclasses (the canonical song model). |
| `midi.py` | `tempo_points`, `beat_to_seconds`, `seconds_to_beat`, and MIDI message building. Requires `mido`. |
| `assets.py` | Resolves `RIFFER_ASSETS` / `RIFFER_TOOLS` (env override, else repo-relative, else auto-detected). |
| `di.py` | Sampled dry-DI guitar: nearest recorded pitch globally (same string only as a tie-breaker), repitched; palm-mute/bend/dead/ghost handling. |
| `drums.py` | Multisampled drums: velocity layers + round-robin, antialiased resampling, per-instrument gain/pan, parallel compression + room IR. |
| `amp.py` | Numpy-only tube-ish amp + synthesized cabinet IR (fallback when NAM is off/unavailable). |
| `namamp.py` | NAM amp captures in torch + real cab IR. Device-aware: runs on CUDA when available. |
| `fastsynth.py` | Persistent `libfluidsynth` via ctypes; renders non-guitar tracks from a soundfont. |
| `synth.py` | Orchestration helpers: `_stem_tracks`, `_subsong`, `_sum_buses`, `find_soundfont`, `find_fluidsynth`, `category_of`. |

### Asset/tool resolution (`assets.py`)

`assets_root()` returns `RIFFER_ASSETS` if set, else `<repo>/assets`. `tools_root()`
returns `RIFFER_TOOLS` if set, else `<repo>/tools`. `app.py` detects a valid root
(`RIFFER_ASSETS` env, then `APP_DIR/assets`, then `~/Desktop/god-tier-metal/assets`,
probed on `di/`; tools probed on `fluidsynth/`) and exports it via `os.environ` **before**
importing the engine modules (which read the paths at import time).

### Timing

`midi.tempo_points(song)` builds the tempo map from per-event `tempo_bpm`; `beat_to_seconds`
integrates across it. `model.Song.duration_beats()` is the max `onset_beat + duration_beats`.
The DI/drum renderers size their buffers from `duration_beats + tail`.

### Rendering (`app.render_song`)

1. Split tracks into drums / guitars / others via `synth._stem_tracks`
   (`category_of`: 24-31 = guitar, 32-39 = bass, 0-7 = keys). `others` is further split
   into `bass` and the rest.
2. **Others** -> `fastsynth.render_array` (skipped if libfluidsynth/soundfont absent).
3. **Bass** -> `fastsynth.render_array`, then `_bass_bus` (bass amp + synthesized bass
   cab, slight width).
4. **Guitars** -> `di.render` (dry DI). If no DI assets, fall back to the soundfont.
   The DI goes through `_guitar_bus`, which runs NAM+cab when `use_nam`, else the numpy
   `amp.amp` with a real cab IR if one exists.
5. **Drums** -> `drums.render` (skipped if no samples).
6. Sum with `synth._sum_buses` (normalizes to 0.95 peak). If no bus produced anything,
   fall back to a 5 s test tone.

`_guitar_bus` adds a small right-channel delay for width, matching the sibling project.

### Expression (`app._attach_expression`, `engine/di.py`)

`load_song` calls `_attach_expression`, which matches `raw/song.json` notes to events by
`(track, measure, string, fret)` and attaches bend curves (`event.bends` as
`(position, tone)` points) plus `staccato`/`accentuated`. `di._render` then applies:
palm-mute (gain + low-pass + shorter hold), dead notes (short low-passed chug instead of
a skipped note), ghost/hammer gain shaping, staccato shortening, and `di._apply_bends`
(time-varying resample read position from the bend contour).

### Preview

The UI's **Length** control sends `preview` seconds. `worker_render` trims the song with
`synth.trim_song(song, seconds)` before rendering.

### NAM device selection (`namamp.py`)

- `device()` returns `cuda` if `torch.cuda.is_available()`, else `cpu`.
- `load()` moves the model to that device once and caches it (`_DEVICES`).
- `process()` resamples to 48 kHz, normalizes input RMS, runs the model in 4 s chunks
  under `torch.inference_mode()`, moving each chunk to the device and the result back,
  then resamples to the render rate and convolves the cab IR.

## Web layer

| Route / event | Description |
| --- | --- |
| `GET /` | HTML page: song `<select>`, NAM/amp/cab/drive/gain controls, engine-status footer. All option text HTML-escaped. |
| `GET /audio/<filename>` | Serves a WAV from `OUT_DIR` with `conditional=True` (range requests). Name must match `[A-Za-z0-9_.-]+`, contain no `..`, and stay inside `OUT_DIR`. |
| `socket render_request` | Validates `artist`/`slug` strings; enqueues a job (with `use_nam`, `amp_name`, `cab_name`, `drive`, `gain`) tagged with `request.sid`; replies `render_started`. |
| `socket render_complete` | `{filename}` to the requesting client. |
| `socket render_error` | `{error}` to the requesting client. |

### Corpus layer

- `load_songs(force)` — cached (`_SONGS_TTL`) scan of `CORPUS_ROOT/*/*/notes.json`,
  skipping `(old)`.
- `_safe_song_dir(artist, slug)` — path-traversal guard (rejects separators/`..`/escape).
- `load_song(artist, slug)` — builds a `model.Song` from `notes.json`; `_attach_mix`
  pulls per-track `volume`/`balance` from `raw/song.json` when present.

The worker emits results to the job's originating `sid`, so concurrent clients don't
receive each other's audio.

## Concurrency

One worker thread -> renders are serialized. The queue is unbounded. The song-list cache
is process-local and expires after 5 s.

## Tests

`tests/` (pytest, configured by `pytest.ini`) covers path-traversal guards and the audio
route, corpus discovery/loading, the tempo map (`engine/midi.py`), bend rendering
(`engine/di.py`), and render smoke tests (stereo, finite, fallbacks). Corpus-dependent
tests skip cleanly when no corpus is present. Run with `pytest`.
