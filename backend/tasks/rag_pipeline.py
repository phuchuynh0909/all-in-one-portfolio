"""Prefect RAG pipeline: report PDF -> markdown -> summary -> embeddings -> Qdrant.

Given a wichart report id, this flow:
  1. Looks up the report's PDF url / symbol / title (raw_wichart_report) and
     makes sure the report has a ``wichart_reports`` detail row, creating it from
     the feed when the sync has not reached this report yet.
  2. Downloads and parses the PDF into **page-delimited** markdown with one of
     four parsers (``RAG_PDF_PARSER``): marker, llamaparse, docling or
     pymupdf4llm. Every parser emits pages joined by the same separator, so the
     rest of the flow is parser-agnostic.
  3. **Condenses** that markdown into a sectioned digest with an LLM over the
     OpenAI-compatible gateway (``app.services.llm``): one ``##`` section per
     topic, boilerplate dropped, figures preserved verbatim, forecasts and
     opinion attributed rather than restated as fact, and a ``_Source: p. N_``
     reference under each heading. The digest is saved to
     ``wichart_reports.llm_summary`` (overwriting any prior value), so the Report
     detail page and the trading agents read the same text that was indexed.
  4. Chunks that digest **by heading** — one chunk per section, each carrying its
     heading path so it reads standalone and its cited page so retrieval can
     still point at the PDF — and embeds each with Qwen3-Embedding-8B.
  5. Upserts the chunks (+ vectors) into Qdrant (re-embedding replaces prior).

Chunking has three strategies, all chosen by :func:`pick_strategy`: content under
``RAG_SINGLE_CHUNK_CHARS`` is indexed **whole** as one point, a digest is split
**by heading**, and raw markdown is split **by page**. Step 3 is best-effort — if
summarization is disabled, unreachable, or yields a digest that cannot be split,
the flow falls back to the raw markdown. The strategy used is logged and recorded
on every point as ``chunk_strategy``, so a fallback is never silently mistaken
for a summarized index.

Status + the parsed markdown are tracked per report in MySQL via
``app.services.report_rag_service`` so the Report page can show which reports
are embedded. Trigger it from the API (background job) or run standalone:

    python tasks/rag_pipeline.py <report_id> [--recreate] [--no-summarize]

Config (env):
    RAG_PDF_PARSER              "marker" (default), "llamaparse", "docling" or
                                "pymupdf4llm"
    RAG_LLAMAPARSE_API_KEY      (or LLAMA_CLOUD_API_KEY) — required for llamaparse
    RAG_LLAMAPARSE_LANGUAGE     optional OCR language hint (e.g. "vi")
    QDRANT_URL                  default http://192.168.1.3:6333
    QDRANT_REPORTS_COLLECTION   default wichart_reports
    RAG_SUMMARY                 "1" (default) / "0" to skip the digest step and
                                chunk raw pages instead
    RAG_SUMMARY_INPUT_CHARS     chars of markdown per LLM call; longer reports are
                                condensed in page-aligned batches (default 120000)
    RAG_SUMMARY_MAX_SECTION_CHARS  heading-chunk cap; longer sections are
                                sub-split at paragraph boundaries (default 4000)
    RAG_SUMMARY_MIN_SECTION_CHARS  subsections shorter than this fold into their
                                parent chunk (default 200)
    RAG_SINGLE_CHUNK_CHARS      content shorter than this is indexed whole, as one
                                point (default 50000; 0 disables the shortcut)
    RAG_MAX_PAGE_CHARS          page-chunk cap; longer pages are sub-split (default 20000)

Embedding and summarization both go through an OpenAI-compatible API; their knobs
(``RAG_OPENAI_*`` / ``RAG_EMBED_*`` and ``RAG_LLM_*``) are documented in
``app/services/embeddings.py`` and ``app/services/llm.py``, which are the source
of truth rather than this list.
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
from app.services import llm  # noqa: E402
from app.services import report_rag_service as rag  # noqa: E402

QDRANT_URL = os.getenv("QDRANT_URL", "http://192.168.1.3:6333")
COLLECTION = os.getenv("QDRANT_REPORTS_COLLECTION", "wichart_reports")

# PDF -> markdown parser: "marker" (local, ML models) or "llamaparse" (cloud API).
# Overridable per job via the API/flow ``parser`` argument.
PDF_PARSER = os.getenv("RAG_PDF_PARSER", "marker").lower()
LLAMAPARSE_API_KEY = os.getenv("RAG_LLAMAPARSE_API_KEY") or os.getenv("LLAMA_CLOUD_API_KEY")
LLAMAPARSE_LANGUAGE = os.getenv("RAG_LLAMAPARSE_LANGUAGE")  # optional OCR language hint, e.g. "vi"
# Page-level chunking: one chunk per page. A page longer than this is sub-split
# so a single embedding call never silently truncates a huge page. Used for the
# raw-markdown fallback when the summary step is off or unavailable.
MAX_PAGE_CHARS = int(os.getenv("RAG_MAX_PAGE_CHARS", "6000"))

# Summarization step (PDF markdown -> sectioned digest -> heading chunks).
SUMMARY_ENABLED = os.getenv("RAG_SUMMARY", "1").strip().lower() not in (
    "0", "false", "no", "off",
)
# Markdown per LLM call. The gateway's models carry a very large context, so most
# reports condense in a single pass; anything longer is split on page boundaries.
SUMMARY_INPUT_CHARS = int(os.getenv("RAG_SUMMARY_INPUT_CHARS", "120000"))
# Heading-chunk sizing.
MAX_SECTION_CHARS = int(os.getenv("RAG_SUMMARY_MAX_SECTION_CHARS", "4000"))
MIN_SECTION_CHARS = int(os.getenv("RAG_SUMMARY_MIN_SECTION_CHARS", "200"))

# Content shorter than this is indexed whole rather than split — a short digest is
# already one coherent topic. Two ceilings bound how far this can be raised: the
# embedder's ~32k-token context (longer input is truncated *silently*), and
# ``kb_search.format_hits``, which renders only ~1500 chars per hit, so the tail
# of a whole-document chunk never reaches an analyst prompt.
SINGLE_CHUNK_CHARS = int(os.getenv("RAG_SINGLE_CHUNK_CHARS", "50000"))
_EMBED_SAFE_CHARS = 60000  # beyond this a single chunk risks that silent truncation

# Chunk-strategy provenance, stored on every Qdrant point (see module docstring).
STRATEGY_HEADING = "summary_heading"
STRATEGY_PAGE = "page"
STRATEGY_SINGLE = "single"


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
    """Drop the marker singleton once parsing is done, freeing its ML models.

    Embedding is a remote API call now, so this no longer prevents an OOM against
    a co-resident embedding model — it is plain memory hygiene for a long-lived
    worker that parses many reports.
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


