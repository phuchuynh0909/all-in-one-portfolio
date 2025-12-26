from prefect.deployments import run_deployment


def run_sync_stock_workflow(symbol: str):
    """
    Run the stock sync workflow deployment and wait for completion.
    
    Args:
        symbol: The stock symbol to sync
        
    Returns:
        FlowRun: The completed flow run object
    """
    flow_run = run_deployment(
        name="f71c5783-46e2-47e3-939b-da025d338fde",  # deployment ID
        parameters={"symbol": symbol},
        timeout=None,  # Wait indefinitely for completion
    )
    return flow_run
