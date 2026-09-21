"""
namamp.py -- Neural Amp Modeler guitar amp + real cabinet IR.

Loads `.nam` captures (including legacy v0.5.0 files, whose config only differs
from the current format by naming: flat `head_size`/`head_bias` vs. a nested
`head` object) and runs them in torch. The captures were trained at 48 kHz, so
the DI is resampled to 48 kHz, processed, then resampled back. A real cabinet
impulse response (see `assets/cab/*.wav`) is convolved on afterwards -- that
combination is what makes the guitar sound like an amp, not a toy.
"""
from __future__ import annotations

from math import gcd
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
from scipy.signal import fftconvolve, resample_poly

from assets import assets_root
from model import Song

NAM_DIR = assets_root() / "nam"
CAB_DIR = assets_root() / "cab"
NAM_RATE = 48000
DEFAULT_CAB = "uk-v30-sm57.wav"

_MODELS: Dict[str, object] = {}
_CABS: Dict[str, np.ndarray] = {}
_DEVICES: Dict[str, "object"] = {}


def device():
    import torch
    return torch.device("cuda" if torch.cuda.is_available() else "cpu")


def available() -> bool:
    return bool(_nam_files()) and _torch_ok()


def _torch_ok() -> bool:
    try:
        import torch  # noqa: F401
        from nam.models import init_from_nam  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _nam_files() -> List[Path]:
    return sorted(NAM_DIR.glob("*.nam")) if NAM_DIR.exists() else []


def amps() -> List[str]:
    return [p.stem for p in _nam_files()]


def cabs() -> List[str]:
    return sorted(p.name for p in CAB_DIR.glob("*.wav")) if CAB_DIR.exists() else []


def _convert_legacy(config: dict) -> dict:
    layers = []
    for lc in config["layers"]:
        lc = dict(lc)
        if "head" not in lc and "head_size" in lc:
            lc["head"] = {"out_channels": lc.pop("head_size"),
                          "kernel_size": 1,
                          "bias": lc.pop("head_bias", False)}
        layers.append(lc)
    return {"layers": layers, "head": config.get("head"),
            "head_scale": config.get("head_scale", 1.0)}


def load(name: Optional[str] = None):
    import json
    import torch
    from nam.models import init_from_nam

    files = _nam_files()
    if not files:
        return None
    path = None
    if name:
        for p in files:
            if p.stem == name:
                path = p
                break
    if path is None:
        path = files[0]
    key = path.stem
    if key in _MODELS:
        return _MODELS[key]
    d = json.loads(path.read_text())
    cfg = {"architecture": d.get("architecture", "WaveNet"),
           "sample_rate": d.get("sample_rate") or NAM_RATE,
           "config": _convert_legacy(d["config"]),
           "weights": d["weights"]}
    model = init_from_nam(cfg)
    dev = device()
    model.to(dev)
    model.eval()
    _MODELS[key] = model
    _DEVICES[key] = dev
    return model


def cab(name: Optional[str] = None) -> Optional[np.ndarray]:
    import soundfile as sf
    path = CAB_DIR / (name or DEFAULT_CAB)
    if not path.exists():
        return None
    if path.name in _CABS:
        return _CABS[path.name]
    ir, sr = sf.read(path, dtype="float32", always_2d=False)
    if ir.ndim > 1:
        ir = ir.mean(axis=1)
    ir = (ir / (np.max(np.abs(ir)) + 1e-9)).astype(np.float32)
    _CABS[path.name] = ir
    return ir


def _to_rate(x: np.ndarray, src: int, dst: int) -> np.ndarray:
    if src == dst:
        return x.astype(np.float32)
    g = gcd(int(src), int(dst))
    return resample_poly(x, dst // g, src // g).astype(np.float32)


def process(di: np.ndarray, rate: int, name: Optional[str] = None,
            cab_name: Optional[str] = None) -> np.ndarray:
    """DI (mono float32) -> amped + cab-filtered guitar (mono float32)."""
    import torch
    model = load(name)
    if model is None:
        return di
    x = di.astype(np.float32)
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    x = x * (0.1 / rms)                       # consistent input level
    x48 = _to_rate(x, rate, NAM_RATE)
    rf = int(getattr(model, "receptive_field", 0))
    step = NAM_RATE * 4
    out = np.zeros(len(x48), dtype=np.float32)
    dev = device()
    with torch.inference_mode():
        for i in range(0, len(x48), step):
            seg = x48[i:i + step]
            if len(seg) < rf:
                seg = np.pad(seg, (0, rf - len(seg)))
            tensor = torch.from_numpy(seg)[None].to(dev)
            y = model(tensor)[0].detach().cpu().numpy().astype(np.float32)
            n = min(len(y), len(out) - i)
            out[i:i + n] = y[:n]
    y = _to_rate(out, NAM_RATE, rate)
    ir = cab(cab_name)
    if ir is not None:
        ir_r = _to_rate(ir, _ir_rate(cab_name), rate)
        wet = fftconvolve(y, ir_r)[:len(y)].astype(np.float32)
        y = 0.35 * y + 1.4 * wet
    peak = float(np.max(np.abs(y)) + 1e-9)
    return (y / peak * 0.9).astype(np.float32)


_IR_RATE: Dict[str, int] = {}


def _ir_rate(name: Optional[str]) -> int:
    import soundfile as sf
    path = CAB_DIR / (name or DEFAULT_CAB)
    if path.name in _IR_RATE:
        return _IR_RATE[path.name]
    sr = int(sf.info(path).samplerate)
    _IR_RATE[path.name] = sr
    return sr
