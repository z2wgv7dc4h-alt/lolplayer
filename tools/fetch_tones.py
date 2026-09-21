"""fetch_tones.py -- download NAM captures / IRs from TONE3000.

Uses YOUR TONE3000 account API key (create one at
https://www.tone3000.com/settings). The key is a secret: pass it via the
TONE3000_API_KEY environment variable or --key, never commit it.

Usage:
    python tools/fetch_tones.py search --query "5150 high gain" --format nam
    python tools/fetch_tones.py download --query "5150 high gain" --format nam --limit 3
    python tools/fetch_tones.py download --tone-id 84864 --limit 5

Downloads land in assets/nam (format=nam) or assets/cab (format=ir).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.parse
import urllib.request
from pathlib import Path

API = "https://www.tone3000.com/api/v1"
ROOT = Path(__file__).resolve().parents[1]


def _key(args) -> str:
    key = getattr(args, "key", None) or os.environ.get("TONE3000_API_KEY")
    if not key:
        sys.exit("No API key. Set TONE3000_API_KEY or pass --key (see tone3000.com/settings).")
    return key


def _get(url: str, key: str) -> dict:
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read().decode("utf-8"))


def search(key: str, query: str, fmt: str | None, gears: str | None,
           page_size: int, sort: str) -> list:
    params = {"query": query or "", "page_size": str(max(1, min(25, page_size))),
              "sort": sort}
    if fmt:
        params["format"] = fmt
    if gears:
        params["gears"] = gears
    url = f"{API}/tones/search?" + urllib.parse.urlencode(params)
    data = _get(url, key)
    return data.get("data", data if isinstance(data, list) else [])


def list_models(key: str, tone_id: int, architecture: str | None = None) -> list:
    params = {"tone_id": str(tone_id), "page_size": "300"}
    if architecture:
        params["architecture"] = architecture
    url = f"{API}/models?" + urllib.parse.urlencode(params)
    data = _get(url, key)
    return data.get("data", [])


def _assets_root() -> Path:
    """RIFFER_ASSETS override, else this project's own assets/ folder."""
    env = os.environ.get("RIFFER_ASSETS")
    if env:
        return Path(env)
    return ROOT / "assets"


def _dest_dir(fmt: str) -> Path:
    return _assets_root() / ("cab" if fmt == "ir" else "nam")


def _download(url: str, key: str, dest: Path) -> None:
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {key}"})
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(req, timeout=300) as resp, open(dest, "wb") as fh:
        while True:
            chunk = resp.read(1 << 16)
            if not chunk:
                break
            fh.write(chunk)


def _model_filename(model: dict, fmt: str) -> str:
    for field in ("name", "title", "filename"):
        name = model.get(field)
        if isinstance(name, str) and name.strip():
            base = "".join(c if c.isalnum() or c in "._- " else "_" for c in name).strip()
            break
    else:
        base = f"tone{model.get('tone_id', '')}_{model.get('id', '')}"
    ext = ".wav" if fmt == "ir" else ".nam"
    return (base if base.lower().endswith(ext) else base + ext)


def cmd_search(args) -> int:
    key = _key(args)
    tones = search(key, args.query, args.format, args.gears, args.page_size, args.sort)
    for t in tones:
        print("%-8s %-55s %s" % (t.get("id"), (t.get("title") or "")[:55],
                                 t.get("format") or ""))
    print(f"\n{len(tones)} tones")
    return 0


def cmd_download(args) -> int:
    key = _key(args)
    tones = []
    if args.tone_id:
        tones = [{"id": args.tone_id, "title": f"tone {args.tone_id}",
                  "format": args.format or "nam"}]
    else:
        tones = search(key, args.query, args.format, args.gears, args.page_size, args.sort)

    got = 0
    for tone in tones:
        if got >= args.limit:
            break
        tid = tone.get("id")
        fmt = tone.get("format") or args.format or "nam"
        try:
            models = list_models(key, tid, args.architecture)
        except Exception as exc:  # noqa: BLE001
            print(f"  ! tone {tid}: {exc}")
            continue
        for model in models:
            if got >= args.limit:
                break
            url = model.get("model_url")
            if not url:
                continue
            dest = _dest_dir(fmt) / _model_filename(model, fmt)
            try:
                _download(url, key, dest)
                got += 1
                print(f"  + {dest}  ({tone.get('title', tid)})")
            except Exception as exc:  # noqa: BLE001
                print(f"  ! {tone.get('title', tid)}: {exc}")
    print(f"\n{got} files downloaded")
    return 0 if got else 1


def main() -> int:
    p = argparse.ArgumentParser(description="Fetch NAM/IR files from TONE3000")
    p.add_argument("--key", help="TONE3000 secret API key (else TONE3000_API_KEY)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("--query", default="")
        sp.add_argument("--format", choices=["nam", "ir"], default="nam")
        sp.add_argument("--gears", default="amp-cab")
        sp.add_argument("--page-size", type=int, default=25)
        sp.add_argument("--sort", default="trending")
        sp.add_argument("--architecture", choices=["1", "2", "custom"], default=None)

    s = sub.add_parser("search")
    common(s)
    s.set_defaults(func=cmd_search)

    d = sub.add_parser("download")
    common(d)
    d.add_argument("--tone-id", type=int, default=None)
    d.add_argument("--limit", type=int, default=5)
    d.set_defaults(func=cmd_download)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
