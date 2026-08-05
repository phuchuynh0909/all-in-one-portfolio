"""Prefect RAG pipeline: report PDF -> markdown -> embeddings -> Qdrant.

Given a wichart report id, this flow:
  1. Looks up the report's PDF url / symbol / title (raw_wichart_report).
  2. Downloads and parses the PDF into markdown with **marker** (marker-pdf),
     with pagination on so page boundaries are preserved.
  3. Splits the markdown **per page** (page-level chunking) and embeds each page
     with Qwen3-Embedding-8B — either through an **Ollama** server or a local
     **HuggingFace** model in-process (``RAG_EMBED_BACKEND``).
  4. Upserts the chunks (+ vectors) into Qdrant (re-embedding replaces prior).

Status + the parsed markdown are tracked per report in ClickHouse via
``app.services.report_rag_service`` so the Report page can show which reports
are embedded. Trigger it from the API (background job) or run standalone:

    python tasks/rag_pipeline.py <report_id> [--recreate]

Config (env):
    RAG_PDF_PARSER              "marker" (default) or "llamaparse"
    RAG_LLAMAPARSE_API_KEY      (or LLAMA_CLOUD_API_KEY) — required for llamaparse
    RAG_LLAMAPARSE_LANGUAGE     optional OCR language hint (e.g. "vi")
    QDRANT_URL                  default http://192.168.1.3:6333
    QDRANT_REPORTS_COLLECTION   default wichart_reports
    RAG_EMBED_BACKEND           "ollama" (default) or "huggingface" (local model)
    RAG_EMBED_MODEL             Ollama model, default qwen3-embedding:8b
    RAG_OLLAMA_URL              default OLLAMA_BASE_URL or http://host.docker.internal:11434
    RAG_HF_EMBED_MODEL          HF backend model, default Qwen/Qwen3-Embedding-8B
    RAG_EMBED_BATCH             texts per embed call (default 4)
    RAG_EMBED_RETRIES           retries on transient embed EOF/OOM (default 3)
    RAG_MAX_PAGE_CHARS          page-chunk cap; longer pages are sub-split (default 6000)
    TORCH_DEVICE                forwarded to marker (cpu / cuda / mps)

See ``app/services/embeddings.py`` for the full set of embedding knobs.
Prerequisite (ollama backend): `ollama pull qwen3-embedding:8b` on the Ollama host.
"""
from __future__ import annotations

import gc
import os
import re
import sys
import tempfile
import uuid
from typing import Optional

import requests
from prefect import flow, task
from prefect.logging import get_run_logger
from dotenv import load_dotenv

load_dotenv()

# Make the app package importable when run standalone from tasks/.
CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_ROOT = os.path.dirname(CURRENT_DIR)
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from app.services import embeddings  # noqa: E402
from app.services import report_rag_service as rag  # noqa: E402

QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.1.3:6333")
COLLECTION = os.getenv("QDRANT_REPORTS_COLLECTION", "wichart_reports")

# PDF -> markdown parser: "marker" (local, ML models) or "llamaparse" (cloud API).
# Overridable per job via the API/flow ``parser`` argument.
PDF_PARSER = os.getenv("RAG_PDF_PARSER", "marker").lower()
LLAMAPARSE_API_KEY = os.getenv("RAG_LLAMAPARSE_API_KEY") or os.getenv("LLAMA_CLOUD_API_KEY")
LLAMAPARSE_LANGUAGE = os.getenv("RAG_LLAMAPARSE_LANGUAGE")  # optional OCR language hint, e.g. "vi"
# Page-level chunking: one chunk per page. A page longer than this is sub-split
# so a single embedding call never silently truncates a huge page.
MAX_PAGE_CHARS = int(os.getenv("RAG_MAX_PAGE_CHARS", "6000"))


# ---------------------------------------------------------------------------
# marker converter (heavy: loads ML models once, cached for the process)
# ---------------------------------------------------------------------------

_CONVERTER = None


