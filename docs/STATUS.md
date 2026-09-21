# Status

Last verified: 2026-09-21, by importing and running `app.py` in the development venv.

## Verified working

| Area | Evidence |
| --- | --- |
| Corpus discovery | `load_songs()` returned **343** songs from `RIPPER/bulk/songs`. |
| Song loading | Sample song loaded 9 tracks / 6796 events from `notes.json`. |
| Track mapping | Channels observed `[0..8]`; drums routed to 9; GM program clamped. |
| Render pipeline | Sample rendered to a 297.72 s buffer; last event end 297.32 s, fully covered. |
| Tempo map | Unit-checked 120 -> 60 -> 120 BPM over beats 0/4/8 -> 0.0 / 2.0 / 6.0 s. |
| End-to-end render | Socket.IO test client produced a 445 KB WAV in `out/`. |
| Audio serving | Full `200`; range request returned `206 bytes 0-99/441044`. |
| Path traversal | `load_song('..','..')` and `/audio/..` both rejected. |
| Empty corpus / no selection | Index renders with 343 options; guarded against empty list. |

## Not yet implemented / partial

| Item | State |
| --- | --- |
| Instrument synthesis | Placeholder sine per note event only. |
| NAM amp/cab | Wired and path-anchored; requires user-supplied assets. Slow on full songs. |
| Amp Drive slider | UI only; not connected to rendering. |
| Palm mute / dead notes | Parsed and carried on events; not applied to audio. |
| Automated tests | None. |
| Persistence / project save | None; renders land in `out/`. |

## Environment

- Python 3.12.10 (Windows)
- Flask 3.1.3, Flask-SocketIO 5.6.1, numpy 2.5.3, soundfile 0.14.0
- Optional: torch 2.14.0, scipy 1.18.1

## How to re-verify

```powershell
.\venv\Scripts\python.exe -c "import app; s=app.load_songs(); print(len(s))"
.\venv\Scripts\python.exe app.py   # then open http://127.0.0.1:5000
```

Regenerate this file by actually running the commands above, not from intent.
