import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
for path in (ROOT / "engine", ROOT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

# Importing app detects and exports RIFFER_ASSETS / RIFFER_TOOLS before the
# engine modules (which read those paths at import time) are used.
try:
    import app  # noqa: F401
except Exception:
    pass

