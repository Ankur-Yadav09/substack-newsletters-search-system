from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from src.api.dependencies import rate_limit, verify_api_key
from src.api.main import app


@pytest.fixture(autouse=True)
def bypass_auth_and_rate_limit():
    """These tests exercise route/business logic, not the auth layer itself (see
    test_dependencies.py for that) — bypass both via FastAPI's dependency_overrides
    so a missing/placeholder API_SECURITY__API_KEY in the test environment doesn't
    fail every request here.
    """
    app.dependency_overrides[verify_api_key] = lambda: None
    app.dependency_overrides[rate_limit] = lambda: None
    yield
    app.dependency_overrides.pop(verify_api_key, None)
    app.dependency_overrides.pop(rate_limit, None)


def _fake_point(point_id: str, title: str):
    """Build a stand-in for a Qdrant ScoredPoint, shaped the way search_service.py expects."""
    return SimpleNamespace(
        id=point_id,
        score=0.9,
        payload={
            "title": title,
            "feed_author": "Test Author",
            "feed_name": "Test Feed",
            "article_authors": ["Test Author"],
            "url": "https://example.com/test-article",
            "chunk_text": "This is a test chunk about RAG.",
        },
    )


@pytest.fixture(autouse=True)
def fake_vectorstore():
    """Patch AsyncQdrantVectorStore for every test in this module.

    Without this, FastAPI's lifespan (src/api/main.py) constructs a *real*
    AsyncQdrantVectorStore on every test, which loads real Fastembed models and
    opens a connection to a real Qdrant instance. That makes these "unit" tests
    slow, flaky, and dependent on external infrastructure being reachable and
    pre-populated. Patching the class keeps every test here fully offline and
    deterministic.
    """
    fake_client = MagicMock()
    fake_client.query_points = AsyncMock(
        return_value=SimpleNamespace(
            points=[
                _fake_point("11111111-1111-1111-1111-111111111111", "Test Article One")
            ]
        )
    )
    fake_client.get_collections = AsyncMock(
        return_value=SimpleNamespace(collections=[])
    )
    fake_client.close = AsyncMock()

    fake_store = MagicMock()
    fake_store.dense_vectors = MagicMock(return_value=[[0.0] * 768])
    fake_store.sparse_vectors = MagicMock(
        return_value=[MagicMock(indices=[], values=[])]
    )
    fake_store.collection_name = "test_collection"
    fake_store.client = fake_client

    # Reranking is on by default (settings.reranker.enabled=True); give it a
    # real return value (not an unconfigured MagicMock) so search_service.py's
    # zip(candidates, scores) works. A constant score is fine here since these
    # tests only ever return a single fake point, so relative ordering never
    # matters for what they assert.
    fake_store.reranker_settings = SimpleNamespace(enabled=True, candidate_pool_size=25)
    fake_store.rerank = MagicMock(
        side_effect=lambda query, documents: [1.0] * len(documents)
    )

    with patch("src.api.main.AsyncQdrantVectorStore", return_value=fake_store):
        yield fake_store


@pytest.mark.asyncio
async def test_lifespan_and_client():
    """Verify the app lifespan wires up app.state.vectorstore, and that both
    the liveness (/health) and readiness (/ready) endpoints respond correctly
    against the faked vector store.
    """
    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            health_response = await client.get("/health")
            assert health_response.status_code == 200, (
                "Health endpoint did not return 200 OK"
            )

            ready_response = await client.get("/ready")
            assert ready_response.status_code == 200, (
                "Ready endpoint did not return 200 OK"
            )
            assert ready_response.json()["status"] == "ready"


@pytest.mark.asyncio
async def test_search_unique_titles_route():
    """Test that /search/unique-titles returns the (faked) hybrid search results."""
    payload = {
        "query_text": "RAG",
        "feed_author": None,
        "feed_name": None,
        "article_author": None,
        "title_keywords": None,
        "limit": 1,
    }

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        async with AsyncClient(
            transport=transport, base_url="http://testserver"
        ) as client:
            response = await client.post("/search/unique-titles", json=payload)
            assert response.status_code == 200, "Search endpoint did not return 200 OK"
            data = response.json()
            assert "results" in data, "Search response missing 'results' key"
            assert len(data["results"]) == 1
            assert data["results"][0]["title"] == "Test Article One"


@pytest.mark.asyncio
async def test_search_ask():
    """Test /search/ask end-to-end, with the OpenRouter call mocked out so the
    test never depends on a live LLM provider, quota, or network access.
    """
    payload = {"query_text": "RAG", "provider": "openrouter", "limit": 1}
    mocked_generate = AsyncMock(
        return_value=("This is a mocked answer.", "mock-model", "stop")
    )

    async with app.router.lifespan_context(app):
        transport = ASGITransport(app=app)
        with patch(
            "src.api.services.generation_service.generate_openrouter",
            new=mocked_generate,
        ):
            async with AsyncClient(
                transport=transport, base_url="http://testserver"
            ) as client:
                response = await client.post("/search/ask", json=payload)
                assert response.status_code == 200, "Ask endpoint did not return 200 OK"
                data = response.json()
                assert data["answer"] == "This is a mocked answer."
                assert data["model"] == "mock-model"

    mocked_generate.assert_awaited_once()


### ASGI Transport
# You can configure an httpx client to call
# directly into an async Python web application using the ASGI protocol.
