"""ISP Alerts Service - Business logic for ISP alerts."""
from typing import List, Optional, Tuple
from datetime import datetime, timedelta
from clickhouse_connect.driver import Client
from pydantic import BaseModel


class ISPAlert(BaseModel):
    """ISP Alert model."""
    symbol: str
    ts: int  # Unix timestamp in milliseconds
    abnormality_ratio_5m: float
    abnormality_ratio_15m: float
    abnormality_ratio_30m: float
    abnormality_ratio_60m: float


class ISPAlertsService:
    """Service for managing ISP alerts."""
    
    def __init__(self, clickhouse_client: Client):
        """
        Initialize service with ClickHouse client.
        
        Args:
            clickhouse_client: ClickHouse client instance
        """
        self.client = clickhouse_client
    
    def _build_where_clause(
        self,
        symbol: Optional[str] = None,
        min_abnormality: Optional[float] = None,
        since: Optional[datetime] = None,
    ) -> str:
        """
        Build WHERE clause for queries.
        
        Args:
            symbol: Filter by symbol
            min_abnormality: Minimum abnormality ratio
            since: Filter by timestamp
            
        Returns:
            WHERE clause string
        """
        where_clauses = []
        
        if symbol:
            # Sanitize symbol for SQL injection prevention
            sanitized_symbol = symbol.replace("'", "''")
            where_clauses.append(f"symbol = '{sanitized_symbol}'")
        
        if min_abnormality is not None:
            where_clauses.append(
                f"(abnormality_ratio_5m >= {min_abnormality} OR "
                f"abnormality_ratio_15m >= {min_abnormality} OR "
                f"abnormality_ratio_30m >= {min_abnormality} OR "
                f"abnormality_ratio_60m >= {min_abnormality})"
            )
        
        if since:
            # Convert to ClickHouse DateTime format
            ts_str = since.strftime('%Y-%m-%d %H:%M:%S')
            where_clauses.append(f"ts > '{ts_str}'")
        
        return " AND ".join(where_clauses) if where_clauses else "1=1"
    
    def _parse_alert_row(self, row: Tuple) -> ISPAlert:
        """
        Parse a row from ClickHouse into ISPAlert model.
        
        Args:
            row: Tuple from ClickHouse query result
            
        Returns:
            ISPAlert instance
        """
        # Convert datetime to Unix timestamp in milliseconds
        ts_datetime = row[1]
        ts_ms = int(ts_datetime.timestamp() * 1000) if isinstance(ts_datetime, datetime) else ts_datetime
        
        return ISPAlert(
            symbol=row[0],
            ts=ts_ms,
            abnormality_ratio_5m=row[2],
            abnormality_ratio_15m=row[3],
            abnormality_ratio_30m=row[4],
            abnormality_ratio_60m=row[5],
        )
    
    def get_alerts(
        self,
        offset: int = 0,
        limit: int = 100,
        symbol: Optional[str] = None,
        min_abnormality: Optional[float] = None,
        since: Optional[datetime] = None,
    ) -> Tuple[List[ISPAlert], int]:
        """
        Get ISP alerts with pagination and filtering.
        
        Args:
            offset: Number of records to skip
            limit: Maximum number of records to return
            symbol: Filter by specific symbol
            min_abnormality: Filter by minimum abnormality ratio (any window)
            since: Get only alerts after this timestamp
            
        Returns:
            Tuple of (alerts list, total count)
        """
        where_clause = self._build_where_clause(symbol, min_abnormality, since)
        
        # Query for total count
        count_query = f"SELECT COUNT(*) FROM isp_alerts WHERE {where_clause}"
        total = self.client.query(count_query).result_rows[0][0]
        
        # Query for data
        data_query = f"""
            SELECT 
                symbol,
                ts,
                abnormality_ratio_5m,
                abnormality_ratio_15m,
                abnormality_ratio_30m,
                abnormality_ratio_60m
            FROM isp_alerts
            WHERE {where_clause}
            ORDER BY ts DESC
            LIMIT {limit}
            OFFSET {offset}
        """
        
        result = self.client.query(data_query)
        
        # Parse results
        alerts = [self._parse_alert_row(row) for row in result.result_rows]
        
        return alerts, total
    
    def get_latest_alerts(
        self,
        limit: int = 50,
        since: Optional[datetime] = None,
    ) -> List[ISPAlert]:
        """
        Get latest alerts since a specific timestamp.
        
        Optimized for real-time display and incremental loading.
        
        Args:
            limit: Maximum number of alerts to return
            since: Get alerts after this timestamp. If None, returns most recent alerts.
            
        Returns:
            List of latest ISP alerts ordered by timestamp DESC
        """
        where_clause = "1=1"
        
        if since:
            # Convert to ClickHouse DateTime format
            ts_str = since.strftime('%Y-%m-%d %H:%M:%S')
            where_clause = f"ts > '{ts_str}'"
        
        query = f"""
            SELECT 
                symbol,
                ts,
                abnormality_ratio_5m,
                abnormality_ratio_15m,
                abnormality_ratio_30m,
                abnormality_ratio_60m
            FROM isp_alerts
            WHERE {where_clause}
            ORDER BY ts DESC
            LIMIT {limit}
        """
        
        result = self.client.query(query)
        
        # Parse results
        alerts = [self._parse_alert_row(row) for row in result.result_rows]
        
        return alerts
    
    def get_active_symbols(self, seconds: int = 300) -> List[str]:
        """
        Get list of symbols that have recent alerts.
        
        Args:
            seconds: Look back this many seconds
            
        Returns:
            List of active symbol names
        """
        threshold = datetime.now() - timedelta(seconds=seconds)
        ts_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
        
        query = f"""
            SELECT DISTINCT symbol
            FROM isp_alerts
            WHERE ts > '{ts_str}'
            ORDER BY symbol
        """
        
        result = self.client.query(query)
        return [row[0] for row in result.result_rows]
    
    def get_alert_statistics(self, seconds: int = 300) -> dict:
        """
        Get statistics about recent alerts.
        
        Args:
            seconds: Look back this many seconds
            
        Returns:
            Dictionary with statistics
        """
        threshold = datetime.now() - timedelta(seconds=seconds)
        ts_str = threshold.strftime('%Y-%m-%d %H:%M:%S')
        
        query = f"""
            SELECT 
                COUNT(*) as total_alerts,
                COUNT(DISTINCT symbol) as unique_symbols,
                AVG(abnormality_ratio_5m) as avg_5m,
                AVG(abnormality_ratio_15m) as avg_15m,
                AVG(abnormality_ratio_30m) as avg_30m,
                AVG(abnormality_ratio_60m) as avg_60m,
                MAX(abnormality_ratio_5m) as max_5m,
                MAX(abnormality_ratio_15m) as max_15m,
                MAX(abnormality_ratio_30m) as max_30m,
                MAX(abnormality_ratio_60m) as max_60m
            FROM isp_alerts
            WHERE ts > '{ts_str}'
        """
        
        result = self.client.query(query)
        row = result.result_rows[0]
        
        return {
            'total_alerts': row[0],
            'unique_symbols': row[1],
            'average_ratios': {
                '5m': float(row[2]) if row[2] else 0.0,
                '15m': float(row[3]) if row[3] else 0.0,
                '30m': float(row[4]) if row[4] else 0.0,
                '60m': float(row[5]) if row[5] else 0.0,
            },
            'max_ratios': {
                '5m': float(row[6]) if row[6] else 0.0,
                '15m': float(row[7]) if row[7] else 0.0,
                '30m': float(row[8]) if row[8] else 0.0,
                '60m': float(row[9]) if row[9] else 0.0,
            },
        }

