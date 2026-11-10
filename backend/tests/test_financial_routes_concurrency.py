import asyncio
import threading

import httpx
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.v1.routes.financial_statements import router
from app.db.base import get_db


def test_financial_query_does_not_block_other_requests():
    started, release = threading.Event(), threading.Event()

    class Database:
        def execute(self, *args):
            started.set()
            release.wait(timeout=3)
            return self

        def fetchall(self):
            return []

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: Database()

    @app.get("/ping")
    async def ping():
        return {"ok": True}

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
            query = asyncio.create_task(client.get("/financial/periods"))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                response = await client.get("/ping")
                assert response.json() == {"ok": True}
                assert not query.done()
            finally:
                release.set()
                result = await query
            assert result.status_code == 200
            assert result.json() == []

    asyncio.run(run())


def test_missing_company_returns_same_404_for_both_endpoints():
    class Database:
        def execute(self, query, params):
            assert params == {"ticker": "UNKNOWN"}
            return self

        def fetchone(self):
            return None

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[get_db] = lambda: Database()
    client = TestClient(app)
    for suffix in ("", "/summary"):
        response = client.get(f"/financial/companies/UNKNOWN/statements{suffix}")
        assert response.status_code == 404
        assert response.json() == {"detail": "Company UNKNOWN not found"}