def _log():
    """Prefect run logger inside a flow/task run, plain logging outside one.

    Lets the tasks below be called directly (re-embed scripts, tests) without
    tripping over Prefect's missing-run-context error.
    """
    try:
        return get_run_logger()
    except Exception:  # noqa: BLE001 — no Prefect context
        import logging

        return logging.getLogger(__name__)


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


@task(retries=1, retry_delay_seconds=10)
def ensure_detail_row(report_id: int) -> bool:
    """Create the report's ``wichart_reports`` row if it does not exist yet.

    The detail table is only otherwise filled by ``POST /report/sync`` (newest N
    reports), so a pipeline run on an older report used to have nowhere to write
    its summary. Seeding here rather than at summary-save time means the row
    exists even when the digest step is off (``RAG_SUMMARY=0``) or fails.
    """
    from app.stores.raw_wichart_report import WichartReportStore

    return WichartReportStore().ensure_detail(int(report_id))


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


def _parse_with_docling(path: str) -> str:
    """Docling -> markdown, exported a page at a time and joined canonically.

    ``export_to_markdown(page_no=…)`` is 1-based; exporting per page keeps page
    attribution exact instead of inferring it from a break placeholder.

    Caveat for this corpus: docling's PDF text backend splits Vietnamese combining
    diacritics ("CTCP S ữ a Vi ệ t Nam"), which marker and pymupdf4llm do not, and
    it is not an OCR setting. Prefer another parser for Vietnamese reports.
    """
    from docling.document_converter import DocumentConverter

    doc = DocumentConverter().convert(path).document
    pages = [
        (doc.export_to_markdown(page_no=n) or "").strip()
        for n in range(1, (doc.num_pages() or 0) + 1)
    ]
    pages = [p for p in pages if p]
    # A document docling reports no pages for still has content worth indexing.
    return _PAGE_JOIN.join(pages) or (doc.export_to_markdown() or "").strip()


