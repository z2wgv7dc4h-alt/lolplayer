from pathlib import Path
from typing import Optional
import numpy as np
from scipy.signal import resample_poly, fftconvolve
import torch
from nam.models import init_from_nam
import json
from math import gcd
import soundfile as sf

NAM_DIR = Path("assets/nam")
CAB_DIR = Path("assets/cab")
NAM_RATE = 48000

_MODELS = {}
_CABS = {}

def available():
    return bool(list(NAM_DIR.glob("*.nam")))

def load_nam(name=None):
    files = sorted(NAM_DIR.glob("*.nam"))
    if not files:
        return None
    path = next((f for f in files if name and name in f.stem), files[0])
    stem = path.stem
    if stem in _MODELS:
        return _MODELS[stem]
    cfg = json.loads(path.read_text())
    if "config" in cfg and cfg["config"].get("layers"):
        for layer in cfg["config"]["layers"]:
            if "head" not in layer and "head_size" in layer:
                layer["head"] = {
                    "out_channels": layer.pop("head_size"),
                    "kernel_size": 1,
                    "bias": layer.pop("head_bias", False)
                }
    model = init_from_nam(cfg)
    model.eval()
    _MODELS[stem] = model
    return model

def _resample(x, src, dst):
    if src == dst:
        return x.astype(np.float32)
    g = gcd(int(src), int(dst))
    return resample_poly(x, dst // g, src // g).astype(np.float32)

def process(di, rate, name=None, cab_name=None):
    model = load_nam(name)
    if model is None:
        return di
    x = di.astype(np.float32)
    rms = float(np.sqrt(np.mean(x ** 2)) + 1e-9)
    x = x * (0.1 / rms)
    x48 = _resample(x, rate, NAM_RATE)
    rf = int(getattr(model, "receptive_field", 8192))
    step = max(NAM_RATE * 2, rf * 2)
    out = np.zeros(len(x48), dtype=np.float32)
    with torch.no_grad():
        for i in range(0, len(x48), step):
            seg = x48[i:i + step]
            if len(seg) < rf:
                seg = np.pad(seg, (0, rf - len(seg)))
            y = model(torch.from_numpy(seg)[None])[0].numpy().astype(np.float32)
            n = min(len(y), len(out) - i)
            out[i:i + n] = y[:n]
    y = _resample(out, NAM_RATE, rate)
    ir = _load_cab(cab_name, rate)
    if ir is not None:
        wet = fftconvolve(y, ir)[:len(y)].astype(np.float32)
        y = 0.35 * y + 1.4 * wet
    peak = float(np.max(np.abs(y)) + 1e-9)
    return (y / peak * 0.9).astype(np.float32)

def _load_cab(name, rate):
    if name is None:
        cabs = sorted(CAB_DIR.glob("*.wav"))
        name = cabs[0].name if cabs else None
    if not name:
        return None
    path = CAB_DIR / name
    if not path.exists():
        return None
    if name in _CABS:
        return _CABS[name]
    ir, sr = sf.read(str(path), dtype='float32', always_2d=False)
    if ir.ndim > 1:
        ir = ir.mean(axis=1)
    if sr != rate:
        ir = _resample(ir, sr, rate)
    ir = (ir / (np.max(np.abs(ir)) + 1e-9)).astype(np.float32)
    _CABS[name] = ir
    return ir
