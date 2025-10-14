
import asyncio
from prefect.client.orchestration import get_client

def run_sync_stock_workflow(symbol: str):
    client = get_client()
    flow_run = client.create_flow_run_from_deployment(deployment_id="f71c5783-46e2-47e3-939b-da025d338fde", parameters={"symbol": symbol})
    flow_run = client.wait_for_flow_run(flow_run_id=flow_run.id)
    return flow_run
