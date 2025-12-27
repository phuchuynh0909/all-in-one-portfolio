import pandas as pd
from deltalake import DeltaTable
from app.core.settings import settings
from datetime import datetime

class FeatureStore:
    
    def get_features(self, symbol: str, start: datetime | None = None, end: datetime | None = None, columns: list | None = None) -> pd.DataFrame:
        dt = DeltaTable(settings.stocks_feature_store, storage_options=settings.delta_storage_options)

        df = dt.to_pandas(filters=[("symbol", "=", symbol)], columns=columns)
        if start:
            df = df[df["date"] >= start]
        if end:
            df = df[df["date"] <= end]
        
        return df.sort_values("date").reset_index(drop=True)
