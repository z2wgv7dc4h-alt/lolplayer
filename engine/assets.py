"""Asset and tool path resolution for the engine.

Set ``RIFFER_ASSETS`` to the folder holding ``di/ drums/ nam/ cab/`` and
``RIFFER_TOOLS`` to the folder holding ``fluidsynth/`` and ``soundfonts/``.
Defaults resolve next to this repo (``<repo>/assets``, ``<repo>/tools``).
"""
from __future__ import annotations

import os
from pathlib import Path


def _anchor() -> Path:
    return Path(__file__).resolve().parent.parent


def assets_root() -> Path:
    env = os.environ.get("RIFFER_ASSETS")
    return Path(env) if env else _anchor() / "assets"


def tools_root() -> Path:
    env = os.environ.get("RIFFER_TOOLS")
    return Path(env) if env else _anchor() / "tools"
