from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.api.deps import require_user
from app.api.v1.routes import future, sector
from app.core.settings import _build_mysql_url, settings
from app.db.clickhouse import get_clickhouse_client
from app.main import app


@pytest.mark.parametrize("endpoint", ["ohlc-5m", "rl-exits"])
def test_future_binds_request_values(endpoint, monkeypatch):
    calls = []
    class Client:
        def query(self, query, parameters):
            calls.append((query, parameters))
            return SimpleNamespace(result_rows=[])

    monkeypatch.setattr(future, "_load_rl_model", lambda symbol: object())
    app.dependency_overrides[get_clickhouse_client] = lambda: Client()
    try:
        with TestClient(app) as client:
            response = client.get(f"/api/v1/future/{endpoint}/VN30F", params={"start_date": "x' OR 1=1 --"})
        assert response.status_code == 200
        query, params = calls[0]
        assert "x' OR 1=1 --" not in query
        assert params["start"] == "x' OR 1=1 --"
        assert params["symbol"] == "VN30F"
        assert "{symbol:String}" in query and "{start:String}" in query
    finally:
        app.dependency_overrides.pop(get_clickhouse_client, None)


def test_future_handlers_are_synchronous():
    import inspect
    assert not inspect.iscoroutinefunction(future.get_ohlc_5m)
    assert not inspect.iscoroutinefunction(future.get_rl_exits)


def test_custom_prefix_exempts_login(monkeypatch):
    monkeypatch.setattr(settings, "api_v1_prefix", "/custom")
    request = SimpleNamespace(url=SimpleNamespace(path="/custom/auth/login"))
    assert require_user(request, None, None) is None


def test_mysql_credentials_are_required(monkeypatch):
    monkeypatch.delenv("MYSQL_USER", raising=False)
    monkeypatch.delenv("MYSQL_PASSWORD", raising=False)
    with pytest.raises(ValueError, match="MYSQL_USER and MYSQL_PASSWORD"):
        _build_mysql_url()


@pytest.mark.parametrize("dotenv", [
    "APP_DATABASE_URL=sqlite:///./test.db\n",
    "DATABASE_URL=sqlite:///./test.db\n",
    "MYSQL_USER=testuser\nMYSQL_PASSWORD=testpassword\n",
])
def test_settings_loads_database_config_from_dotenv(tmp_path, dotenv):
    (tmp_path / ".env").write_text(dotenv)
    env = os.environ.copy()
    for key in ("MYSQL_USER", "MYSQL_PASSWORD", "APP_MYSQL_USER", "APP_MYSQL_PASSWORD", "APP_DATABASE_URL", "DATABASE_URL"):
        env.pop(key, None)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1])
    result = subprocess.run(
        [sys.executable, "-c", "from app.core.settings import settings; assert settings.database_url"],
        cwd=tmp_path, env=env, capture_output=True, text=True,
    )
    assert result.returncode == 0, result.stderr


def test_sector_uploads_use_unique_files(monkeypatch):
    paths = []
    def capture(db, path, level):
        paths.append(path)
        with open(path, "rb") as f:
            assert f.read() == b"[]"
        return 0
    monkeypatch.setattr(sector.sector_service, "upsert_sector_from_file", capture)
    with TestClient(app) as client:
        def upload(_):
            return client.post("/api/v1/sector/import/1", files={"file": ("sector.json", b"[]", "application/json")})
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(upload, range(2)))
    assert all(response.status_code == 200 for response in responses)
    assert len(set(paths)) == 2
