import os

from prefect.deployments import run_deployment


def _prefect_api_url() -> str:
    """Resolve the Prefect API URL to talk to.

    The Prefect server runs on the host (``localhost:4200``), but this code runs
    inside the API container where ``localhost`` is the container itself — so we
    reach the host server via ``host.docker.internal``. An explicit
    ``PREFECT_API_URL`` (or ``RAG_PREFECT_API_URL``) always wins, so a non-Docker
    or differently-networked deployment can override it.
    """
    return (
        os.getenv("RAG_PREFECT_API_URL")
        or os.getenv("PREFECT_API_URL")
        or "http://host.docker.internal:4200/api"
    )


def run_rag_pipeline_deployment(report_id: int, recreate: bool = False, parser: str | None = None):
    """Trigger the report RAG pipeline deployment (runs on the Prefect worker).

    Register it once with ``python tasks/rag_pipeline.py --deploy`` (deployment
    ``report-rag-pipeline/report-rag-pipeline`` on the ``my-worker`` pool).
    ``timeout=0`` schedules the run and returns immediately (fire-and-forget).

    The Prefect client is pointed at the host server (see ``_prefect_api_url``)
    for the duration of the call, so scheduling works from inside the container.
    """
    from prefect.settings import PREFECT_API_URL, temporary_settings

    with temporary_settings({PREFECT_API_URL: _prefect_api_url()}):
        return run_deployment(
            name="report-rag-pipeline/report-rag-pipeline",
            parameters={"report_id": int(report_id), "recreate": recreate, "parser": parser},
            timeout=0,
        )


def run_sync_stock_workflow(symbol: str):
    """
    Run the stock sync workflow deployment and wait for completion.
    
    Args:
        symbol: The stock symbol to sync
        
    Returns:
        FlowRun: The completed flow run object
    """
    flow_run = run_deployment(
        name="1eb96644-fe2a-478e-a43a-a01a75687b6b",  # deployment ID
        parameters={"symbol": symbol},
        timeout=None,  # Wait indefinitely for completion
    )
    return flow_run
