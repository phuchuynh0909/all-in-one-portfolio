# ---------------- ClickHouse replay source ----------------
import clickhouse_connect  # type: ignore
from bytewax.inputs import StatelessSourcePartition, DynamicSource
from datetime import datetime
import orjson
from typing import Tuple

class _ClickHouseReplayPartition(StatelessSourcePartition):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
        symbols: list[str],
        start_ts: str,
        end_ts: str,
        speed: float = 1.0,
        loop: bool = False,
        topic_prefix: str = "mock/ch",
        refresh_per_loop: bool = False,
    ):
        self._client = clickhouse_connect.get_client(
            host=host, port=port, username=username, password=password, database=database
        )
        self._symbols = symbols
        self._start_ts = start_ts
        self._end_ts = end_ts
        self._speed = max(0.0, float(speed))
        self._loop = bool(loop)
        self._topic_prefix = topic_prefix
        self._refresh_per_loop = bool(refresh_per_loop)
        self._iter = self._row_iter()

    def _query_rows(self):
        sym_list = ",".join([f"'{s}'" for s in self._symbols])
        sql = f"""
            SELECT ts, symbol, close, volume
            FROM default.ohlc_1m
            WHERE symbol IN ({sym_list})
              AND ts >= toDateTime('{self._start_ts}')
              AND ts <= toDateTime('{self._end_ts}')
            ORDER BY ts, symbol
        """
        return self._client.query(sql).result_rows

    def _row_iter(self):
        rows = self._query_rows()
        # compute per-symbol cumulative volume
        cum_by_symbol: dict[str, float] = {}
        for ts, sym, close, vol in rows:
            topic = f"{self._topic_prefix}/{sym}"
            ts_dt = ts if isinstance(ts, datetime) else datetime.fromisoformat(str(ts))
            prev_cum = cum_by_symbol.get(sym, 0.0)
            new_cum = prev_cum + float(vol or 0.0)
            cum_by_symbol[sym] = new_cum

            payload = {
                "marketId": "MARKET_ID_STO",
                "boardId": "BOARD_ID_G1",
                "isin": "VN000000VCG3",
                "sendingTime": ts_dt.isoformat().replace("+00:00", "Z"),
                "symbol": sym,
                "matchPrice": float(close or 0.0),
                "matchQtty": float(vol or 0.0),
                "totalVolumeTraded": float(new_cum),
                "grossTradeAmount": float((close or 0.0) * (vol or 0.0)),
                "side": 0,
            }
            yield (topic, orjson.dumps(payload))

    def next_batch(self, sched=None):
        batch: list[Tuple[str, bytes]] = []
        try:
            for _ in range(1024):
                batch.append(next(self._iter))
        except StopIteration:
            pass
        return batch

    def close(self):
        try:
            self._client.close()
        except Exception:
            pass


class MockClickHouseSource(DynamicSource):
    def __init__(
        self,
        host: str,
        port: int,
        username: str,
        password: str,
        database: str,
        symbols: list[str],
        start_ts: str,
        end_ts: str,
        speed: float = 1.0,
        loop: bool = False,
        topic_prefix: str = "mock/ch",
        refresh_per_loop: bool = False,
    ):
        self._host = host
        self._port = port
        self._username = username
        self._password = password
        self._database = database
        self._symbols = symbols
        self._start_ts = start_ts
        self._end_ts = end_ts
        self._speed = speed
        self._loop = loop
        self._topic_prefix = topic_prefix
        self._refresh_per_loop = refresh_per_loop

    def build(self, _step_id: str, _worker_index: int, _worker_count: int):
        return _ClickHouseReplayPartition(
            self._host,
            self._port,
            self._username,
            self._password,
            self._database,
            self._symbols,
            self._start_ts,
            self._end_ts,
            speed=self._speed,
            loop=self._loop,
            topic_prefix=self._topic_prefix,
            refresh_per_loop=self._refresh_per_loop,
        )
