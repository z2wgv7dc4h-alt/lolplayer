# Architecture

`app.py` is a single module containing the web layer, the corpus loader, the render
pipeline and the background worker. The synthesis/amp engine lives in `engine/` and is
imported into the module namespace.

## Data flow

```
browser  --socket: render_request-->  on_render()  --job-->  render_queue
                                                              |
                                              worker_render() (single thread)
                                                              |
                                       load_song() -> notes.json -> Song
                                                              |
                                       render_song() -> float32 audio
                                                              |
                                       soundfile.write() -> out/<file>.wav
                                                              |
browser  <--socket: render_complete {filename}----------------+
         <--socket: render_error    {error,traceback}--------+
```

The browser then requests `GET /audio/<filename>`, served with HTTP range support.

## Components

### Corpus layer

- `load_songs(force=False)` — returns the cached list of `{slug, artist, title, path}`.
  Discovery globs `CORPUS_ROOT/*/*/notes.json` and skips any path containing `(old)`.
  Results are cached for `_SONGS_TTL` seconds behind a lock to avoid re-reading every
  song file on each page load.
- `_safe_song_dir(artist, slug)` — resolves `CORPUS_ROOT/<artist>/<slug>` and rejects
  any path that escapes the corpus or contains separators / NUL. This is the
  path-traversal guard for both rendering and the file server.
- `load_song(artist, slug)` — parses `notes.json` into `Song`/`Track`/`Event`. Track
  channels skip the drum channel (9); GM programs are clamped (1024 -> 0).

### Timing layer

`notes.json` carries both musical time (`onset_beat`, `duration_beats`, `tempo_bpm`)
and absolute time (`onset_ms`, `duration_ms`). Rendering prefers absolute time when
present.

- `_tempo_map(events)` — builds a cumulative `beat -> seconds` map from the sequence of
  tempo changes in the event stream. Values are `(start_beat, seconds_at_start, bpm)`.
- `_beat_to_sec(beat, tm)` — integrates within the active segment.
- `render_song()` uses `onset_ms/1000` when available, falling back to the tempo map.
  The output buffer is sized from the final event's end time plus a 0.25 s tail, so no
  event is silently dropped.

### Render layer

`render_song(song, amp_name, cab_name)`:

1. Returns a 5 s test sine if the song has no events.
2. Allocates a mono `float32` buffer at 44.1 kHz.
3. For each event, adds a sine at the note's equal-tempered frequency, amplitude scaled
   by velocity (`velocity/127 * 0.3`).
4. Normalizes to 0.9 peak.
5. If `namamp` imported successfully, an `amp_name` was supplied and a capture is
   available, runs `namamp.process(signal, rate, name, cab_name)`.

### Worker layer

`worker_render()` is an infinite loop consuming `render_queue`. Each job is a dict
carrying the requester's Socket.IO session id (`_sid`). Results are emitted **to that
sid only**, so concurrent clients never receive each other's waveforms. Exceptions are
returned to the client as `render_error` with a traceback string.

The worker thread is started only under `if __name__ == '__main__'`.

### Web layer

| Route / event | Description |
| --- | --- |
| `GET /` | HTML page with the song `<select>` and player. Song options are HTML-escaped. |
| `GET /audio/<filename>` | Serves a rendered WAV from `OUT_DIR` with `conditional=True` (range requests). Filename is sanitized and confined to `OUT_DIR`. |
| `socket render_request` | Validates `artist`/`slug` are strings, enqueues a job tagged with `request.sid`, replies `render_started`. |
| `socket render_complete` | `{filename}` — emitted to the requesting client. |
| `socket render_error` | `{error}` — emitted to the requesting client. |

## NAM engine (`engine/namamp_integrated.py`)

- `available()` — true if any `*.nam` exists in `NAM_DIR`.
- `load_nam(name)` — loads and caches an NAM model, patching head layers if needed.
- `process(di, rate, name, cab_name)` — resamples to 48 kHz, normalizes input RMS,
  runs the model in overlapping chunks, resamples back, optionally convolves with a cab
  IR (`fftconvolve`), and normalizes.

The app overrides `namamp.NAM_DIR`/`namamp.CAB_DIR` to absolute paths at import time so
the engine does not depend on the working directory.

## Concurrency and robustness notes

- Single worker thread -> renders are serialized. The queue is unbounded; a long queue
  is possible if many large songs are requested.
- `load_song` and `_safe_song_dir` are safe against hostile artist/slug values.
- The page cache is process-local and expires after `_SONGS_TTL`; editing the corpus is
  picked up automatically within a few seconds.
