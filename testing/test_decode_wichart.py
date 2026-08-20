#!/usr/bin/env python3
"""
Decode a wichart ``enc`` payload (OpenSSL "Salted__" AES-CBC, see
``backend/app/utils/wichart.decrypt``) and pretty-print the JSON inside.

A terminal's line-editing (canonical) mode truncates/garbles very long
pasted lines (often silently, past ~1-4k chars). Do not paste a real enc
value into the interactive prompt — use --file or a pipe instead.

Usage:
  python testing/test_decode_wichart.py --file path/to/enc.txt
  pbpaste | python testing/test_decode_wichart.py     # macOS clipboard
  python testing/test_decode_wichart.py "<short enc>"
"""
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from app.utils.wichart import decrypt  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "enc",
        nargs="?",
        default=None,
        help="the wichart 'enc' payload to decrypt; if omitted, reads stdin/--file",
    )
    parser.add_argument(
        "-f", "--file", metavar="PATH",
        help="read the enc payload from this file (avoids terminal paste limits)",
    )
    return parser.parse_args()


def _clean(enc: str) -> str:
    enc = enc.strip()
    if len(enc) >= 2 and enc[0] == enc[-1] and enc[0] in {"'", '"'}:
        enc = enc[1:-1].strip()
    return enc


def _read_enc(args: argparse.Namespace) -> str:
    if args.enc:
        return _clean(args.enc)
    if args.file:
        return _clean(Path(args.file).read_text())
    if not sys.stdin.isatty():
        # Piped input (e.g. `pbpaste | ...`) bypasses the terminal's line
        # discipline entirely, so there's no length limit here.
        piped = sys.stdin.read()
        if piped.strip():
            return _clean(piped)
    print(
        "error: no enc payload. Paste into a file or the clipboard, then:\n"
        "  python testing/test_decode_wichart.py --file path/to/enc.txt\n"
        "  pbpaste | python testing/test_decode_wichart.py",
        file=sys.stderr,
    )
    raise SystemExit(2)


def main() -> int:
    args = parse_args()
    enc = _read_enc(args)
    raw = decrypt(enc)
    try:
        data = json.loads(raw)
        print(json.dumps(data, ensure_ascii=False, indent=2))
    except json.JSONDecodeError:
        print(raw.decode("utf-8", errors="replace"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
