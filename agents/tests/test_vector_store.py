"""
agents/tests/test_vector_store.py

Unit tests for the semantic log retrieval store (agents/vector_store.py).
Written to be backend-agnostic (assert on stats()["backend"]/[retrieval_method]
being one of "chromadb"/"tfidf_fallback", never a specific one) since
ChromaDB + sentence-transformers are optional, install-your-own dependencies
(see the module's own docstring for why they aren't pinned in
backend/requirements.txt) -- this suite exercises the real code either way.
"""
from agents.vector_store import SREVectorStore, create_incident_store


def test_vector_store_creates():
    store = SREVectorStore("test-collection")
    assert store is not None
    assert store.stats() is not None


def test_add_and_search_logs():
    store = SREVectorStore("test-search")
    logs = [
        "ERROR: OOMKilled pod payment-service memory exceeded",
        "WARN: heap usage at 95% in payment-service",
        "INFO: scheduled backup completed successfully",
        "ERROR: connection pool exhausted payment-service",
        "INFO: health check passed api-gateway",
    ]
    store.add_logs(logs, metadata={"service": "payment-service"})
    results = store.search("memory OOMKilled heap payment", top_k=3)
    assert len(results) > 0
    assert all("log_line" in r for r in results)
    assert all("score" in r for r in results)
    assert all(0.0 <= r["score"] <= 1.0 for r in results)


def test_search_returns_relevant_results():
    store = SREVectorStore("test-relevance")
    logs = [
        "ERROR: deadlock detected on orders table row locks",
        "WARN: query timeout exceeded 30 seconds",
        "INFO: pod nginx-ingress started successfully",
    ]
    store.add_logs(logs)
    results = store.search("database deadlock query timeout", top_k=3)
    assert len(results) > 0
    top_result = results[0]["log_line"].lower()
    assert any(kw in top_result for kw in ["deadlock", "timeout", "query"])


def test_search_empty_store():
    store = SREVectorStore("test-empty")
    results = store.search("anything", top_k=5)
    assert isinstance(results, list)


def test_search_empty_query_returns_empty():
    store = SREVectorStore("test-empty-query")
    store.add_logs(["some log line"])
    assert store.search("", top_k=5) == []


def test_vector_store_stats():
    store = SREVectorStore("test-stats")
    stats = store.stats()
    assert "backend" in stats
    assert "document_count" in stats
    assert "model" in stats
    assert stats["backend"] in ["chromadb", "tfidf_fallback"]


def test_stats_document_count_reflects_added_logs():
    store = SREVectorStore("test-count")
    store.add_logs(["line one", "line two", "line three"])
    assert store.stats()["document_count"] == 3


def test_create_incident_store():
    store = create_incident_store("inc_test12345678")
    assert store is not None
    assert "inc-" in store.collection_name


def test_retrieval_method_in_results():
    store = SREVectorStore("test-method")
    logs = ["ERROR: test log line for method verification"]
    store.add_logs(logs)
    results = store.search("test log", top_k=1)
    if results:
        assert "retrieval_method" in results[0]
        assert results[0]["retrieval_method"] in ["vector_cosine", "tfidf_fallback"]


def test_add_logs_empty_list_returns_zero():
    store = SREVectorStore("test-empty-add")
    assert store.add_logs([]) == 0


def test_clear_resets_the_store():
    store = SREVectorStore("test-clear")
    store.add_logs(["a log line", "another log line"])
    assert store.stats()["document_count"] == 2
    store.clear()
    assert store.stats()["document_count"] == 0
