# Report RAG pipeline

Turns a wichart research report (PDF) into embeddings in Qdrant, so reports
become searchable/retrievable. Triggered from the **Report** page (the ✨ action
per row) or the API; runs as a background Prefect flow.

```
Report PDF (url)
   → download + parse to markdown        (marker, paginated)
   → page-level chunking                  (one chunk per page; huge pages sub-split)
   → embed                                (Qwen3-Embedding-8B via OpenAI-compatible API)
   → upsert into Qdrant                    (re-embed replaces prior chunks)
```

**Parser (choose one):**
- **marker** (default) — [marker-pdf](https://github.com/datalab-to/marker),
  local, `paginate_output=True`; the converter loads ML models once and is cached
  as a process-wide singleton. Best layout fidelity, slowest, heaviest.
- **llamaparse** — [LlamaParse](https://cloud.llamaindex.ai) cloud API; needs
  `LLAMA_CLOUD_API_KEY` (or `RAG_LLAMAPARSE_API_KEY`). One Document per page.
- **docling** — [Docling](https://github.com/docling-project/docling), local;
  exported one page at a time via `export_to_markdown(page_no=…)`. Pulls its own
  layout/OCR models (torch + RapidOCR), so first run downloads weights.
  > ⚠️ **Not recommended for these Vietnamese reports.** Docling's PDF text
  > backend splits Vietnamese combining diacritics, emitting `CTCP S ữ a Vi ệ t
  > Nam` where marker and pymupdf4llm both give `CTCP Sữa Việt Nam` — measured at
  > 177 space-isolated diacritics per report versus 3-5 for the others, and it is
  > not an OCR setting (identical with `do_ocr=False`). Broken tokens degrade both
  > the digest and the embeddings.
- **pymupdf4llm** — [PyMuPDF4LLM](https://pymupdf.readthedocs.io/en/latest/pymupdf4llm/),
  local, `page_chunks=True`. No ML models at all: by far the fastest and
  lightest, with correspondingly plainer structure on complex layouts.

Every parser returns **page-delimited** markdown joined by the same canonical
separator, so everything downstream (page batching, `[page N]` markers, the
digest's `_Source: p. N` citations, page-level chunking) is parser-agnostic.

Pick with `RAG_PDF_PARSER` (server default) or per job — the Report page has a
**Parser** dropdown, and `POST /report/{id}/rag?parser=…` accepts an override.
Only the selected parser's library is imported at runtime, so you only need the
ones you actually use installed. The valid names live in
`report_rag_service.PDF_PARSERS` (the API validates against it without importing
the heavy RAG stack; `rag_pipeline` asserts its registry matches at import).

**Chunking:** page-level — each
page becomes one chunk (a page longer than `RAG_MAX_PAGE_CHARS` is sub-split,
keeping its page number). The Qdrant collection is created on first use with the
vector size reported by the model (Cosine distance). Each point's payload carries
`report_id, symbol, title, pdf_url, page, chunk_index, text`.

**Embeddings** — one backend: an **OpenAI-compatible** `POST /v1/embeddings` API,
implemented once in `app/services/embeddings.py` and shared with the read side
(`app/services/tradingagents/kb_search.py`), so ingest and search always agree.

It is the same gateway `app/services/llm.py` uses for the summary step, serving
`openrouter/qwen/qwen3-embedding-8b` at 4096 dimensions. Nothing heavy runs
in-process and there is no model to pull or host.

The key is read from `RAG_OPENAI_API_KEY`, falling back to
`OPENAI_COMPATIBLE_API_KEY` (where the gateway's key is normally stored) and then
`OPENAI_API_KEY`. The default URL is resolved per process —
`host.docker.internal:20128` from inside a container, `localhost:20128` on the
host — because the API runs in Docker while the Prefect worker runs on the host;
override with `RAG_OPENAI_EMBED_URL`.

> Earlier versions also supported a local Ollama server (`RAG_EMBED_BACKEND=ollama`,
> `/api/embed`) and an in-process HuggingFace model (`huggingface`). Both are
> removed — `RAG_EMBED_BACKEND`, `RAG_EMBED_MODEL`, `RAG_OLLAMA_URL`,
> `RAG_HF_EMBED_MODEL` and `RAG_HF_DTYPE` no longer do anything.

Status + the parsed markdown for every report are tracked in **MySQL**
(`report_rag`, keyed by `report_id`) so the list shows which reports are
embedded. Every write is an `INSERT … ON DUPLICATE KEY UPDATE`, so a status
write creates the row if the pipeline was started outside the API.

## Pieces

| File | Role |
|---|---|
| `tasks/rag_pipeline.py` | Prefect `@flow rag_pipeline_flow(report_id)` + tasks |
| `app/services/report_rag_service.py` | MySQL status/markdown store (`report_rag`) |
| `app/db/mysql.py` | shared MySQL engine + schema bootstrap |
| `app/stores/raw_wichart_report.py` | MySQL report detail store (`llm_summary`) |
| `app/api/v1/routes/report.py` | trigger + status endpoints |
| `frontend/.../ReportTable.tsx`, `Report.tsx` | embed action + status badge, polling |

## Status lifecycle

`PENDING → PARSING → PARSED → SUMMARIZING → EMBEDDING → EMBEDDED` (any step →
`FAILED`, with `error`). `SUMMARIZING` is skipped when `RAG_SUMMARY` is off.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/report/{id}/rag?recreate=false` | queue the pipeline (background) |
| GET | `/report/{id}/rag` | one report's RAG status |
| GET | `/report/rag/statuses` | bulk status for the list |
| GET | `/report/{id}/markdown` | parsed markdown (after PARSED) |

## Config (env)

| Var | Default | Notes |
|---|---|---|
| `QDRANT_URL` | `http://192.168.1.3:6333` | your live Qdrant |
| `QDRANT_REPORTS_COLLECTION` | `wichart_reports` | target collection |
| `RAG_PDF_PARSER` | `marker` | `marker` / `docling` / `pymupdf4llm` (local) or `llamaparse` (cloud) |
| `LLAMA_CLOUD_API_KEY` / `RAG_LLAMAPARSE_API_KEY` | _(unset)_ | required for `llamaparse` |
| `RAG_LLAMAPARSE_LANGUAGE` | _(unset)_ | optional OCR language hint, e.g. `vi` |
| `RAG_OPENAI_EMBED_URL` | `host.docker.internal:20128/v1` in Docker / `localhost:20128/v1` on the host | OpenAI-compatible embeddings API |
| `RAG_OPENAI_API_KEY` | falls back to `OPENAI_COMPATIBLE_API_KEY`, then `OPENAI_API_KEY` | bearer token for that API |
| `RAG_OPENAI_EMBED_MODEL` | `openrouter/qwen/qwen3-embedding-8b` | embedding model id |
| `RAG_EMBED_DIMENSIONS` | `4096` | vector size requested from the API |
| `RAG_EMBED_BATCH` | `4` | texts per embed call (keep small after marker) |
| `RAG_EMBED_RETRIES` | `3` | retries on transient connection / 429 / 5xx errors |
| `RAG_MAX_PAGE_CHARS` | `6000` | page-chunk safety cap; longer pages are sub-split |
| `TORCH_DEVICE` | (marker default) | `cpu` / `cuda` / `mps` for marker |
| `MYSQL_HOST` / `MYSQL_PORT` / `MYSQL_USER` / `MYSQL_PASSWORD` / `MYSQL_DB` | `localhost` / `3306` / `root` / — / `my_portfolio` | status + detail store (or `MYSQL_URL` for the whole DSN) |
| `MYSQL_REPORT_RAG_TABLE` | `report_rag` | status table name |

**marker is heavy** — it pulls `torch` and downloads its models on first parse
(cached afterwards, GPU used automatically if available). Embedding itself is a
remote API call, so nothing competes with marker for memory. The Qdrant
collection and the MySQL database/tables are created automatically on first use.

## Run standalone

```bash
cd backend && python tasks/rag_pipeline.py <report_id> [--recreate] [--parser marker|llamaparse|docling|pymupdf4llm]
```

Payload stored per chunk: `report_id, symbol, title, pdf_url, page, chunk_index`
(+ the chunk text), so you can filter retrieval by report, symbol, or page.

## Run as a Prefect deployment (offload to a worker)

Like `sync_cw.py`, the flow can be registered as a deployment so the heavy
marker/embedding work runs on a Prefect worker instead of the API process:

```bash
cd backend && python tasks/rag_pipeline.py --deploy   # deployment: report-rag-pipeline on my-worker
```

It's **on-demand** (no cron) — triggered per `report_id`. To make the API
dispatch to the deployment instead of running in-process, set
`RAG_USE_DEPLOYMENT=1`; the trigger goes through
`prefect_workflow_service.run_rag_pipeline_deployment` (fire-and-forget). Needs a
running worker on the `my-worker` pool; otherwise leave it unset to run in-process.

### Container → host Prefect server

The Prefect server runs on the **host** (`localhost:4200`), but the API runs
**inside the container**, so `run_rag_pipeline_deployment` points the Prefect
client at `http://host.docker.internal:4200/api` by default (override with
`PREFECT_API_URL` / `RAG_PREFECT_API_URL`). On Docker Desktop `host.docker.internal`
resolves automatically; on Linux add to the backend service:

```yaml
extra_hosts:
  - "host.docker.internal:host-gateway"
```

The **worker runs on the host**, so it reaches the embeddings/LLM gateway at
`localhost:20128` (the non-Docker default) and Qdrant at `192.168.1.3:6333`
directly — start it with the backend `.env` loaded so MySQL, ClickHouse, Qdrant and the
gateway key are present:

```bash
cd backend && python tasks/rag_pipeline.py --deploy    # against the host server
prefect worker start --pool my-worker
```

## Deploy note

Deps: `qdrant-client` plus the parser you use (`marker-pdf`, `llama-parse`,
`docling`, `pymupdf4llm`) — rebuild the backend image (or `pip install -r
requirements.txt` from `backend/`). Ensure the backend/worker can reach Qdrant
(`192.168.1.3:6333`) and the OpenAI-compatible gateway (`:20128`).
