"""MCP Server — FastMCP 暴露数据库查询工具"""
import logging
from mcp.server.fastmcp import FastMCP
from backend.db.connection import get_conn

logger = logging.getLogger(__name__)
mcp = FastMCP("multi-agent-customer-service-mcp")


# ═══ 订单域 ═══

@mcp.tool()
def query_orders(tenant_id: int, phone: str | None = None, order_no: str | None = None, status: str | None = None) -> list[dict]:
    """按手机号、订单号或状态查询订单列表。所有可选参数不传则不做限制，返回最近50笔订单。"""
    sql = """SELECT id, order_no, customer_name, customer_phone, status, payment_status, total_amount, created_at
FROM orders WHERE tenant_id = %s AND (%s IS NULL OR customer_phone = %s) AND (%s IS NULL OR order_no = %s) AND (%s IS NULL OR status = %s)
ORDER BY created_at DESC LIMIT 50"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [tenant_id, phone, phone, order_no, order_no, status, status])
            return [dict(r) for r in cur.fetchall()]


@mcp.tool()
def get_order_detail(order_id: int, tenant_id: int) -> list[dict]:
    """获取单笔订单完整信息，包含商品明细和物流记录。"""
    sql = """SELECT o.id, o.order_no, o.customer_name, o.customer_phone, o.status, o.payment_status, o.total_amount, o.created_at,
       COALESCE(json_agg(DISTINCT jsonb_build_object('product', oi.product_name, 'sku', oi.product_sku, 'qty', oi.quantity, 'price', oi.unit_price)) FILTER (WHERE oi.id IS NOT NULL), '[]') AS items,
       COALESCE(json_agg(DISTINCT jsonb_build_object('tracking_no', l.tracking_no, 'carrier', l.carrier, 'status', l.status)) FILTER (WHERE l.id IS NOT NULL), '[]') AS logistics
FROM orders o LEFT JOIN order_items oi ON oi.order_id = o.id LEFT JOIN logistics l ON l.order_id = o.id
WHERE o.tenant_id = %s AND o.id = %s GROUP BY o.id"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [tenant_id, order_id])
            return [dict(r) for r in cur.fetchall()]


@mcp.tool()
def track_logistics(tracking_no: str) -> list[dict]:
    """按运单号查询物流轨迹时间线，返回运单信息和所有轨迹事件。"""
    sql = """SELECT l.tracking_no, l.carrier, l.status AS logistics_status, l.est_delivery_date,
       COALESCE(json_agg(jsonb_build_object('time', lt.recorded_at, 'location', lt.location, 'description', lt.description) ORDER BY lt.recorded_at) FILTER (WHERE lt.id IS NOT NULL), '[]') AS events
FROM logistics l LEFT JOIN logistics_tracking lt ON lt.logistics_id = l.id WHERE l.tracking_no = %s GROUP BY l.id"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [tracking_no])
            return [dict(r) for r in cur.fetchall()]


# ═══ 知识库域 ═══

@mcp.tool()
def search_faqs(query: str, tenant_id: int, top_n: int = 5) -> list[dict]:
    """全文搜索FAQ知识库，返回匹配的问答对及相似度分数。"""
    sql = "SELECT id, question, answer, tags, similarity(question, %s) AS score FROM faqs WHERE tenant_id = %s AND is_active = true AND question %% %s ORDER BY score DESC LIMIT %s"
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, [query, tenant_id, query, top_n])
            return [dict(r) for r in cur.fetchall()]


@mcp.tool()
def list_faq_tags(tenant_id: int) -> list[dict]:
    """列出知识库中所有使用的标签去重列表。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT DISTINCT unnest(tags) AS tag FROM faqs WHERE tenant_id = %s AND tags IS NOT NULL", [tenant_id])
            return [dict(r) for r in cur.fetchall()]


# ═══ 用户域 ═══

@mcp.tool()
def get_user_profile(user_id: str, tenant_id: int) -> list[dict]:
    """查询用户画像，返回历史记忆和偏好信息。"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT tenant_id, user_id, facts, updated_at FROM user_profiles WHERE tenant_id = %s AND user_id = %s", [tenant_id, user_id])
            return [dict(r) for r in cur.fetchall()]