def _get_converter():
    """Build the marker PdfConverter once and reuse it across jobs.

    marker loads several ML models via ``create_model_dict()``; doing that per
    report would be prohibitively slow, so the converter is a lazy process-wide
    singleton. ``paginate_output=True`` makes marker emit page separators so we
    can chunk by page.
    """
    global _CONVERTER
    if _CONVERTER is None:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
        from marker.config.parser import ConfigParser

        config_parser = ConfigParser(
            {"output_format": "markdown", "paginate_output": True}
        )
        _CONVERTER = PdfConverter(
            config=config_parser.generate_config_dict(),
            artifact_dict=create_model_dict(),
            processor_list=config_parser.get_processors(),
            renderer=config_parser.get_renderer(),
        )
    return _CONVERTER


def _release_converter() -> None:
    """Drop the marker singleton so the embedding model has room to load.

    Marker + Qwen3-Embedding-8B together often OOM: with the ollama backend that
    surfaces as ``EOF`` on an ephemeral ``127.0.0.1:<port>/v1/embeddings`` URL;
    with the huggingface backend the local model allocation itself fails.
    """
    global _CONVERTER
    if _CONVERTER is None:
        return
    _CONVERTER = None
    gc.collect()
    try:
        import torch

        if torch.backends.mps.is_available():
            torch.mps.empty_cache()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:  # noqa: BLE001
        pass


# ---------------------------------------------------------------------------
# Tasks
# ---------------------------------------------------------------------------


@task(retries=1, retry_delay_seconds=5)
def fetch_report_metadata(report_id: int) -> Optional[dict]:
    """Look up symbol/title/pdf_url for a report from raw_wichart_report."""
    from app.services.report_service import _none_if_nan, _query_raw_reports

    df = _query_raw_reports(report_id=report_id)
    if df is None or df.empty:
        return None
    row = df.iloc[0]
    return {
        "symbol": str(_none_if_nan(row.get("mack")) or "").upper(),
        "title": str(_none_if_nan(row.get("tenbaocao")) or ""),
        "pdf_url": str(_none_if_nan(row.get("url")) or ""),
    }


# Canonical page separator used to stitch parser output into one page-delimited
# markdown string; ``_split_pages`` recovers the pages downstream.
_PAGE_JOIN = "\n\n" + "-" * 48 + "\n\n"


def _parse_with_marker(path: str) -> str:
    """marker -> paginated markdown (page separators emitted by marker itself)."""
    from marker.output import text_from_rendered

    rendered = _get_converter()(path)
    text, _, _images = text_from_rendered(rendered)
    return text or ""


def _parse_with_llamaparse(path: str) -> str:
    """LlamaParse (cloud) -> markdown. Each returned Document is one page; they
    are joined with the canonical separator so page-level chunking is preserved.
    """
    if not LLAMAPARSE_API_KEY:
        raise RuntimeError(
            "RAG_PDF_PARSER=llamaparse requires an API key: set LLAMA_CLOUD_API_KEY "
            "(or RAG_LLAMAPARSE_API_KEY). Get one at https://cloud.llamaindex.ai."
        )
    from llama_parse import LlamaParse

    kwargs = {"api_key": LLAMAPARSE_API_KEY, "result_type": "markdown"}
    if LLAMAPARSE_LANGUAGE:
        kwargs["language"] = LLAMAPARSE_LANGUAGE
    parser = LlamaParse(**kwargs)
    docs = parser.load_data(path)
    pages = [(getattr(d, "text", "") or "").strip() for d in docs]
    pages = [p for p in pages if p]
    return _PAGE_JOIN.join(pages)


