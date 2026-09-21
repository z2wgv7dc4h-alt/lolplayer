# Troubleshooting

Traps that actually bit us. Read this before assuming a code bug.

## "Nothing launched" / the page is the wrong one

Symptom: you start the server, but the browser shows an old or minimal page, or a
render does nothing.

Cause: a **stale `python app.py`** process is already listening on port 5000, so the new
server fails to bind and the old one keeps serving. On a dev box there are often several,
including ones started with the *system* Python rather than the venv.

Find them and kill them:

```powershell
Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'app\.py' } |
    Select-Object ProcessId, CreationDate, CommandLine
Stop-Process -Id <pid> -Force
```

Or just free the port:

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force }
```

The venv `python.exe` can appear as two process ids for one launch; kill both if needed.

## The page takes ~30 seconds to appear

Not a hang. Flask binds quickly, but the **first page request** triggers `torch`/CUDA
initialization (the engine-status check on the index page), which can take ~30 s. Wait,
then retry. Verify the server is actually listening:

```powershell
Get-NetTCPConnection -LocalPort 5000 -State Listen
Invoke-WebRequest http://127.0.0.1:5000/ -UseBasicParsing | Select-Object StatusCode
```

A correct page is ~40 KB and contains `NAM amp` and `engine: DI=... drums=...`.

## A render is silent or thin, and the footer says a stage is "off"

The engine footer (`engine: DI=on drums=on NAM=on synth=on`) reflects what was found at
startup:

- `DI=off` — no `assets/di/*.wav`; guitars fall back to the soundfont.
- `drums=off` — no `assets/drums/*.wav`; no drum bus.
- `NAM=off` — no `assets/nam/*.nam`.
- `synth=off` — `libfluidsynth-3.dll` or a soundfont is missing.

Check where the app looked:

```powershell
.\venv\Scripts\python.exe -c "import app; print(app.ASSETS_DIR); print(app.TOOLS_DIR); print(app._engine_status())"
```

Override with `RIFFER_ASSETS` / `RIFFER_TOOLS`, or place assets in `assets/` / `tools/`.

## NAM is slow

CPU `torch` will make NAM renders extremely slow. Install the CUDA build:

```powershell
pip install --upgrade torch --index-url https://download.pytorch.org/whl/cu130
.\venv\Scripts\python.exe -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

`cu130` is the right channel for RTX 50-series on a CUDA 13 driver; `cu126` does not
support Blackwell.

## Renders pile up / feel serialized

By design: one worker thread consumes an unbounded queue. Large songs (and NAM) are
processed one at a time. Use the **Length** control to render a preview while iterating.
