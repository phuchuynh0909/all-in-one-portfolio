import pandas as pd
from deltalake import DeltaTable
from app.core.settings import settings

class WichartReportStore:
    def get_data(self, mack: str | None = None) -> pd.DataFrame:
        dt = DeltaTable(settings.wichart_report_delta_table, storage_options=settings.delta_storage_options)
        if mack:
            df = dt.to_pandas(filters=[("mack", "==", mack.upper())])
        else:
            df = dt.to_pandas()
        return df
