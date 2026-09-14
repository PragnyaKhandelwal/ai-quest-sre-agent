"""
agents/tests/test_log_retriever.py

Unit tests for the TF-IDF semantic log retriever (agents/log_retriever.py).
"""
from __future__ import annotations

from agents.log_retriever import LogRetriever, retrieve_relevant_logs

_CORPUS = [
    "2026-01-01T00:00:00 INFO  payment-service startup complete",
    "2026-01-01T00:01:00 ERROR payment-service OOMKilled after exceeding memory limit",
    "2026-01-01T00:01:05 WARN  payment-service connection pool leak detected, heap growing",
    "2026-01-01T00:02:00 ERROR api-gateway missing environment variable DOWNSTREAM_AUTH_URL",
    "2026-01-01T00:02:05 ERROR api-gateway 502 Bad Gateway auth client not initialized",
    "2026-01-01T00:03:00 WARN  postgres-primary long-running transaction holding row lock",
]


def test_retriever_returns_top_k_results():
    retriever = LogRetriever(_CORPUS)
    results = retriever.retrieve("payment-service memory OOMKilled", top_k=2)
    assert len(results) <= 2
    assert len(results) > 0


def test_empty_corpus_returns_empty_list():
    retriever = LogRetriever([])
    results = retriever.retrieve("anything at all", top_k=5)
    assert results == []


def test_retrieve_falls_back_to_corpus_lines_when_query_has_no_semantic_match():
    # LogRetriever deliberately never starves the agent: if TF-IDF finds
    # nothing above its relevance threshold for a query, it falls back to
    # the first top_k corpus lines (each scored 0.0) rather than returning
    # nothing -- so "no matches" means "no real matches", not "empty list".
    retriever = LogRetriever(_CORPUS)
    results = retriever.retrieve("zzz qqq totally unrelated gibberish xyzzy", top_k=3)
    assert len(results) == 3
    assert all(r["score"] == 0.0 for r in results)


def test_retrieve_scores_are_between_0_and_1():
    retriever = LogRetriever(_CORPUS)
    results = retriever.retrieve("payment-service memory leak OOMKilled", top_k=len(_CORPUS))
    for r in results:
        assert 0.0 <= r["score"] <= 1.0


def test_top_result_is_most_relevant():
    retriever = LogRetriever(_CORPUS)
    results = retriever.retrieve("payment-service OOMKilled memory limit exceeded", top_k=3)
    assert len(results) > 0
    # The top-ranked result should be the line that actually shares the
    # most vocabulary with the query (the OOMKilled line), not an
    # unrelated one like the postgres lock line.
    assert "OOMKilled" in results[0]["log_line"]
    assert results[0]["rank"] == 1
    # Scores should be sorted descending.
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


def test_retrieve_relevant_logs_convenience_function():
    results = retrieve_relevant_logs("api-gateway missing environment variable", _CORPUS, top_k=2)
    assert len(results) <= 2
    assert any("DOWNSTREAM_AUTH_URL" in r["log_line"] for r in results)