@task(retries=1, retry_delay_seconds=10)
def parse_pdf(pdf_url: str, parser: Optional[str] = None) -> str:
    """Download the PDF and convert it to page-delimited markdown.

    ``parser`` ("marker" | "llamaparse") overrides the ``RAG_PDF_PARSER`` default.
    Only the selected parser's library is imported, so a deployment can install
    just one.
    """
    which = (parser or PDF_PARSER or "marker").lower()
    if which not in ("marker", "llamaparse"):
        raise ValueError(
            f"Unknown parser '{which}'. Use 'marker' or 'llamaparse'."
        )

    resp = requests.get(pdf_url, timeout=120)
    resp.raise_for_status()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(resp.content)
            tmp_path = fh.name
        if which == "llamaparse":
            return _parse_with_llamaparse(tmp_path)
        return _parse_with_marker(tmp_path)
    finally:
        if tmp_path and os.path.exists(tmp_path):
            os.unlink(tmp_path)


# marker's paginated markdown separates pages with a run of dashes, optionally
# prefixed by the page index in braces (e.g. ``{0}------------…``). Match either
# form so page splitting is robust across marker versions.
_PAGE_SEPARATOR = re.compile(r"\n\s*(?:\{\d+\})?\s*-{20,}\s*\n")


def _split_pages(markdown: str) -> list[str]:
    """Split marker's paginated markdown into one string per page."""
    text = (markdown or "").strip()
    if not text:
        return []
    pages = [p.strip() for p in _PAGE_SEPARATOR.split(text) if p.strip()]
    # If pagination markers were absent, treat the whole document as one page.
    return pages or [text]


def _build_page_chunks(markdown: str, max_chars: int = MAX_PAGE_CHARS) -> list[dict]:
    """Page-level chunks: one chunk per page; over-long pages are sub-split.

    Each chunk keeps its ``page`` number so retrieval can cite the source page.
    """
    chunks: list[dict] = []
    for page_no, page_text in enumerate(_split_pages(markdown)):
        if len(page_text) <= max_chars:
            chunks.append({"page": page_no, "text": page_text})
            continue
        for i in range(0, len(page_text), max_chars):
            chunks.append({"page": page_no, "text": page_text[i : i + max_chars]})
    return chunks


@task
def chunk_markdown(markdown: str) -> list[dict]:
    return _build_page_chunks(markdown)


@task(retries=1, retry_delay_seconds=10)
def embed_and_upsert(
    report_id: int,
    symbol: str,
    title: str,
    pdf_url: str,
    chunks: list[dict],
) -> int:
    """Embed page chunks with the configured backend and upsert them into Qdrant.

    Existing points for this report are deleted first so re-embedding replaces
    rather than duplicates. The collection is created on first use with the
    vector size reported by the embedding model. Returns the number of chunks.
    """
    from qdrant_client import QdrantClient, models

    texts = [c["text"] for c in chunks]
    vectors = embeddings.embed_documents(texts)
    if not vectors:
        return 0

    dim = len(vectors[0])
    client = QdrantClient(url=QDRANT_URL, timeout=300)

    if not client.collection_exists(COLLECTION):
        client.create_collection(
            collection_name=COLLECTION,
            vectors_config=models.VectorParams(
                size=dim, distance=models.Distance.COSINE
            ),
        )

    # Drop any prior chunks for this report (so re-embedding replaces).
    try:
        client.delete(
            collection_name=COLLECTION,
            points_selector=models.Filter(
                must=[
                    models.FieldCondition(
                        key="report_id", match=models.MatchValue(value=int(report_id))
                    )
                ]
            ),
        )
    except Exception:  # noqa: BLE001
        pass

    points = [
        models.PointStruct(
            id=uuid.uuid4().hex,
            vector=vector,
            payload={
                "report_id": int(report_id),
                "symbol": symbol,
                "title": title,
                "pdf_url": pdf_url,
                "page": chunk["page"],
                "chunk_index": idx,
                "text": chunk["text"],
            },
        )
        for idx, (chunk, vector) in enumerate(zip(chunks, vectors))
    ]
    client.upsert(collection_name=COLLECTION, points=points)
    return len(points)


# ---------------------------------------------------------------------------
# Flow
# ---------------------------------------------------------------------------


