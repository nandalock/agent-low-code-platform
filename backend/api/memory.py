"""记忆系统 API"""
from fastapi import APIRouter, Header, HTTPException

from backend.db.connection import get_conn

router = APIRouter(prefix="/api/memory", tags=["Memory"])


@router.get("/users")
def list_user_profiles(
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> list[dict]:
    """获取所有有画像的用户列表"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT up.*, c.customer_name
                   FROM user_profiles up
                   LEFT JOIN conversations c ON c.tenant_id = up.tenant_id
                     AND c.customer_id = up.user_id
                     AND c.id = (
                       SELECT id FROM conversations
                       WHERE tenant_id = up.tenant_id AND customer_id = up.user_id
                       ORDER BY updated_at DESC LIMIT 1
                     )
                   WHERE up.tenant_id = %s
                   ORDER BY up.updated_at DESC""",
                (x_tenant_id,),
            )
            rows = cur.fetchall()
            return [
                {
                    "user_id": r["user_id"],
                    "customer_name": r.get("customer_name") or r["user_id"],
                    "facts_count": len(r["facts"]),
                    "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
                }
                for r in rows
            ]


@router.get("/users/{user_id}")
def get_user_profile(
    user_id: str,
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    """获取单个用户的画像详情"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM user_profiles WHERE tenant_id = %s AND user_id = %s",
                (x_tenant_id, user_id),
            )
            row = cur.fetchone()
            if not row:
                raise HTTPException(404, "用户画像不存在")
            return {
                "user_id": row["user_id"],
                "facts": row["facts"],
                "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
            }
