"""
agents/log_retriever.py

Semantic log retrieval for the Diagnostician agent.
Uses TF-IDF similarity to find the most relevant log lines for a given
alert/incident summary. This demonstrates RAG-style retrieval quality
without requiring a vector DB -- the Diagnostician agent is handed only
the top-K retrieved lines, not the full corpus, so both the token budget
and the hallucination surface area shrink together.
"""
from __future__ import annotations

import logging

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.metrics.pairwise import cosine_similarity

logger = logging.getLogger(__name__)


class LogRetriever:
    """
    Retrieves semantically relevant log lines for a given query.
    Simulates RAG-style retrieval over the log corpus.
    """

    def __init__(self, log_corpus: list[str]):
        self.corpus = log_corpus
        self.vectorizer = TfidfVectorizer(
            ngram_range=(1, 2),
            max_features=500,
            stop_words="english",
        )
        if log_corpus:
            self.tfidf_matrix = self.vectorizer.fit_transform(log_corpus)
        else:
            self.tfidf_matrix = None

    def retrieve(self, query: str, top_k: int = 10) -> list[dict]:
        """
        Retrieve top_k most relevant log lines for the query.
        Returns list of {log_line, score, rank} dicts.
        """
        if self.tfidf_matrix is None or not query:
            return [
                {"log_line": line, "score": 1.0, "rank": i}
                for i, line in enumerate(self.corpus[:top_k])
            ]

        query_vec = self.vectorizer.transform([query])
        similarities = cosine_similarity(query_vec, self.tfidf_matrix)[0]
        top_indices = np.argsort(similarities)[::-1][:top_k]

        results = []
        for rank, idx in enumerate(top_indices):
            if similarities[idx] > 0.01:  # Minimum relevance threshold
                results.append(
                    {
                        "log_line": self.corpus[idx],
                        "score": float(similarities[idx]),
                        "rank": rank + 1,
                    }
                )

        # Fallback: if TF-IDF found nothing above threshold (e.g. a very
        # short/generic query), still hand the diagnostician *something*
        # rather than an empty corpus -- never silently starve the agent.
        if not results:
            results = [
                {"log_line": line, "score": 0.0, "rank": i + 1}
                for i, line in enumerate(self.corpus[:top_k])
            ]

        logger.info(f"Retrieved {len(results)} relevant log lines for query: '{query[:50]}...'")
        return results


def retrieve_relevant_logs(alert_description: str, log_corpus: list[str], top_k: int = 15) -> list[dict]:
    """Convenience function for one-shot retrieval."""
    retriever = LogRetriever(log_corpus)
    return retriever.retrieve(alert_description, top_k=top_k)
