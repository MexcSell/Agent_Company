"""Fixed research-retrieval benchmark (advanced_self_improving_agent_guide §9, §18).

Measures `learning.retrieve` of whatever `agent_company` is first on PYTHONPATH (the self-evolution runner
points it at the baseline or the candidate source tree) on a fixed corpus of knowledge items and a fixed set
of queries, one group per query class of §9:

    exact      an exact function or file name        concept    a technical concept in other words
    recent     the newest finding on a topic         document   one specific document or source
    complex    a research question spanning topics

Prints one JSON line: {"benchmark", "source", "metrics": {...}, "classes": {...}, "cases": [...]}.
Metrics: hit_at_3 (a relevant item in the top 3), mrr, precision_at_5, duplicate_rate (results that repeat a
higher-ranked one), latency_ms (median per query). Runs offline in a fresh DATA_DIR; never edit it to make a
candidate pass — the runner uses the copy taken when the contract was created.
"""

from __future__ import annotations

import json
import os
import statistics
import sys
import tempfile
import time

# (key, text, technologies); later items are newer
CORPUS = [
    ("retrieve_fn", "learning.retrieve ranks knowledge items by keyword overlap, author and recency", ["python"]),
    ("retrieve_dup", "learning.retrieve ranks knowledge items by keyword overlap, author and recency.", ["python"]),
    ("sched_dag", "scheduler.run_dag starts a task once all its dependencies are done", ["python"]),
    ("sched_lock", "Two writers never edit one worktree at once: run_dag takes the write lock", ["python"]),
    ("vector_old", "Vector search with embeddings finds semantically similar passages (2024 survey)", ["embeddings"]),
    ("vector_new", "Hybrid search combining BM25 keywords and embeddings beats either alone (2026 benchmark)",
     ["embeddings", "bm25"]),
    ("bm25", "BM25 is a bag-of-words ranking function that weights rare terms higher", ["bm25"]),
    ("cache_lru", "An LRU cache in front of the search API removed repeated identical queries", ["cache"]),
    ("cache_dup", "An LRU cache in front of the search API removed repeated identical queries!", ["cache"]),
    ("dedupe", "Near-duplicate documents are detected with MinHash shingles before indexing", ["minhash"]),
    ("rewrite", "Query rewriting expands abbreviations and synonyms before the search runs", ["nlp"]),
    ("sqlite_fts", "SQLite FTS5 gives full-text search with bm25() ranking inside the database", ["sqlite", "bm25"]),
    ("pg_index", "A GIN index on a tsvector column makes PostgreSQL text search fast", ["postgresql"]),
    ("crawl_robots", "The crawler honours robots.txt and refuses private network addresses", ["http"]),
    ("rfc9110", "RFC 9110 defines HTTP semantics: methods, status codes and caching headers", ["http"]),
    ("adr_0007", "ADR-0007 chose SQLite with one writer over PostgreSQL for the control plane", ["sqlite"]),
    ("retry_backoff", "Transient 429 and 5xx errors are retried with exponential backoff", ["http"]),
    ("freshness_old", "Release notes 2025: the crawler revisits sources weekly", ["crawler"]),
    ("freshness_new", "Release notes 2026: the crawler revisits each source on its own monitoring interval",
     ["crawler"]),
    ("token_budget", "Trimming retrieved context to the token budget cut cost by a third", ["llm"]),
    ("rerank", "A cross-encoder reranker reorders the top 50 keyword hits by semantic relevance",
     ["embeddings", "reranker"]),
    ("noise_1", "The dashboard rail order is shared by all users", ["javascript"]),
    ("noise_2", "Machines run as Docker containers with a noVNC console", ["docker"]),
    ("noise_3", "Topics are conversation lanes inside a project", ["product"]),
]

