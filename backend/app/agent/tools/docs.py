from __future__ import annotations

import asyncio
import html
import json
import math
import re
import time
from hashlib import blake2b
from typing import Any
from urllib.parse import quote, urlencode
from urllib.request import urlopen

from ...config import (
    DEMO_USER_ID,
    DOC_CHUNK_OVERLAP,
    DOC_CHUNK_SIZE,
    DOC_SEARCH_CACHE_MAX_ITEMS,
    DOC_SEARCH_CACHE_TTL_SEC,
    DOC_VECTOR_DIMS,
    ENABLE_EXTERNAL_SEARCH,
    EXTERNAL_SEARCH_MAX_ITEMS,
    EXTERNAL_SEARCH_PROVIDER,
)
from ...db import conn_ctx, get_conn

_CACHE: dict[tuple[int, int, int, int, bool], tuple[float, dict[str, Any]]] = {}
_CACHE_LOCK = asyncio.Lock()
_DOCUMENTS_VERSION = 0
_STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "before",
    "can",
    "do",
    "for",
    "from",
    "how",
    "i",
    "if",
    "in",
    "is",
    "it",
    "me",
    "of",
    "on",
    "or",
    "should",
    "tell",
    "the",
    "this",
    "to",
    "what",
    "with",
}


def _tokenize(query: str) -> list[str]:
    tokens: list[str] = []
    seen: set[str] = set()
    for raw in query.lower().replace("/", " ").replace("-", " ").split():
        token = "".join(ch for ch in raw if ch.isalnum())
        if len(token) < 3 or token in _STOPWORDS or token in seen:
            continue
        seen.add(token)
        tokens.append(token)
    return tokens


