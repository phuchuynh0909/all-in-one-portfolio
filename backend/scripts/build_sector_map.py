#!/usr/bin/env python3
"""Build a flat symbol -> sector mapping from sieucophieu.vn stock lists.

Usage:
    SIEUCOPHIEU_TOKEN=<bearer_token> python backend/scripts/build_sector_map.py
    python backend/scripts/build_sector_map.py --token <bearer_token>

Fetches every page of the ``stock_lists`` endpoint, drops index-style lists
(e.g. VN30), and writes ``{ "SYMBOL": ["Sector name", ...] }`` sorted by
symbol to ``backend/app/sector_map.json``. A symbol may belong to multiple
sectors (multi-tag).

The bearer token is never hardcoded; pass it via env var or ``--token``.
"""
from __future__ import annotations

import argparse
import json
import os
import urllib.request
from pathlib import Path

API_URL = "https://sieucophieu.vn/api/v1/stock/stock_lists/?page_size=100"

# Lists that are indices/baskets rather than real sectors -> excluded.
EXCLUDED_LISTS = {"VN30"}

OUTPUT_PATH = Path(__file__).resolve().parent.parent / "app" / "sector_map.json"


def fetch_all(token: str) -> list[dict]:
    """Fetch every page of stock lists, following the ``next`` cursor."""
    results: list[dict] = []
    url: str | None = API_URL
    headers = {
        "accept": "*/*",
        "authorization": f"Bearer {token}",
        "content-type": "application/json",
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/151.0.0.0 Safari/537.36"
        ),
        "referer": "https://sieucophieu.vn/bang-dien",
    }
    while url:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=30) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
        results.extend(payload.get("results", []))
        url = payload.get("next")
    return results


def build_map(lists: list[dict]) -> dict[str, list[str]]:
    """Map each symbol to every real sector it belongs to (multi-tag)."""
    mapping: dict[str, list[str]] = {}
    for entry in lists:
        name = entry.get("name")
        if not name or name in EXCLUDED_LISTS:
            continue
        for stock in entry.get("stocks", []):
            symbol = stock.get("symbol")
            if not symbol:
                continue
            sectors = mapping.setdefault(symbol, [])
            if name not in sectors:
                sectors.append(name)
    return {k: mapping[k] for k in sorted(mapping)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--token",
        default=os.environ.get("SIEUCOPHIEU_TOKEN"),
        help="Bearer token (or set SIEUCOPHIEU_TOKEN env var)",
    )
    args = parser.parse_args()
    if not args.token:
        parser.error("token required via --token or SIEUCOPHIEU_TOKEN")

    lists = fetch_all(args.token)
    mapping = build_map(lists)

    OUTPUT_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    sectors = sorted({s for tags in mapping.values() for s in tags})
    multi = sum(1 for tags in mapping.values() if len(tags) > 1)
    print(
        f"Wrote {len(mapping)} symbols across {len(sectors)} sectors "
        f"({multi} in multiple sectors)"
    )
    print(f"Output: {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