def _parse_with_pymupdf4llm(path: str) -> str:
    """PyMuPDF4LLM -> markdown, one chunk per page, joined canonically.

    ``page_chunks=True`` returns a dict per page, so page boundaries come from the
    parser rather than being recovered from separators.
    """
    import pymupdf4llm

    chunks = pymupdf4llm.to_markdown(path, page_chunks=True)
    pages = [(c.get("text") or "").strip() for c in chunks]
    return _PAGE_JOIN.join(p for p in pages if p)


# Parser registry: name -> callable(path) -> page-delimited markdown. Doubles as
# the set of valid ``RAG_PDF_PARSER`` / ``--parser`` / API values, so the choices
# are declared once. Each function imports its own library lazily, so a
# deployment only needs the parsers it actually uses installed.
_PARSERS = {
    "marker": _parse_with_marker,
    "llamaparse": _parse_with_llamaparse,
    "docling": _parse_with_docling,
    "pymupdf4llm": _parse_with_pymupdf4llm,
}
PARSER_CHOICES = tuple(_PARSERS)

# The API validates ``?parser=`` against rag.PDF_PARSERS without importing this
# module; fail loudly at import if the two ever drift apart.
assert set(PARSER_CHOICES) == set(rag.PDF_PARSERS), (
    f"parser registry {PARSER_CHOICES} disagrees with "
    f"report_rag_service.PDF_PARSERS {rag.PDF_PARSERS}"
)


@task(retries=1, retry_delay_seconds=10)
def parse_pdf(pdf_url: str, parser: Optional[str] = None) -> str:
    """Download the PDF and convert it to page-delimited markdown.

    ``parser`` (one of :data:`PARSER_CHOICES`) overrides the ``RAG_PDF_PARSER``
    default. Only the selected parser's library is imported.
    """
    which = (parser or PDF_PARSER or "marker").lower()
    if which not in _PARSERS:
        raise ValueError(
            f"Unknown parser '{which}'. Use one of: {', '.join(PARSER_CHOICES)}."
        )

    resp = requests.get(pdf_url, timeout=120)
    resp.raise_for_status()

    tmp_path = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as fh:
            fh.write(resp.content)
            tmp_path = fh.name
        return _PARSERS[which](tmp_path)
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


# ---------------------------------------------------------------------------
# Summarization: raw PDF markdown -> sectioned digest
# ---------------------------------------------------------------------------

_SUMMARY_SYSTEM = (
    "You are a financial document analysis expert: a versatile, objective "
    "summarizer of financial material — filings, annual and quarterly reports, "
    "analyst notes, financial news and investor commentary — specializing in "
    "structured, section-by-section digests of long-form documents.\n"
    "\n"
    "You are objective above all. You never present a forecast, price target, "
    "rumour or investor sentiment as confirmed fact, and you always distinguish "
    "reported or audited figures from management commentary and from third-party "
    "opinion. Where sources disagree — bullish against bearish, analyst against "
    "management — you represent each viewpoint and attribute it, adopting none as "
    "the truth.\n"
    "\n"
    "You never fabricate, round or approximate a number that is not explicitly "
    "stated in the source, and you add no commentary of your own."
)