def _stable_index(token: str, salt: str = "") -> int:
    digest = blake2b(f"{salt}:{token}".encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big")


def embed_text(text: str, dims: int = DOC_VECTOR_DIMS) -> list[float]:
    vector = [0.0] * max(8, dims)
    tokens = _tokenize(text)
    if not tokens:
        return vector
    for token in tokens:
        slot = _stable_index(token) % len(vector)
        sign = -1.0 if _stable_index(token, salt="sign") % 2 else 1.0
        weight = 1.6 if len(token) > 7 else 1.0
        vector[slot] += sign * weight
    norm = math.sqrt(sum(value * value for value in vector))
    if norm <= 1e-9:
        return vector
    return [round(value / norm, 6) for value in vector]


def _cosine_similarity(left: list[float], right: list[float]) -> float:
    if not left or not right or len(left) != len(right):
        return 0.0
    return sum(a * b for a, b in zip(left, right))


def _chunk_text(text: str, *, chunk_size: int = DOC_CHUNK_SIZE, overlap: int = DOC_CHUNK_OVERLAP) -> list[str]:
    words = text.split()
    if not words:
        return []
    size = max(40, chunk_size)
    step = max(10, size - max(0, overlap))
    chunks: list[str] = []
    start = 0
    while start < len(words):
        piece = " ".join(words[start : start + size]).strip()
        if piece:
            chunks.append(piece)
        if start + size >= len(words):
            break
        start += step
    return chunks or [text.strip()]


def _score(query: str, title: str, content: str, tags: list[str]) -> float:
    tokens = _tokenize(query)
    haystack = f"{title} {content} {' '.join(tags)}".lower()
    title_lower = title.lower()
    tag_text = " ".join(tags).lower()
    score = 0.0
    for token in tokens:
        if token in title_lower:
            score += 5.0
        if token in tag_text:
            score += 3.0
        if token in haystack:
            score += 1.25
    return score


def _snippet(content: str, query: str) -> str:
    if not content:
        return ""
    lower = content.lower()
    needle = query.lower().strip()
    if needle and needle in lower:
        idx = lower.index(needle)
        start = max(0, idx - 80)
        end = min(len(content), idx + max(len(query), 120))
        return content[start:end].strip()
    for token in _tokenize(query):
        if token in lower:
            idx = lower.index(token)
            start = max(0, idx - 80)
            end = min(len(content), idx + 180)
            return content[start:end].strip()
    return content[:220].strip()


def bump_documents_version() -> None:
    global _DOCUMENTS_VERSION
    _DOCUMENTS_VERSION += 1
    _CACHE.clear()


def index_document_sync(*, document_id: int, user_id: int, title: str, text: str) -> None:
    chunks = _chunk_text(text)
    with conn_ctx() as conn:
        conn.execute("DELETE FROM document_chunks WHERE document_id = ?", (document_id,))
        for index, chunk in enumerate(chunks):
            conn.execute(
                """
                INSERT INTO document_chunks
                  (user_id, document_id, title, chunk_index, chunk_text, token_count, embedding_json)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    document_id,
                    title,
                    index,
                    chunk,
                    len(chunk.split()),
                    json.dumps(embed_text(chunk)),
                ),
            )


def rebuild_document_indexes_sync(*, user_id: int = DEMO_USER_ID) -> None:
    conn = get_conn()
    try:
        rows = conn.execute(
            """
            SELECT id, title, text_content
            FROM documents
            WHERE user_id = ?
            ORDER BY id
            """,
            (user_id,),
        ).fetchall()
    finally:
        conn.close()
    for row in rows:
        index_document_sync(
            document_id=row["id"],
            user_id=user_id,
            title=row["title"],
            text=row["text_content"] or "",
        )
    bump_documents_version()


def _search_documents_sync(query: str, limit: int, user_id: int) -> dict[str, Any]:
    conn = get_conn()
    tokens = _tokenize(query)
    fts_query = " OR ".join(tokens[:8]) if tokens else query
    query_embedding = embed_text(query)
    try:
        rows = conn.execute(
            """
            SELECT c.id, c.document_id, c.title, c.chunk_text, c.embedding_json, c.token_count,
                   d.summary, d.source_path, d.tags_json,
                   bm25(document_chunks_fts) AS fts_score
            FROM document_chunks_fts
            JOIN document_chunks c ON c.id = document_chunks_fts.rowid
            JOIN documents d ON d.id = c.document_id
            WHERE document_chunks_fts MATCH ? AND c.user_id = ?
            ORDER BY fts_score
            LIMIT ?
            """,
            (fts_query or query, user_id, max(limit * 6, 12)),
        ).fetchall()
    except Exception:
        rows = []
    finally:
        conn.close()

    ranked_by_doc: dict[int, dict[str, Any]] = {}
    for row in rows:
        chunk_text = row["chunk_text"] or ""
        vector = json.loads(row["embedding_json"] or "[]")
        similarity = _cosine_similarity(query_embedding, vector)
        fts_component = round(float(-row["fts_score"]), 4) if row["fts_score"] is not None else 0.0
        lexical = _score(query, row["title"], chunk_text, json.loads(row["tags_json"] or "[]"))
        total = round((fts_component * 0.45) + (similarity * 4.0) + lexical, 4)
        current = ranked_by_doc.get(row["document_id"])
        candidate = {
            "id": row["document_id"],
            "title": row["title"],
            "summary": row["summary"],
            "source_path": row["source_path"],
            "snippet": _snippet(chunk_text, query),
            "score": total,
            "source": "local_hybrid",
        }
        if current is None or candidate["score"] > current["score"]:
            ranked_by_doc[row["document_id"]] = candidate

    if not ranked_by_doc:
        conn = get_conn()
        try:
            rows = conn.execute(
                """
                SELECT id, title, source_path, summary, text_content, tags_json
                FROM documents
                WHERE user_id = ?
                ORDER BY created_at DESC
                """,
                (user_id,),
            ).fetchall()
        finally:
            conn.close()
        for row in rows:
            tags = json.loads(row["tags_json"] or "[]")
            lexical = _score(query, row["title"], row["text_content"], tags)
            if lexical <= 0 and query.strip():
                continue
            ranked_by_doc[row["id"]] = {
                "id": row["id"],
                "title": row["title"],
                "summary": row["summary"],
                "source_path": row["source_path"],
                "snippet": _snippet(row["text_content"], query),
                "score": round(lexical, 4),
                "source": "local_scan",
            }

    ranked = sorted(ranked_by_doc.values(), key=lambda item: item["score"], reverse=True)
    return {
        "source": "hybrid_local",
        "query": query,
        "items": ranked[:limit],
    }


async def _search_wikipedia(query: str, limit: int) -> list[dict[str, Any]]:
    if EXTERNAL_SEARCH_PROVIDER != "wikipedia":
        return []
    params = urlencode(
        {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "format": "json",
            "srlimit": min(limit, EXTERNAL_SEARCH_MAX_ITEMS),
        }
    )

    def _fetch() -> dict[str, Any]:
        with urlopen(f"https://en.wikipedia.org/w/api.php?{params}", timeout=5.0) as response:
            return json.loads(response.read().decode("utf-8"))

    payload = await asyncio.to_thread(_fetch)
    results: list[dict[str, Any]] = []
    for item in payload.get("query", {}).get("search", [])[:limit]:
        title = str(item.get("title", "")).strip()
        snippet = html.unescape(re.sub(r"<[^>]+>", "", str(item.get("snippet", "")))).strip()
        if not title:
            continue
        results.append(
            {
                "id": f"wiki:{title}",
                "title": title,
                "summary": "Wikipedia external search result",
                "snippet": snippet,
                "score": round(0.9 - (0.05 * len(results)), 4),
                "source_path": f"https://en.wikipedia.org/wiki/{quote(title.replace(' ', '_'))}",
                "source": "external_wikipedia",
            }
        )
    return results


async def search_documents(
    query: str,
    limit: int = 3,
    user_id: int = DEMO_USER_ID,
    *,
    include_external: bool = False,
) -> dict[str, Any]:
    normalized_query = " ".join(query.split()).strip()
    cache_key = (user_id, limit, _DOCUMENTS_VERSION, hash(normalized_query.lower()), include_external)
    now = time.monotonic()

    async with _CACHE_LOCK:
        cached = _CACHE.get(cache_key)
        if cached and now - cached[0] <= DOC_SEARCH_CACHE_TTL_SEC:
            result = dict(cached[1])
            result["source"] = f"{result.get('source', 'cache')}:cache"
            return result

    local_result = await asyncio.to_thread(_search_documents_sync, normalized_query, limit, user_id)
    items = list(local_result["items"])
    if include_external and ENABLE_EXTERNAL_SEARCH and len(items) < limit:
        try:
            external_items = await _search_wikipedia(normalized_query, limit - len(items))
        except Exception:
            external_items = []
        if external_items:
            items.extend(external_items)

    result = {
        "source": local_result["source"],
        "query": normalized_query,
        "items": items[:limit],
    }

    async with _CACHE_LOCK:
        if len(_CACHE) >= DOC_SEARCH_CACHE_MAX_ITEMS:
            oldest_key = min(_CACHE.items(), key=lambda item: item[1][0])[0]
            _CACHE.pop(oldest_key, None)
        _CACHE[cache_key] = (now, result)
    return result
