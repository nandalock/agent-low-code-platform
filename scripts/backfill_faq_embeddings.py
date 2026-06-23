"""
补全 FAQ 向量：筛选 embedding 为空的记录，调 bge-m3 填充。
用法：在项目根目录运行  python scripts/backfill_faq_embeddings.py
"""
import asyncio
import logging
import sys

sys.path.insert(0, ".")
from backend.db.connection import get_conn
from backend.rag import embed

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


async def main():
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, question FROM faqs WHERE embedding IS NULL AND is_active = true"
            )
            rows = cur.fetchall()
            if not rows:
                logger.info("没有需要补全的 FAQ")
                return
            logger.info(f"共 {len(rows)} 条需补全")

    total = len(rows)
    success = 0

    for row in rows:
        faq_id = row["id"]
        vec = await embed(row["question"])
        if not vec:
            logger.warning(f"跳过 id={faq_id}: embedding 失败")
            continue

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "UPDATE faqs SET embedding = %s::vector WHERE id = %s",
                    (vec, faq_id),
                )
            conn.commit()
        success += 1
        logger.info(f"[{success}/{total}] id={faq_id} 完成")

    logger.info(f"补全结束: {success}/{total}")


if __name__ == "__main__":
    asyncio.run(main())