# The heading structure *is* the chunk-boundary contract, and each section is
# embedded on its own — which is why the formatting and standalone-section rules
# below are stated so rigidly.
_SUMMARY_INSTRUCTIONS = """\
Produce a structured, section-by-section markdown digest of the financial
document below. Adapt the depth and framing to what the document actually is (a
sector outlook, a company initiation, an earnings preview, a strategy note): let
the document's own structure and terminology drive the sections.

Structure (strict — the headings are used as chunk boundaries downstream):
- Output markdown only. No preamble, no closing remarks, no code fences.
- Every section starts with a level-2 heading (`## `). Use no other heading level.
- One topic per section: a sector, a company, a thesis, a catalyst, a risk, a
  valuation, a forecast, a financial-statement line. Aim for 6-20 sections.
- Section labels must be descriptive and concise, reusing the document's own
  terminology where it has any (e.g. its own section names).
- Heading format: `## <topic in source language> (<English gloss>)`. The English
  gloss is required — it is what makes the section findable from an English query.
  Example: `## Ngành điện (Electricity sector) — triển vọng 2026`

Content rules:
- Each section must stand alone. Name the company, ticker, sector and time period
  inside the section body, even when that repeats the heading. Never refer to
  "the above", "this section" or another section.
- Preserve every material fact exactly as stated: numbers, units, currency,
  percentages, periods, dates, ticker codes, target prices, valuation multiples,
  growth rates, covenants, named risks and drivers. Never round, never omit a
  unit, never state a figure that is not in the source.
- Preserve technical wording verbatim where it carries meaning: entity names,
  accounting terms, GAAP/non-GAAP labels, defined terms, footnote qualifiers
  ("adjusted", "pro forma", "unaudited", "svck", "LTM").
- Attribute every claim that is not a reported figure. Mark forecasts, guidance,
  estimates, ratings and sentiment as such, and say whose they are — e.g.
  "management guides…", "the analyst forecasts…", "consensus expects…". Do not
  restate them as fact.
- Where the document presents competing views or a range of outcomes, keep both
  sides and their attribution rather than resolving them.
- Keep all risk-factor language and required disclosures that carry substance,
  including downside scenarios and regulatory or accounting caveats.
- Write the prose in the document's own language (Vietnamese for these reports),
  keeping English financial terms where the document uses them.
- Be detailed but not redundant: do not repeat the same figure across sections
  unless it is needed to make a section standalone.

Discard as noise (these only): legal disclaimers, analyst certifications, rating
scales, contact details and addresses, tables of contents, page headers/footers,
repeated branding, navigation or UI text, image placeholders. If a section would
carry no concrete content, omit the section entirely.

Document title: {title}
Document symbol: {symbol}

--- DOCUMENT START ---
{body}
--- DOCUMENT END ---
"""


def _page_batches(markdown: str, max_chars: int = SUMMARY_INPUT_CHARS) -> list[str]:
    """Group pages into batches of at most ``max_chars``, never splitting a page.

    Each page is prefixed with a ``[page N]`` marker (1-based, continuous across
    batches) so the model can cite a real page on every section; the digest is
    what gets indexed, so without these the citation would be lost.

    An over-cap page becomes its own batch rather than being cut mid-sentence: the
    cap bounds one response's output, not the model's context window.
    """
    pages = [f"[page {i + 1}]\n{p}" for i, p in enumerate(_split_pages(markdown))]
    batches: list[str] = []
    current: list[str] = []
    size = 0
    for page in pages:
        if current and size + len(page) > max_chars:
            batches.append(_PAGE_JOIN.join(current))
            current, size = [], 0
        current.append(page)
        size += len(page)
    if current:
        batches.append(_PAGE_JOIN.join(current))
    return batches


