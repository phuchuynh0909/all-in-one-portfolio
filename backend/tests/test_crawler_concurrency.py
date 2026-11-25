import asyncio
import threading

import httpx
from fastapi import FastAPI

from app.api.v1.routes.data_crawler import router
from app.db.base import get_db


def test_slow_crawler_query_does_not_block_other_requests():
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
            crawl = asyncio.create_task(client.get("/crawler/available-symbols"))
            try:
                assert await asyncio.to_thread(started.wait, 2)
                assert (await client.get("/ping")).json() == {"ok": True}
                assert not crawl.done()
            finally:
                release.set()
                response = await crawl
            assert response.status_code == 200
            assert response.json() == []

    asyncio.run(run())