@flow(name="report-rag-pipeline")
def rag_pipeline_flow(
    report_id: int, recreate: bool = False, parser: Optional[str] = None
) -> dict:
    """Run the full RAG pipeline for one report, tracking status in ClickHouse.

    ``parser`` ("marker" | "llamaparse") overrides the RAG_PDF_PARSER default.
    ``recreate`` currently just forces a re-run; chunks are always replaced.
    """
    logger = get_run_logger()
    report_id = int(report_id)
    # Log where THIS process writes status — compare with the API's
    # /report/rag/health to catch a worker-vs-API ClickHouse mismatch.
    logger.info("report_rag status store (worker): %s", rag.endpoint())

    try:
        meta = fetch_report_metadata(report_id)
        if not meta or not meta.get("pdf_url"):
            rag.set_status(report_id, rag.FAILED, error="report or PDF url not found")
            return {"report_id": report_id, "status": rag.FAILED}

        logger.info(
            "report %s meta saved: symbol=%r title=%r pdf_url=%r",
            report_id,
            meta["symbol"],
            (meta["title"] or "")[:80],
            meta["pdf_url"],
        )

        which_parser = (parser or PDF_PARSER or "marker").lower()
        logger.info(
            "Parsing PDF (%s) for report %s: %s", which_parser, report_id, meta["pdf_url"]
        )
        markdown = parse_pdf(meta["pdf_url"], parser)
        # Free marker's ML models before the embedding model is loaded.
        if which_parser == "marker":
            _release_converter()
            logger.info("Released marker models before embedding")
        rag.update(report_id, markdown=markdown, status=rag.PARSED)

        chunks = chunk_markdown(markdown)
        if not chunks:
            rag.set_status(report_id, rag.FAILED, error="no text extracted from PDF")
            return {"report_id": report_id, "status": rag.FAILED}

        rag.set_status(report_id, rag.EMBEDDING)
        logger.info(
            "Embedding %d page-chunks for report %s -> %s  (%s batch=%d)",
            len(chunks),
            report_id,
            COLLECTION,
            embeddings.describe(),
            embeddings.batch_size(),
        )
        n_chunks = embed_and_upsert(
            report_id, meta["symbol"], meta["title"], meta["pdf_url"], chunks
        )

        rag.set_status(
            report_id,
            rag.EMBEDDED,
            chunk_count=n_chunks,
            collection=COLLECTION,
            error="",
        )
        logger.info("Report %s embedded: %d chunks in '%s'", report_id, n_chunks, COLLECTION)
        return {"report_id": report_id, "status": rag.EMBEDDED, "chunks": n_chunks}

    except Exception as exc:  # noqa: BLE001
        logger.exception("RAG pipeline failed for report %s", report_id)
        rag.set_status(report_id, rag.FAILED, error=str(exc)[:500])
        raise


if __name__ == "__main__":
    import argparse
    from pathlib import Path

    argp = argparse.ArgumentParser(description="Run the report RAG pipeline")
    argp.add_argument(
        "report_id", type=int, nargs="?", help="wichart report id (omit with --deploy)"
    )
    argp.add_argument("--recreate", action="store_true", help="force re-embedding")
    argp.add_argument(
        "--parser", choices=["marker", "llamaparse"], help="override RAG_PDF_PARSER"
    )
    argp.add_argument(
        "--deploy", action="store_true", help="Deploy as a Prefect deployment"
    )
    ns = argp.parse_args()

    if ns.deploy:
        # On-demand deployment (triggered per report_id via run_deployment), so
        # no cron — unlike the scheduled sync flows.
        rag_pipeline_flow.from_source(
            source=str(Path(__file__).parent),
            entrypoint="rag_pipeline.py:rag_pipeline_flow",
        ).deploy(
            name="report-rag-pipeline",
            work_pool_name="my-worker",
        )
    else:
        if ns.report_id is None:
            argp.error("report_id is required unless --deploy")
        result = rag_pipeline_flow(ns.report_id, recreate=ns.recreate, parser=ns.parser)
        print(result)