def _strip_code_fence(text: str) -> str:
    """Drop a wrapping ```markdown fence if the model added one anyway."""
    stripped = text.strip()
    if not stripped.startswith("```"):
        return stripped
    lines = stripped.splitlines()
    if len(lines) >= 2:
        lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        return "\n".join(lines).strip()
    return stripped


@task(retries=1, retry_delay_seconds=15)
def summarize_markdown(markdown: str, title: str = "", symbol: str = "") -> str:
    """Condense parsed report markdown into a heading-structured digest.

    Long reports are condensed in page-aligned batches and the section lists are
    concatenated — the output is already a flat list of ``##`` sections, so no
    reduce step is needed. Returns ``""`` when summarization is unavailable or
    produced nothing usable, which the flow treats as "fall back to page chunks".
    """
    if not (markdown or "").strip():
        return ""

    batches = _page_batches(markdown)
    sections: list[str] = []
    for i, body in enumerate(batches, start=1):
        prompt = _SUMMARY_INSTRUCTIONS.format(
            title=title or "(unknown)", symbol=symbol or "(none)", body=body
        )
        part = llm.chat(
            [
                {"role": "system", "content": _SUMMARY_SYSTEM},
                {"role": "user", "content": prompt},
            ]
        )
        part = _strip_code_fence(part)
        if part:
            sections.append(part)
        if len(batches) > 1:
            _log().info(
                "summarized batch %d/%d: %d chars in -> %d chars out",
                i, len(batches), len(body), len(part),
            )

    return "\n\n".join(sections).strip()


@task(retries=1, retry_delay_seconds=10)
def save_summary(report_id: int, summary: str) -> bool:
    """Store the digest in ``wichart_reports.llm_summary``.

    Overwrites unconditionally: the pipeline is the authority on this column, so
    a re-run replaces the previous digest (and any hand edit made in the Report
    detail page). The store seeds the detail row from the raw feed when the
    report has none yet, so this works on a report that was never synced.
    """
    if not (summary or "").strip():
        return False
    from app.stores.raw_wichart_report import WichartReportStore

    return WichartReportStore().update_summary(int(report_id), summary)


# ---------------------------------------------------------------------------
# Heading-level chunking (over the digest)
# ---------------------------------------------------------------------------

# ATX heading: captures level and text, tolerating trailing closing hashes.
_HEADING_LINE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")

# The ``_Source: p. 4_`` line the digest prompt asks for under every heading. Kept
# in the chunk text (the citation is part of the summary) *and* parsed out into
# the point's ``page`` field, so retrieval can cite a page as it does for raw
# page chunks. Tolerant of the emphasis markers and of "pp." / en-dash ranges.
_SOURCE_LINE = re.compile(
    r"^\s*[_*]*\s*Source\s*:?\s*p{1,2}\.?\s*(\d+)", re.IGNORECASE
)


def _first_page(body: str) -> Optional[int]:
    """0-based page index from a section's ``_Source: p. N_`` line, if present.

    Zero-based to match the page-chunk convention, since the retrieval layer
    renders ``page + 1``. Returns None when the model omitted the line.
    """
    for line in body.splitlines():
        match = _SOURCE_LINE.match(line)
        if match:
            page = int(match.group(1)) - 1
            return page if page >= 0 else None
    return None


def _split_paragraphs(text: str, max_chars: int) -> list[str]:
    """Split over-long section bodies at blank lines, never mid-word."""
    paragraphs = [p for p in re.split(r"\n\s*\n", text) if p.strip()]
    parts: list[str] = []
    current: list[str] = []
    size = 0
    for para in paragraphs:
        # A single paragraph over the cap still has to be broken somewhere.
        if len(para) > max_chars:
            if current:
                parts.append("\n\n".join(current))
                current, size = [], 0
            for i in range(0, len(para), max_chars):
                parts.append(para[i : i + max_chars])
            continue
        if current and size + len(para) > max_chars:
            parts.append("\n\n".join(current))
            current, size = [], 0
        current.append(para)
        size += len(para) + 2
    if current:
        parts.append("\n\n".join(current))
    return parts or [text]


