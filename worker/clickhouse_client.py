"""ClickHouse client wrapper for ISP worker."""
from datetime import datetime, time as dtime, timedelta
from collections import OrderedDict
import clickhouse_connect  # type: ignore
from config import config


class ClickHouseClient:
    """Wrapper for ClickHouse operations."""
    
    def __init__(self, host: str = None, port: int = None, user: str = None, 
                 password: str = None, database: str = None):
        """Initialize ClickHouse client with optional connection parameters.
        
        Args:
            host: ClickHouse host (defaults to config)
            port: ClickHouse port (defaults to config)
            user: ClickHouse username (defaults to config)
            password: ClickHouse password (defaults to config)
            database: ClickHouse database (defaults to config)
        """
        self.host = host or config.clickhouse.host
        self.port = port or config.clickhouse.port
        self.user = user or config.clickhouse.user
        self.password = password or config.clickhouse.password
        self.database = database or config.clickhouse.database
        self._client = None
    
    @property
    def client(self):
        """Lazy initialization of ClickHouse client (singleton pattern)."""
        if self._client is None:
            try:
                self._client = clickhouse_connect.get_client(
                    host=self.host,
                    port=self.port,
                    username=self.user,
                    password=self.password,
                    database=self.database,
                )
            except Exception as e:
                print(f"Error connecting to ClickHouse: {e}")
                raise
        return self._client
    
    def query(self, sql: str):
        """Execute a SQL query and return results.
        
        Args:
            sql: SQL query string
            
        Returns:
            Query result object
        """
        return self.client.query(sql)
    
    def fetch_bins_for_day(
        self,
        symbol: str,
        day: datetime.date,
        session_start: dtime,
        session_end: dtime,
        bin_minutes: int,
        bin_count: int,
    ) -> list[float] | None:
        """Fetch volume bins for a specific trading day.
        
        Args:
            symbol: Stock symbol
            day: Trading day date
            session_start: Session start time
            session_end: Session end time
            bin_minutes: Bin size in minutes
            bin_count: Total number of bins in session
            
        Returns:
            List of volume values per bin, or None on error
        """
        if not symbol:
            return None
        
        try:
            start_dt = datetime.combine(day, session_start)
            end_dt = datetime.combine(day, session_end)
            start_str = start_dt.strftime("%Y-%m-%d %H:%M:%S")
            end_str = end_dt.strftime("%Y-%m-%d %H:%M:%S")
            sess_start_min = session_start.hour * 60 + session_start.minute
            sess_end_min = session_end.hour * 60 + session_end.minute
            
            sql = f"""
                WITH
                  toDateTime('{start_str}') AS start_dt,
                  toDateTime('{end_str}')   AS end_dt,
                  {sess_start_min} AS sess_start_min,
                  {sess_end_min}   AS sess_end_min
                SELECT bin_idx, vol
                FROM (
                  SELECT
                    intDiv(
                      (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) - sess_start_min,
                      {bin_minutes}
                    ) AS bin_idx,
                    sum(volume) AS vol
                  FROM {self.database}.ohlc_1m
                  WHERE ts >= start_dt
                    AND ts <  end_dt
                    AND symbol = '{symbol}'
                    AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) >= sess_start_min
                    AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) <  sess_end_min
                  GROUP BY bin_idx
                )
                ORDER BY bin_idx
            """
            
            result = self.query(sql)
            rows = result.result_rows
            
            # Build zero-filled bins
            bins = [0.0] * bin_count
            for idx, vol in rows:
                if 0 <= idx < bin_count:
                    bins[idx] = float(vol)
            
            return bins
            
        except Exception as e:
            print(f"Error fetching bins for {symbol} on {day}: {e}")
            return None
    
    def fetch_historical_bins(
        self,
        symbol: str,
        end_day: datetime.date,
        session_start: dtime,
        session_end: dtime,
        bin_minutes: int,
        bin_count: int,
        lookback_days: int,
        alpha: float = None,
    ) -> list[float] | None:
        """Fetch and smooth historical volume bins over multiple days.
        
        Args:
            symbol: Stock symbol
            end_day: Last day to include (exclusive)
            session_start: Session start time
            session_end: Session end time
            bin_minutes: Bin size in minutes
            bin_count: Total number of bins in session
            lookback_days: Number of historical days to aggregate
            alpha: Smoothing factor (defaults to config.isp.alpha)
            
        Returns:
            List of smoothed volume values per bin, or None on error
        """
        if not symbol:
            return None
        
        if alpha is None:
            alpha = config.isp.alpha
        
        try:
            # Calculate date range
            start_day_dt = datetime.combine(end_day, dtime(0, 0)) - timedelta(days=lookback_days - 1)
            end_day_dt = datetime.combine(end_day, dtime(23, 59, 59))
            start_str = start_day_dt.strftime('%Y-%m-%d %H:%M:%S')
            end_str = end_day_dt.strftime('%Y-%m-%d %H:%M:%S')
            sess_start_min = session_start.hour * 60 + session_start.minute
            sess_end_min = session_end.hour * 60 + session_end.minute
            
            sql = f"""
                WITH
                  toDateTime('{start_str}') AS start_dt,
                  toDateTime('{end_str}')   AS end_dt,
                  {sess_start_min} AS sess_start_min,
                  {sess_end_min}   AS sess_end_min
                SELECT d, bin_idx, vol
                FROM (
                  SELECT
                    toDate(ts) AS d,
                    intDiv(
                      (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) - sess_start_min,
                      {bin_minutes}
                    ) AS bin_idx,
                    sum(volume) AS vol
                  FROM {self.database}.ohlc_1m
                  WHERE ts >= start_dt
                    AND ts <= end_dt
                    AND symbol = '{symbol}'
                    AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) >= sess_start_min
                    AND (toRelativeMinuteNum(ts) - toRelativeMinuteNum(toStartOfDay(ts))) <  sess_end_min
                  GROUP BY d, bin_idx
                )
                ORDER BY d, bin_idx
            """
            
            result = self.query(sql)
            rows = result.result_rows
            
            if not rows:
                return [0.0] * bin_count
            
            # Organize data by day
            daily = OrderedDict()
            for d, idx, vol in rows:
                key = str(d)
                if key not in daily:
                    daily[key] = [0.0] * bin_count
                if 0 <= idx < bin_count:
                    daily[key][idx] = float(vol)
            
            # Apply exponential smoothing across days
            smoothed = None
            for _, day_bins in daily.items():
                if smoothed is None:
                    smoothed = [float(v) for v in day_bins]
                else:
                    smoothed = [
                        alpha * float(vd) + (1.0 - alpha) * float(vs)
                        for vd, vs in zip(day_bins, smoothed)
                    ]
            
            if smoothed is None:
                return [0.0] * bin_count
            
            return smoothed
            
        except Exception as e:
            print(f"Error fetching historical bins for {symbol}: {e}")
            return None
    
    def test_connection(self) -> bool:
        """Test the ClickHouse connection.
        
        Returns:
            True if connection is successful, False otherwise
        """
        try:
            result = self.client.query("SELECT 1")
            return result is not None
        except Exception as e:
            print(f"Connection test failed: {e}")
            return False
    
    def close(self):
        """Close the ClickHouse client connection."""
        if self._client is not None:
            try:
                self._client.close()
            except Exception as e:
                print(f"Error closing ClickHouse client: {e}")
            finally:
                self._client = None


# Global singleton instance
_global_client = None


def get_clickhouse_client() -> ClickHouseClient:
    """Get or create the global ClickHouse client instance.
    
    Returns:
        Global ClickHouseClient instance
    """
    global _global_client
    if _global_client is None:
        _global_client = ClickHouseClient()
    return _global_client

