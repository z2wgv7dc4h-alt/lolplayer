# Riffer Renderer

A small local web app that turns a Songsterr-derived tab corpus into audio and plays
it back in the browser. Pick a song, hit **Render**, and the server loads the song's
canonical `notes.json`, renders a real stem mix (sampled drums + DI guitar through a
NAM amp/cab + FluidSynth for everything else), and streams the result back with a
waveform player.

Single-process Flask + Socket.IO app. The synthesis engine lives in `engine/`.

## What it does

- Scans the bundled tab corpus and lists every song (artist / title).
- Loads each song's flattened `notes.json` event stream (tracks, tunings, tempo map,
  articulation).
- Renders a **stem mix**:
  - **Drums** — multisampled kit from `assets/drums/`, velocity layers, round-robin,
    per-instrument gain/pan, parallel compression + short room.
  - **Guitar** — sampled dry DI from `assets/di/` (nearest sample, repitched) through
    a **noise gate** and a **Tube-Screamer-style boost** (ported from `riff_engine.py`),
    then either a **NAM** capture + real cab IR or a built-in numpy amp/cab. Applies
    **expression**: bends (from `raw/song.json`), palm-mute, dead/ghost/hammer notes
    and staccato.
  - **Bass** — rendered from the soundfont, then run through a bass amp/cab stage.
  - **Everything else** (keys, ...) — FluidSynth via `libfluidsynth`, loaded once per
    process with a soundfont.
- **Gain staging + master glue** — buses are summed at fixed relative gains, then a
  master chain (high-pass + glue compressor + limiter via `pedalboard`, with a numpy
  fallback) limits to -0.5 dBFS (no more independent per-bus peak-normalizing).
- **Preview renders** — limit a render to the first 30/45/60 s to hear a result quickly.
- Uses **CUDA** automatically for the NAM stage when a CUDA-enabled torch is installed.
- Serves audio over HTTP with byte-range support so the browser player can seek.

## Tests

```powershell
pip install -r requirements-dev.txt
.\venv\Scripts\python.exe -m pytest
```

Covers path-traversal guards, corpus discovery/loading, the tempo map, bend rendering,
and render smoke tests. Some tests skip cleanly when no corpus/assets are present.

## Requirements

- Python 3.12+
- A tab corpus (see [docs/CORPUS.md](docs/CORPUS.md))
- Assets: `di/ drums/ nam/ cab/` and tools: `fluidsynth/ soundfonts/` (see below)

Python packages — see [requirements.txt](requirements.txt). Key ones:

```
Flask, Flask-SocketIO, numpy, soundfile, scipy, mido, pedalboard
torch + neural-amp-modeler   # NAM amp stage
```

### CUDA torch (for fast NAM)

The default PyPI `torch` is CPU-only. For GPU NAM on an RTX 50-series / CUDA 13 driver:

```powershell
pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu130
```

`engine/namamp.py` picks `cuda` when `torch.cuda.is_available()`, else `cpu` — no config
needed. On this machine a 52 s guitar part renders in ~11 s on the 5080 (~5x realtime).

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu130   # optional GPU
```

## Running

```powershell
.\venv\Scripts\python.exe app.py
```

Open http://127.0.0.1:5000. The server binds `0.0.0.0:5000` (Flask dev server; local use).

**The first page load takes ~30 s** — Flask binds quickly, but the first request triggers
`torch`/CUDA initialization (the engine-status check), so don't assume it failed if the
page is slow on first open. If it never comes up, another process may already own port
5000 (a previous `python app.py`); see
[docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md).

## Configuration

Paths are anchored to the app file, not the current working directory:

| Constant | Default | Purpose |
| --- | --- | --- |
| `APP_DIR` | dir containing `app.py` | base for everything |
| `ENGINE_DIR` | `APP_DIR/engine` | engine modules, added to `sys.path` |
| `OUT_DIR` | `APP_DIR/out` | rendered WAVs |
| `CORPUS_ROOT` | `RIFFER_CORPUS` env, else `APP_DIR/corpus/songs` | tab corpus |
| `ASSETS_DIR` | `RIFFER_ASSETS` env, else `APP_DIR/assets` | `di/ drums/ nam/ cab/` |
| `TOOLS_DIR` | `RIFFER_TOOLS` env, else `APP_DIR/tools` | `fluidsynth/ soundfonts/` |

Everything is self-contained in this project folder (corpus, assets and tools are
included and gitignored). Set `RIFFER_CORPUS` / `RIFFER_ASSETS` / `RIFFER_TOOLS` to point
elsewhere. The engine reads them via `engine/assets.py`.

## Using the UI

1. Choose a song.
2. Optionally tick **NAM amp (slow)**, keep **Boost** ticked, and pick an amp capture + cab IR.
3. Set **Drive** (boost amount when NAM is on; amp distortion when off) and **Gain** (bus level).
4. Pick a **Length** (full song or a first-N-seconds preview).
5. **Render**. The button disables while the queued job runs; the waveform loads on
   completion. The footer shows which engine stages are available.

## Assets

This project is **self-contained**: the corpus, engine assets and tools live inside the
project folder (and are gitignored, since they're large or licensed per-capture /
per-library). Layout:

```
corpus/songs/                            tab corpus (Artist/slug/notes.json)
assets/
  di/      <Note>_s<string>_<take>.wav   dry DI guitar samples
  drums/   <midi>-<kit><instrument>-<take>.wav
  nam/     *.nam                         amp captures
  cab/     *.wav                         cabinet impulse responses
tools/
  fluidsynth/bin/libfluidsynth-3.dll
  soundfonts/MuseScore_General.sf3
  fetch_tones.py                         TONE3000 downloader
```

Override the roots with `RIFFER_CORPUS` / `RIFFER_ASSETS` / `RIFFER_TOOLS` if needed.

If a stage's assets are missing the renderer degrades gracefully (e.g. no DI -> guitars
fall back to the soundfont; no drums -> no drum bus; nothing at all -> a test tone).

## Getting more tones (TONE3000)

The only two captures shipped here are "noboost". For real djent/tech-death tones, pull
high-gain captures and cab IRs from [TONE3000](https://www.tone3000.com) with your own
account key (create one at `tone3000.com/settings`; the key is a secret — never commit
it):

```powershell
$env:TONE3000_API_KEY = "t3k_cs_..."
.\venv\Scripts\python.exe tools\fetch_tones.py search   --query "6505 high gain" --format nam
.\venv\Scripts\python.exe tools\fetch_tones.py download --query "6505 high gain" --format nam --limit 3
.\venv\Scripts\python.exe tools\fetch_tones.py download --query "v30 sm57"       --format ir  --limit 3
```

Files land in `assets/nam/` and `assets/cab/` and show up in the UI dropdowns on the
next page load. Downloads use the TONE3000 API and are subject to their API terms.

## Limitations

- NAM is CPU-bound unless a CUDA torch is installed; the full song is still serialized
  through one worker thread.
- Guitar is a sampler (nearest DI sample repitched), not a physical model. Bends are
  applied by time-varying resampling; slides/vibrato are not yet rendered.
- Preview trims by seconds up to the first note after the cut; it is not a streaming
  player.
- No Reaper/VST layer or tab/notation export (see `docs/STATUS.md`).

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request flow, engine modules, render pipeline.
- [docs/CORPUS.md](docs/CORPUS.md) — corpus layout and `notes.json` schema.
- [docs/STATUS.md](docs/STATUS.md) — verified-working state with evidence.
- [docs/TROUBLESHOOTING.md](docs/TROUBLESHOOTING.md) — startup, port and asset traps.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — change history.