def _build_heading_chunks(
    markdown: str,
    max_chars: int = MAX_SECTION_CHARS,
    min_chars: int = MIN_SECTION_CHARS,
) -> list[dict]:
    """One chunk per heading section, each prefixed with its heading path.

    The heading path (ancestor headings plus its own) is prepended to the chunk
    text so an independently-embedded section still carries the context that
    placed it — a ``## Rủi ro`` section is near-useless without the report or
    sector heading above it.

    A short section folds into the previous chunk only when it is a *deeper*
    heading — a genuine subsection. Siblings never merge, however short: two
    unrelated sectors in one chunk would blur the distinction the heading strategy
    exists to keep. Sections over ``max_chars`` are sub-split at paragraph
    boundaries, repeating the heading path on each part.
    """
    text = (markdown or "").strip()
    if not text:
        return []

    # Pass 1: cut the document into (heading_stack, body) sections.
    sections: list[dict] = []
    stack: list[tuple[int, str]] = []  # (level, raw heading line)
    pending: Optional[dict] = None
    preamble: list[str] = []

    for line in text.splitlines():
        match = _HEADING_LINE.match(line)
        if not match:
            (pending["body"] if pending else preamble).append(line)
            continue
        if pending:
            sections.append(pending)
        level = len(match.group(1))
        # Pop siblings and deeper headings; what remains is this heading's path.
        while stack and stack[-1][0] >= level:
            stack.pop()
        stack.append((level, line.strip()))
        pending = {
            "level": level,
            "heading": match.group(2).strip(),
            "path": [h for _, h in stack],
            "body": [],
        }

    if pending:
        sections.append(pending)

    lead = "\n".join(preamble).strip()
    if lead:
        # Text before the first heading (rare in a digest, common in raw markdown).
        # Level 0 so no real heading is ever merged into it.
        sections.insert(0, {"level": 0, "heading": "", "path": [], "body": [lead]})

    # Pass 2: render, merging stubs and sub-splitting over-long sections.
    chunks: list[dict] = []
    for section in sections:
        body = "\n".join(section["body"]).strip()
        if not body:
            # A heading with no body carries no retrievable content on its own;
            # it stays in the path of its children instead.
            continue
        path = "\n".join(section["path"])
        heading_path = " > ".join(
            _HEADING_LINE.match(h).group(2).strip()
            for h in section["path"]
            if _HEADING_LINE.match(h)
        )

        # Fold a short *subsection* into its parent chunk; never a sibling.
        if (
            chunks
            and len(body) < min_chars
            and chunks[-1]["level"] >= 1
            and section["level"] > chunks[-1]["level"]
        ):
            own_heading = section["path"][-1] if section["path"] else ""
            chunks[-1]["text"] = (
                f"{chunks[-1]['text']}\n\n{own_heading}\n\n{body}".strip()
            )
            continue

        page = _first_page(body)
        for part_no, part in enumerate(_split_paragraphs(body, max_chars)):
            chunks.append(
                {
                    "page": page,
                    "level": section["level"],
                    "heading": section["heading"],
                    "heading_path": heading_path,
                    "part": part_no,
                    "text": f"{path}\n\n{part}".strip() if path else part,
                }
            )
    return chunks


def has_headings(markdown: str) -> bool:
    """Whether the text has at least two ATX headings to chunk on.

    Fewer than two would collapse into one chunk, so the flow treats that as an
    unusable digest and falls back to page chunking — but only when the digest is
    long enough to be heading-split in the first place.
    """
    found = 0
    for line in (markdown or "").splitlines():
        if _HEADING_LINE.match(line):
            found += 1
            if found >= 2:
                return True
    return False


