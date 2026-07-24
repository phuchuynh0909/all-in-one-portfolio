# Report RAG pipeline

Turns a wichart research report (PDF) into embeddings in Qdrant, so reports
become searchable/retrievable. Triggered from the **Report** page (the ✨ action
per row) or the API; runs as a background Prefect flow.

```
Report PDF (url)
   → download + parse to markdown        (marker, paginated)
   → page-level chunking                  (one chunk per page; huge pages sub-split)
   → embed                                (Ollama, Qwen3-Embedding-8B)
   → upsert into Qdrant                    (re-embed replaces prior chunks)
```

**Parser (choose one):**
- **marker** (default) — [marker-pdf](https://github.com/datalab-to/marker),
  local, `paginate_output=True`; the converter loads ML models once and is cached
  as a process-wide singleton.
- **llamaparse** — [LlamaParse](https://cloud.llamaindex.ai) cloud API; needs
  `LLAMA_CLOUD_API_KEY` (or `RAG_LLAMAPARSE_API_KEY`). Each returned page is
  joined with a canonical separator so page-level chunking is preserved.

Pick with `RAG_PDF_PARSER` (server default) or per job — the Report page has a
**Parser** dropdown, and `POST /report/{id}/rag?parser=marker|llamaparse` accepts
an override. Only the selected parser's library is imported at runtime.

**Chunking:** page-level — each
page becomes one chunk (a page longer than `RAG_MAX_PAGE_CHARS` is sub-split,
keeping its page number). **Embeddings:** Ollama (`qwen3-embedding:8b` by
default) via `/api/embed`; the Qdrant collection is created on first use with the
vector size reported by the model (Cosine distance). Each point's payload carries
`report_id, symbol, title, pdf_url, page, chunk_index, text`.

> **Ollama prerequisite:** `ollama pull qwen3-embedding:8b`, and the server must
> support embeddings — recent Ollama exposes `/api/embed` out of the box; older
> builds need to be started with `--embeddings` (the flow surfaces Ollama's own
> error message on failure). In Docker the backend reaches host Ollama at
> `host.docker.internal:11434` (override with `RAG_OLLAMA_URL`).

Status + the parsed markdown for every report are tracked in ClickHouse
(`report_rag`, ReplacingMergeTree) so the list shows which reports are embedded.

## Pieces

| File | Role |
|---|---|
| `tasks/rag_pipeline.py` | Prefect `@flow rag_pipeline_flow(report_id)` + tasks |
| `app/services/report_rag_service.py` | ClickHouse status/markdown store (`report_rag`) |
| `app/api/v1/routes/report.py` | trigger + status endpoints |
| `frontend/.../ReportTable.tsx`, `Report.tsx` | embed action + status badge, polling |

## Status lifecycle

`PENDING → PARSING → PARSED → EMBEDDING → EMBEDDED` (any step → `FAILED`, with
`error`).

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
| `RAG_PDF_PARSER` | `marker` | `marker` (local) or `llamaparse` (cloud) |
| `LLAMA_CLOUD_API_KEY` / `RAG_LLAMAPARSE_API_KEY` | _(unset)_ | required for `llamaparse` |
| `RAG_LLAMAPARSE_LANGUAGE` | _(unset)_ | optional OCR language hint, e.g. `vi` |
| `RAG_EMBED_MODEL` | `qwen3-embedding:8b` | Ollama embedding model (must be pulled) |
| `RAG_OLLAMA_URL` | `OLLAMA_BASE_URL` or `http://host.docker.internal:11434` | Ollama server (native `/api`) |
| `RAG_EMBED_BATCH` | `4` | texts per `/api/embed` call (keep small after marker) |
| `RAG_EMBED_RETRIES` | `3` | retries on transient EOF / connection errors |
| `RAG_MAX_PAGE_CHARS` | `6000` | page-chunk safety cap; longer pages are sub-split |
| `TORCH_DEVICE` | (marker default) | `cpu` / `cuda` / `mps` for marker |
| `CLICKHOUSE_REPORT_RAG_TABLE` | `report_rag` | status table name |

**marker is heavy** — it pulls `torch` and downloads its models on first parse
(cached afterwards, GPU used automatically if available). The **embedding model
runs on your Ollama host** (`ollama pull qwen3-embedding:8b`). The Qdrant
collection and the ClickHouse table are created automatically on first use.

## Run standalone

```bash
cd backend && python tasks/rag_pipeline.py <report_id> [--recreate] [--parser marker|llamaparse]
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

The **worker runs on the host**, so it reaches Ollama at `localhost:11434` (the
`_ollama_base` default) and Qdrant at `192.168.1.3:6333` directly — start it with
the backend `.env` loaded so ClickHouse/Qdrant/Ollama config is present:

```bash
cd backend && python tasks/rag_pipeline.py --deploy    # against the host server
prefect worker start --pool my-worker
```

## Deploy note

New deps (`qdrant-client`, `ollama`, `marker-pdf`, `llama-parse`) — rebuild the
backend image (or `pip install -r requirements.txt` from `backend/`). Ensure the
backend/worker can reach Qdrant (`192.168.1.3:6333`) and Ollama.
