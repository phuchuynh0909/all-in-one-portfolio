"""Live probe of Qdrant KB search — not collected by pytest.

Run from the repo root:
    python backend/tests/test_qdrant.py

Or from backend/:
    python tests/test_qdrant.py
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_BACKEND = Path(__file__).resolve().parents[1]
_ROOT = _BACKEND.parent
if str(_BACKEND) not in sys.path:
    sys.path.insert(0, str(_BACKEND))

# Repo-root .env, then backend/.env — never print file contents.
load_dotenv(_ROOT / ".env")
load_dotenv(_BACKEND / ".env")
os.environ.setdefault("QDRANT_URL", "http://192.168.1.3:6333")


def main() -> None:
    from app.services.embeddings import api_key, embed_dimensions, model_name, openai_base
    from app.services.tradingagents import kb_search

    print("qdrant_url", kb_search._QDRANT_URL)
    print("collection", kb_search._COLLECTION)
    print("kb_enabled", kb_search.kb_enabled())
    print("min_score_default", kb_search.DEFAULT_MIN_SCORE)
    print("top_k_default", kb_search.DEFAULT_TOP_K)
    print("embed_base", openai_base())
    print("embed_model", model_name())
    print("embed_dims", embed_dimensions())
    print("embed_key_present", bool(api_key()))

    client = kb_search._client()
    try:
        info = client.get_collection(kb_search._COLLECTION)
        count = client.count(kb_search._COLLECTION, exact=False)
        print("collection_status", info.status)
        print("points_count", getattr(info, "points_count", None) or count.count)
        print(
            "vector_size",
            getattr(getattr(info, "config", None), "params", None)
            and info.config.params.vectors,
        )
    except Exception as exc:
        print("collection_error", type(exc).__name__, str(exc)[:300])
        raise SystemExit(1) from exc
    finally:
        client.close()

    queries = [
        "Dầu khí Vietnam sector industry outlook stock",
        "Dầu khí sector industry outlook trends drivers risks Vietnam",
    ]

    def show(label: str, hits: list) -> None:
        print(f"\n=== {label}  hits={len(hits)} ===")
        for i, h in enumerate(hits, 1):
            text = (h.get("text") or "").replace("\n", " ")
            print(
                f"{i}. score={h['score']:.4f}  symbol={h['symbol']!r}  "
                f"page={h.get('page')}  title={h['title'][:80]!r}"
            )
            print(f"   {text[:220]}")

    for q in queries:
        hits_default = kb_search.search(q)
        show(
            f"DEFAULT min_score={kb_search.DEFAULT_MIN_SCORE}  q={q!r}",
            hits_default,
        )
        hits_open = kb_search.search(q, top_k=8, min_score=0.0)
        show(f"OPEN min_score=0 top_k=8  q={q!r}", hits_open)


if __name__ == "__main__":
    main()