def _single_chunk(markdown: str) -> list[dict]:
    """The whole document as one chunk, keeping its first cited page."""
    text = (markdown or "").strip()
    if not text:
        return []
    if len(text) > _EMBED_SAFE_CHARS:
        _log().warning(
            "single chunk is %d chars, above the ~%d-char point where the "
            "embedding model's context truncates silently — lower "
            "RAG_SINGLE_CHUNK_CHARS so this content gets split instead",
            len(text),
            _EMBED_SAFE_CHARS,
        )
    return [
        {
            "page": _first_page(text),
            "level": 0,
            "heading": "",
            "heading_path": "",
            "part": 0,
            "text": text,
        }
    ]


def pick_strategy(markdown: str, by_heading: bool) -> str:
    """The chunk strategy for this content; short content is indexed whole.

    Shared by :func:`chunk_markdown` and the flow so the strategy recorded on the
    points can never disagree with the one actually used.
    """
    size = len((markdown or "").strip())
    if 0 < size < SINGLE_CHUNK_CHARS:
        return STRATEGY_SINGLE
    return STRATEGY_HEADING if by_heading else STRATEGY_PAGE


@task
def chunk_markdown(markdown: str, by_heading: bool = False) -> list[dict]:
    """Index whole (short content), by heading section (digest), or by page."""
    strategy = pick_strategy(markdown, by_heading)
    if strategy == STRATEGY_SINGLE:
        return _single_chunk(markdown)
    if strategy == STRATEGY_HEADING:
        return _build_heading_chunks(markdown)
    return _build_page_chunks(markdown)


