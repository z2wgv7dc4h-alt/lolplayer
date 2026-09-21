# Status

Last verified: 2026-09-21, by importing and running `app.py` in the development venv
(Python 3.12.10, Windows, RTX 5080, driver 616.64 / CUDA 13.4 UMD).

> Startup note: Flask binds quickly, but the first page request triggers the
> `torch`/CUDA engine-status check (~30 s). See [TROUBLESHOOTING.md](TROUBLESHOOTING.md)
> if the page doesn't come up — a stale `python app.py` may already own port 5000.

## Verified working

| Area | Evidence |
| --- | --- |
| Corpus discovery | `load_songs()` returned **343** songs from `RIPPER/bulk/songs`. |
| Engine detection | `_engine_status()`: DI on (19 samples), drums on (29 pitches), NAM on (2 captures), synth on (MuseScore soundfont + libfluidsynth). |
| Assets auto-detection | `ASSETS_DIR` = `~/Desktop/god-tier-metal/assets`, `TOOLS_DIR` = `~/Desktop/god-tier-metal/tools`. |
| Track mapping | Channels `[0..8]`, drums -> 9; GM program clamped; lead/rhythm guitar + bass + drums parsed. |
| Full stem render | Guitar + bass + drums song (854 events) rendered to stereo `(2382778, 2)` in 8.0 s, non-silent, peak 0.95. |
| Expression data | Sample song: 6 bend events, 170 palm-mute events detected and applied. |
| Bend rendering | `di._apply_bends` preserves length, finite, and changes the signal vs. flat. |
| Bass amp | Bass-only render `(2382778, 2)`, rms 0.184, finite, peak 0.95. |
| Preview | `trim_song(song, 20s)` -> 271 events / 44 beats; rendered `(997762, 2)` in 6.3 s. |
| Tests | `pytest` -> **17 passed** in 43.3 s. |
| DI pitch selection | Real song: old `_pick` detuned **236/303** events by >=4 semitones (max +/-31). Fixed: **0/303**, max +/-2. |
| Running server | `GET /` -> `200`, 44,168 bytes, 354 options, footer `engine: DI=on drums=on NAM=on synth=on` on `127.0.0.1:5000`. |
| NAM on CUDA | `namamp.device()` = `cuda`; full 52.4 s guitar part rendered in **10.7 s** (~5x realtime) on the 5080. |
| Numpy amp fallback | Same excerpt with `use_nam=False` in 0.5 s; output differs from NAM. |
| End-to-end render | Socket.IO test client produced a 9.5 MB WAV in `out/`. |
| Audio serving | Full `200`; range request `206 bytes 0-99/9531156` (fixed a double-sanitize 404 on long slugs). |
| Path traversal | `load_song('..','..')` and `/audio/..` rejected. |
| Empty / no selection | Index renders 343 song options plus 2 amp and 5 cab options; guarded against an empty list. |

## Environment

- Python 3.12.10 (Windows)
- Flask 3.1.3, Flask-SocketIO 5.6.1, numpy 2.5.3, soundfile 0.14.0, scipy 1.18.1, mido 1.3.3
- torch **2.14.0+cu130**, neural-amp-modeler 0.13.0
- FluidSynth `libfluidsynth-3.dll` + `MuseScore_General.sf3`

## Not yet implemented / partial

| Item | State |
| --- | --- |
| Real-time streaming preview | Renders are offline; preview is a first-N-seconds trim, not a stream. |
| Slide / vibrato from tabs | Parsed onto events; not rendered (bends are). |
| Reaper + VST (Surge, Valhalla, Spitfire) | Not wired; in-process engine only. |
| Tab/notation export (VexFlow/alphaTab) | Not present. |
| Mix automation (auto-duck/EQ) | Not present; per-track volume/balance from `raw/song.json` is applied to the MIDI side only. |

## How to re-verify

```powershell
.\venv\Scripts\python.exe -c "import app; print(len(app.load_songs())); print(app._engine_status())"
.\venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
.\venv\Scripts\python.exe -m pytest
.\venv\Scripts\python.exe app.py   # then open http://127.0.0.1:5000
```

Regenerate this file by actually running the commands above, not from intent.
