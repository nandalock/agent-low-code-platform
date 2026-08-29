import csv
import io
import logging

from backend.core.connection import get_conn
from backend.services.faq.models import FAQImportResult

logger = logging.getLogger(__name__)


def create_faq(tenant_id: int, question: str, answer: str, tags: list[str], vectorize: bool = True) -> dict:
    embedding = None
    if vectorize:
        embedding = _embed_sync(question)

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO faqs (tenant_id, question, answer, tags, embedding)
                   VALUES (%s, %s, %s, %s, %s::vector) RETURNING *""",
                (tenant_id, question, answer, tags, embedding),
            )
            return dict(cur.fetchone())


def _embed_sync(text: str) -> list[float] | None:
    """同步调用 Ollama embedding"""
    import json
    import os
    from urllib.request import Request, urlopen

    ollama_url = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    try:
        body = json.dumps({"model": "bge-m3", "input": text}).encode()
        req = Request(f"{ollama_url}/api/embed", data=body, headers={"Content-Type": "application/json"})
        with urlopen(req, timeout=60) as resp:
            data = json.loads(resp.read())
            embeddings = data.get("embeddings", [])
            if embeddings:
                return embeddings[0]
    except Exception:
        return None


def get_faq(tenant_id: int, faq_id: int) -> dict | None:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM faqs WHERE id = %s AND tenant_id = %s",
                (faq_id, tenant_id),
            )
            row = cur.fetchone()
            return dict(row) if row else None


def update_faq(tenant_id: int, faq_id: int, **kwargs) -> dict | None:
    allowed = {"question", "answer", "tags", "is_active"}
    updates = {k: v for k, v in kwargs.items() if k in allowed and v is not None}
    if not updates:
        return get_faq(tenant_id, faq_id)

    set_clause = ", ".join(f"{k} = %s" for k in updates)
    values = list(updates.values()) + [faq_id, tenant_id]

    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"UPDATE faqs SET {set_clause}, updated_at = now() WHERE id = %s AND tenant_id = %s RETURNING *",
                values,
            )
            row = cur.fetchone()
            return dict(row) if row else None


def delete_faq(tenant_id: int, faq_id: int) -> bool:
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM faqs WHERE id = %s AND tenant_id = %s",
                (faq_id, tenant_id),
            )
            return cur.rowcount > 0


def list_faqs(tenant_id: int, tag: str = "", page: int = 1, size: int = 20) -> tuple[list[dict], int]:
    with get_conn() as conn:
        with conn.cursor() as cur:
            if tag:
                cur.execute(
                    "SELECT count(*) FROM faqs WHERE tenant_id = %s AND %s = ANY(tags)",
                    (tenant_id, tag),
                )
            else:
                cur.execute("SELECT count(*) FROM faqs WHERE tenant_id = %s", (tenant_id,))
            total = cur.fetchone()["count"]

            offset = (page - 1) * size
            if tag:
                cur.execute(
                    """SELECT * FROM faqs WHERE tenant_id = %s AND %s = ANY(tags)
                       ORDER BY updated_at DESC LIMIT %s OFFSET %s""",
                    (tenant_id, tag, size, offset),
                )
            else:
                cur.execute(
                    "SELECT * FROM faqs WHERE tenant_id = %s ORDER BY updated_at DESC LIMIT %s OFFSET %s",
                    (tenant_id, size, offset),
                )
            rows = [dict(r) for r in cur.fetchall()]
            return rows, total


def match_faq(tenant_id: int, question: str, direct_threshold: float = 0.95) -> dict | None:
    """pg_trgm 匹配，score >= direct_threshold 返回 direct"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT *, similarity(question, %s) AS _score
                   FROM faqs
                   WHERE tenant_id = %s AND is_active = true
                     AND similarity(question, %s) > 0.3
                   ORDER BY _score DESC LIMIT 1""",
                (question, tenant_id, question),
            )
            row = cur.fetchone()
            if row:
                result = dict(row)
                if result.get("_score", 0) >= direct_threshold:
                    result["_tier"] = "direct"
                    return result
    return None


def vector_search(tenant_id: int, query_vector: list[float], top_n: int = 10) -> list[dict]:
    """pgvector 余弦距离搜索，返回 top-N"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT *, 1 - (embedding <=> %s::vector) AS _score
                   FROM faqs
                   WHERE tenant_id = %s AND is_active = true AND embedding IS NOT NULL
                   ORDER BY embedding <=> %s::vector
                   LIMIT %s""",
                (query_vector, tenant_id, query_vector, top_n),
            )
            return [dict(r) for r in cur.fetchall()]


def import_csv(tenant_id: int, file_content: bytes) -> FAQImportResult:
    reader = csv.DictReader(io.StringIO(file_content.decode("utf-8-sig")))
    required = {"question", "answer"}
    if not required.issubset(set(reader.fieldnames or [])):
        return FAQImportResult(
            total=0, success=0, errors=["CSV 缺少必需列: question, answer"]
        )

    total = 0
    success = 0
    errors: list[str] = []

    with get_conn() as conn:
        for i, row in enumerate(reader, start=1):
            total += 1
            q = (row.get("question") or "").strip()
            a = (row.get("answer") or "").strip()
            if not q or not a:
                errors.append(f"行 {i}: question 或 answer 为空")
                continue
            tags_raw = (row.get("tags") or "").strip()
            tags = [t.strip() for t in tags_raw.split(",") if t.strip()] if tags_raw else []

            with conn.cursor() as cur:
                cur.execute(
                    "INSERT INTO faqs (tenant_id, question, answer, tags) VALUES (%s, %s, %s, %s)",
                    (tenant_id, q, a, tags),
                )
            success += 1

    return FAQImportResult(total=total, success=success, errors=errors)


def vectorize_faq(tenant_id: int, faq_id: int) -> tuple[bool, str]:
    """单条 FAQ 向量化"""
    faq = get_faq(tenant_id, faq_id)
    if not faq:
        return False, "FAQ 不存在"
    vec = _embed_sync(faq["question"])
    if not vec:
        return False, "embedding 失败"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE faqs SET embedding = %s::vector, updated_at = now() WHERE id = %s AND tenant_id = %s",
                (vec, faq_id, tenant_id),
            )
        conn.commit()
    return True, ""


def backfill_embeddings(tenant_id: int) -> dict:
    """补全所有向量为空的 FAQ"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, question FROM faqs WHERE tenant_id = %s AND embedding IS NULL AND is_active = true",
                (tenant_id,),
            )
            rows = cur.fetchall()

    total = len(rows)
    success = 0
    errors: list[str] = []

    for row in rows:
        vec = _embed_sync(row["question"])
        if not vec:
            errors.append(f"id={row['id']}: embedding 失败")
            continue
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE faqs SET embedding = %s::vector, updated_at = now() WHERE id = %s",
                    (vec, row["id"]),
                )
            conn.commit()
        success += 1

    return {"total": total, "success": success, "errors": errors}