@task(retries=1, retry_delay_seconds=10)
def embed_and_upsert(
    report_id: int,
    symbol: str,
    title: str,
    pdf_url: str,
    chunks: list[dict],
    strategy: str = STRATEGY_PAGE,
) -> int:
    """Embed chunks with the configured backend and upsert them into Qdrant.

    Existing points for this report are deleted first so re-embedding replaces
    rather than duplicates. The collection is created on first use with the vector
    size reported by the embedding model. ``strategy`` is recorded on every point
    as ``chunk_strategy``. Returns the number of chunks.
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
                "page": chunk.get("page"),
                "heading": chunk.get("heading", ""),
                "heading_path": chunk.get("heading_path", ""),
                "chunk_strategy": strategy,
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
    report_id: int,
    recreate: bool = False,
    parser: Optional[str] = None,
    summarize: Optional[bool] = None,
) -> dict:
    """Run the full RAG pipeline for one report, tracking status in MySQL.

    ``parser`` ("marker" | "llamaparse") overrides the RAG_PDF_PARSER default.
    ``summarize`` overrides ``RAG_SUMMARY``: True forces the digest step, False
    forces page chunking of the raw markdown, None follows the env default.
    ``recreate`` currently just forces a re-run; chunks are always replaced.
    """
    logger = get_run_logger()
    report_id = int(report_id)
    # Log where THIS process writes status — compare with the API's
    # /report/rag/health to catch a worker-vs-API status-store mismatch.
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

        # Best-effort: a missing detail row must not stop the report being
        # indexed, and the summary write later seeds it too as a backstop.
        try:
            if ensure_detail_row(report_id):
                logger.info("Detail row ready for report %s", report_id)
            else:
                logger.warning(
                    "No wichart_reports detail row for report %s and none could "
                    "be seeded from the feed",
                    report_id,
                )
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "Could not ensure detail row for report %s (%s); continuing.",
                report_id,
                exc,
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

        # Condense the report into a sectioned digest. Best-effort: any failure
        # falls back to chunking the raw markdown, logged loudly.
        digest = ""
        want_summary = SUMMARY_ENABLED if summarize is None else summarize
        if want_summary:
            if not llm.available():
                logger.warning(
                    "Summary step skipped: no LLM key configured (%s). "
                    "Falling back to page chunks.",
                    llm.describe(),
                )
            else:
                rag.set_status(report_id, rag.SUMMARIZING)
                logger.info(
                    "Summarizing %d chars of markdown for report %s (%s)",
                    len(markdown),
                    report_id,
                    llm.describe(),
                )
                try:
                    digest = summarize_markdown(
                        markdown, title=meta["title"], symbol=meta["symbol"]
                    )
                except Exception as exc:  # noqa: BLE001 — degrade, don't fail the run
                    logger.warning(
                        "Summarization failed for report %s (%s); "
                        "falling back to page chunks.",
                        report_id,
                        exc,
                    )
                if digest:
                    # Persisted before the heading check below: even a digest
                    # that cannot be heading-chunked is still a good summary.
                    try:
                        if save_summary(report_id, digest):
                            logger.info(
                                "Saved %d-char summary for report %s to "
                                "wichart_reports.llm_summary",
                                len(digest),
                                report_id,
                            )
                        else:
                            logger.warning(
                                "Summary for report %s was not saved (no detail "
                                "row could be created)",
                                report_id,
                            )
                    except Exception as exc:  # noqa: BLE001 — indexing matters more
                        logger.warning(
                            "Failed to save summary for report %s (%s); "
                            "continuing with embedding.",
                            report_id,
                            exc,
                        )

                # Headings only have to exist if the digest is long enough to be
                # split on them; a short digest is indexed whole either way.
                if (
                    digest
                    and pick_strategy(digest, True) == STRATEGY_HEADING
                    and not has_headings(digest)
                ):
                    logger.warning(
                        "Summary for report %s has no usable headings (%d chars); "
                        "falling back to page chunks.",
                        report_id,
                        len(digest),
                    )
                    digest = ""

        by_heading = bool(digest)
        strategy = pick_strategy(digest if by_heading else markdown, by_heading)
        if by_heading:
            logger.info(
                "Report %s condensed: %d -> %d chars (%.0f%% of source)",
                report_id,
                len(markdown),
                len(digest),
                100.0 * len(digest) / max(1, len(markdown)),
            )

        chunks = chunk_markdown(digest if by_heading else markdown, by_heading=by_heading)
        if not chunks:
            rag.set_status(report_id, rag.FAILED, error="no text extracted from PDF")
            return {"report_id": report_id, "status": rag.FAILED}

        rag.set_status(report_id, rag.EMBEDDING)
        logger.info(
            "Embedding %d %s-chunks for report %s -> %s  (%s batch=%d)",
            len(chunks),
            strategy,
            report_id,
            COLLECTION,
            embeddings.describe(),
            embeddings.batch_size(),
        )
        n_chunks = embed_and_upsert(
            report_id, meta["symbol"], meta["title"], meta["pdf_url"], chunks, strategy
        )

        rag.set_status(
            report_id,
            rag.EMBEDDED,
            chunk_count=n_chunks,
            collection=COLLECTION,
            error="",
        )
        logger.info(
            "Report %s embedded: %d chunks (%s) in '%s'",
            report_id, n_chunks, strategy, COLLECTION,
        )
        return {
            "report_id": report_id,
            "status": rag.EMBEDDED,
            "chunks": n_chunks,
            "chunk_strategy": strategy,
        }

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
        "--parser", choices=list(PARSER_CHOICES), help="override RAG_PDF_PARSER"
    )
    summary_grp = argp.add_mutually_exclusive_group()
    summary_grp.add_argument(
        "--summarize",
        dest="summarize",
        action="store_true",
        default=None,
        help="force the LLM digest step + heading chunking (overrides RAG_SUMMARY)",
    )
    summary_grp.add_argument(
        "--no-summarize",
        dest="summarize",
        action="store_false",
        help="skip the digest step and chunk raw pages",
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
        result = rag_pipeline_flow(
            ns.report_id,
            recreate=ns.recreate,
            parser=ns.parser,
            summarize=ns.summarize,
        )
        print(result)
