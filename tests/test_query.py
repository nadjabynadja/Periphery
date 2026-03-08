import pytest
from httpx import ASGITransport, AsyncClient

# Integration test — requires full app startup
# These are marked for manual running since they need model downloads

@pytest.mark.skip(reason="Requires model download and full app init")
@pytest.mark.asyncio
async def test_full_ingest_and_query():
    from periphery.main import app

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Ingest some data
        response = await client.post("/ingest/", json={
            "content": "Machine learning is a subset of artificial intelligence.",
            "content_type": "text/plain",
        })
        assert response.status_code == 200
        data = response.json()
        assert data["count"] >= 1

        # Search
        response = await client.post("/ingest/search", json={
            "query": "AI and machine learning",
            "top_k": 5,
        })
        assert response.status_code == 200

        # Health check
        response = await client.get("/health")
        assert response.status_code == 200
        assert response.json()["status"] == "healthy"