# (query class, query, relevant keys)
QUERIES = [
    ("exact", "learning.retrieve", ["retrieve_fn", "retrieve_dup"]),
    ("exact", "run_dag", ["sched_dag", "sched_lock"]),
    ("exact", "bm25()", ["sqlite_fts", "bm25"]),
    ("concept", "semantic similarity search over passages", ["vector_old", "vector_new", "rerank"]),
    ("concept", "avoid running the same query twice", ["cache_lru", "cache_dup"]),
    ("concept", "finding near duplicate documents", ["dedupe"]),
    ("concept", "expanding synonyms in a user's question", ["rewrite"]),
    ("recent", "latest hybrid search benchmark", ["vector_new"]),
    ("recent", "newest crawler release notes", ["freshness_new"]),
    ("document", "RFC 9110", ["rfc9110"]),
    ("document", "ADR-0007", ["adr_0007"]),
    ("complex", "how should we rank research results combining keywords embeddings and reranking",
     ["vector_new", "rerank", "bm25", "sqlite_fts"]),
    ("complex", "reduce cost and redundant searches in the research agent", ["cache_lru", "token_budget", "dedupe"]),
]


def _near_dup(a: str, b: str) -> bool:
    norm = lambda t: " ".join("".join(c for c in t.lower() if c.isalnum() or c.isspace()).split())
    return norm(a) == norm(b)


def main() -> None:
    os.environ["DATA_DIR"] = os.environ.get("BENCH_DATA_DIR") or tempfile.mkdtemp(prefix="bench-retrieval-")
    import agent_company
    from agent_company import learning
    from agent_company.db import Agent, Lesson, session

    with session() as s:
        agent = Agent(name="Bench Researcher", role="researcher", title="Researcher", room_key="research")
        s.add(agent)
        s.flush()
        agent_id = agent.id
        ids: dict[str, int] = {}
        for key, text, tech in CORPUS:
            item = Lesson(agent_id=agent_id, text=text, outcome="success", kind="semantic",
                          technologies=tech, status="approved", classification="internal")
            s.add(item)
            s.flush()
            ids[key] = item.id
    by_id = {v: k for k, v in ids.items()}
    text_of = {k: t for k, t, _ in CORPUS}

    cases, latencies = [], []
    for cls, query, relevant in QUERIES:
        started = time.perf_counter()
        got = learning.retrieve(agent_id, query=query, limit=5)
        latencies.append((time.perf_counter() - started) * 1000)
        keys = [by_id.get(x["id"], "?") for x in got]
        rank = next((i + 1 for i, k in enumerate(keys) if k in relevant), None)
        dups = sum(1 for i, k in enumerate(keys) if any(_near_dup(text_of.get(k, ""), text_of.get(j, ""))
                                                       for j in keys[:i]))
        cases.append({"class": cls, "query": query, "results": keys, "relevant": relevant,
                      "hit_at_3": bool(rank and rank <= 3), "rr": 1 / rank if rank else 0.0,
                      "precision_at_5": sum(k in relevant for k in keys) / 5, "duplicates": dups,
                      "returned": len(keys)})

    def mean(xs: list[float]) -> float:
        return round(sum(xs) / len(xs), 4) if xs else 0.0

    returned = sum(c["returned"] for c in cases)
    metrics = {"hit_at_3": mean([c["hit_at_3"] for c in cases]), "mrr": mean([c["rr"] for c in cases]),
               "precision_at_5": mean([c["precision_at_5"] for c in cases]),
               "duplicate_rate": round(sum(c["duplicates"] for c in cases) / returned, 4) if returned else 0.0,
               "latency_ms": round(statistics.median(latencies), 3)}
    classes = {cls: {"hit_at_3": mean([c["hit_at_3"] for c in cases if c["class"] == cls]),
                     "mrr": mean([c["rr"] for c in cases if c["class"] == cls])}
               for cls in dict.fromkeys(c["class"] for c in cases)}
    print(json.dumps({"benchmark": "retrieval", "source": os.path.dirname(agent_company.__file__),
                      "metrics": metrics, "classes": classes, "cases": cases}))


if __name__ == "__main__":
    sys.exit(main())
