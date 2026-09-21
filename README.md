# Riffer Renderer

A small local web app that turns a Songsterr-derived tab corpus into audio and plays
it back in the browser. Pick a song, hit **Render**, and the server loads the song's
canonical `notes.json`, synthesizes it to a WAV, optionally runs it through a NAM amp
capture + cab IR, and streams the result back with a waveform player.

This is a single-process Flask + Socket.IO app. The generation/synthesis engine lives
in `engine/` and is imported at startup.

## What it does

- Scans a shared, read-only tab corpus and lists every song (artist / title).
- Loads each song's flattened `notes.json` event stream.
- Renders note events to mono 44.1 kHz audio using a tempo map (honours mid-song
  tempo changes) and an absolute-time (`onset_ms`/`duration_ms`) fast path.
- Optionally processes the result through **Neural Amp Modeler** (`engine/namamp_integrated.py`)
  plus a cab IR.
- Serves audio over HTTP with byte-range support so the browser player can seek.

## Requirements

- Python 3.12+
- A tab corpus in the layout described in [docs/CORPUS.md](docs/CORPUS.md)
- Optional: NAM + cab assets for amp/cab processing

Core Python packages (versions from the development environment):

```
Flask==3.1.3
Flask-SocketIO==5.6.1
numpy==2.5.3
soundfile==0.14.0
python-socketio==5.17.0
python-engineio==4.14.0
# optional, only for NAM processing
torch==2.14.0
scipy==1.18.1
```

## Setup

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install flask flask-socketio numpy soundfile
# optional, for amp/cab processing:
pip install torch scipy
```

## Running

```powershell
.\venv\Scripts\python.exe app.py
```

Then open http://127.0.0.1:5000 in a browser.

The server binds `0.0.0.0:5000` (LAN-accessible). It is the Flask development server —
fine for local use, not intended for public exposure.

## Configuration

Paths are anchored to the app file, **not** the current working directory:

| Constant | Value | Purpose |
| --- | --- | --- |
| `APP_DIR` | directory containing `app.py` | base for everything else |
| `ENGINE_DIR` | `APP_DIR/engine` | added to `sys.path` |
| `OUT_DIR` | `APP_DIR/out` | rendered WAVs |
| `CORPUS_ROOT` | `APP_DIR.parents[1]/bulk/songs` | read-only tab corpus |

The NAM engine's asset directories are repointed to `APP_DIR/assets/nam` and
`APP_DIR/assets/cab`, so it works regardless of launch directory.

To use a different corpus, edit `CORPUS_ROOT` in `app.py`.

## Using the UI

1. Choose a song from the dropdown.
2. Click **Render**. The button disables while a queued job is processed.
3. When it completes, the waveform loads automatically; use **Play** to listen.

The **Amp Drive** slider is currently a UI stub — it is not yet wired into the render.

## Assets

`assets/nam/*.nam` captures and cab IRs are **not** committed: they are licensed
per-capture and should be supplied by the user. Drop your own files into
`assets/nam/`, `assets/cab/` and (for the drum kit) `assets/drums/`. The amp capture
requested by the UI defaults to `wavenet_a1_standard`; if absent, the engine falls
back to the first available capture with a matching name, or the first `.nam` found.

## Limitations

- Synthesis is currently a placeholder oscillator (one sine per note event), not a
  guitar/drum instrument synth. It is correct in timing, pitch and velocity but does
  not yet sound like the source.
- NAM processing a full 5-minute song is slow (WaveNet inference over millions of
  samples). Renders are queued and processed one at a time by a single worker thread.
- No tests yet.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — request/response flow, modules, socket API.
- [docs/CORPUS.md](docs/CORPUS.md) — on-disk layout and `notes.json` schema.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — change history.
