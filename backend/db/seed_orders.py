"""订单&物流 示例数据（tenant_id=1）

覆盖订单 Agent 的典型查询场景：
  - 已发货快递追踪
  - 待支付/待确认
  - 已取消/退货退款
  - 紧急催单
"""
import logging
from datetime import datetime, timedelta, timezone

from backend.db.connection import get_conn

logger = logging.getLogger(__name__)

# ── 示例数据 ──

ORDERS = [
    # id=1: 已签收（顺丰，完整轨迹）
    {"order_no": "ORD20250615001", "customer_name": "张三", "customer_phone": "13800001001",
     "status": "delivered", "payment_status": "paid", "payment_method": "微信支付",
     "total_amount": 299.00, "discount_amount": 0, "shipping_fee": 0,
     "customer_note": "发顺丰", "priority": "normal", "delivered_at": "2025-06-18 14:30:00+08"},

    # id=2: 运输中（中通，有轨迹）
    {"order_no": "ORD20250623002", "customer_name": "李四", "customer_phone": "13900002002",
     "status": "shipped", "payment_status": "paid", "payment_method": "支付宝",
     "total_amount": 1580.00, "discount_amount": 120, "shipping_fee": 15,
     "customer_note": "", "priority": "high", "delivered_at": None},

    # id=3: 待支付
    {"order_no": "ORD20250627003", "customer_name": "王五", "customer_phone": "13700003003",
     "status": "pending", "payment_status": "pending", "payment_method": "",
     "total_amount": 89.90, "discount_amount": 0, "shipping_fee": 8,
     "customer_note": "请尽快发货", "priority": "normal", "delivered_at": None},

    # id=4: 已取消（超时未支付）
    {"order_no": "ORD20250620004", "customer_name": "赵六", "customer_phone": "13600004004",
     "status": "cancelled", "payment_status": "pending", "payment_method": "",
     "total_amount": 456.00, "discount_amount": 30, "shipping_fee": 0,
     "customer_note": "", "priority": "normal", "delivered_at": None},

    # id=5: 退货中（买家不喜欢）
    {"order_no": "ORD20250610005", "customer_name": "孙七", "customer_phone": "13500005005",
     "status": "returning", "payment_status": "refunding", "payment_method": "微信支付",
     "total_amount": 199.00, "discount_amount": 0, "shipping_fee": 10,
     "customer_note": "七天无理由退货", "priority": "high", "delivered_at": None},

    # id=6: 已退货退款完成
    {"order_no": "ORD20250605006", "customer_name": "周八", "customer_phone": "13400006006",
     "status": "returned", "payment_status": "refunded", "payment_method": "支付宝",
     "total_amount": 68.00, "discount_amount": 0, "shipping_fee": 0,
     "customer_note": "", "priority": "normal", "delivered_at": None},

    # id=7: 待处理（客户备注要求改地址）
    {"order_no": "ORD20250628007", "customer_name": "吴九", "customer_phone": "13300007007",
     "status": "confirmed", "payment_status": "paid", "payment_method": "微信支付",
     "total_amount": 2360.00, "discount_amount": 200, "shipping_fee": 0,
     "customer_note": "地址写错了，麻烦改成：北京市朝阳区望京SOHO T3 1508", "priority": "urgent", "delivered_at": None},

    # id=8: 运输中（京东，多仓发货）
    {"order_no": "ORD20250624008", "customer_name": "张三", "customer_phone": "13800001001",
     "status": "shipped", "payment_status": "paid", "payment_method": "白条",
     "total_amount": 5999.00, "discount_amount": 500, "shipping_fee": 0,
     "customer_note": "送礼用，麻烦包装好一点", "priority": "high", "delivered_at": None},

    # id=9: 已确认待处理（催单标记）
    {"order_no": "ORD20250626009", "customer_name": "郑十", "customer_phone": "13200008008",
     "status": "processing", "payment_status": "paid", "payment_method": "支付宝",
     "total_amount": 128.00, "discount_amount": 0, "shipping_fee": 6,
     "customer_note": "什么时候发货？急用", "priority": "urgent", "delivered_at": None},

    # id=10: 已发货（EMS，即将超时）
    {"order_no": "ORD20250618010", "customer_name": "冯十一", "customer_phone": "13100009009",
     "status": "shipped", "payment_status": "paid", "payment_method": "微信支付",
     "total_amount": 345.50, "discount_amount": 0, "shipping_fee": 20,
     "customer_note": "偏远地区请发EMS", "priority": "normal", "delivered_at": None},
]

ORDER_ITEMS = [
    # order 1
    {"order_id": 1, "product_name": "无线蓝牙耳机 Pro 版", "product_sku": "SKU-EAR-001", "quantity": 1, "unit_price": 299.00},
    # order 2
    {"order_id": 2, "product_name": "机械键盘 Cherry 青轴", "product_sku": "SKU-KB-088", "quantity": 1, "unit_price": 899.00},
    {"order_id": 2, "product_name": "电竞鼠标垫 XL", "product_sku": "SKU-MP-012", "quantity": 1, "unit_price": 79.00},
    {"order_id": 2, "product_name": "USB-C 扩展坞 7合1", "product_sku": "SKU-DOCK-007", "quantity": 1, "unit_price": 599.00},
    {"order_id": 2, "product_name": "显示器支架 双屏", "product_sku": "SKU-STAND-022", "quantity": 1, "unit_price": 123.00},
    # order 3
    {"order_id": 3, "product_name": "手机壳 iPhone 16 Pro 硅胶", "product_sku": "SKU-CASE-105", "quantity": 2, "unit_price": 44.95},
    # order 4
    {"order_id": 4, "product_name": "登山背包 40L 防水", "product_sku": "SKU-BAG-033", "quantity": 1, "unit_price": 456.00},
    # order 5
    {"order_id": 5, "product_name": "运动鞋 跑步鞋 透气网面", "product_sku": "SKU-SHOE-099", "quantity": 1, "unit_price": 199.00},
    # order 6
    {"order_id": 6, "product_name": "不锈钢保温杯 500ml", "product_sku": "SKU-CUP-044", "quantity": 1, "unit_price": 68.00},
    # order 7
    {"order_id": 7, "product_name": "人体工学椅 Pro 版", "product_sku": "SKU-CHAIR-001", "quantity": 1, "unit_price": 2360.00},
    # order 8
    {"order_id": 8, "product_name": "iPad Air 11寸 M3 256GB", "product_sku": "SKU-IPAD-003", "quantity": 1, "unit_price": 5999.00},
    # order 9
    {"order_id": 9, "product_name": "台灯 LED 护眼 三档调光", "product_sku": "SKU-LAMP-067", "quantity": 1, "unit_price": 128.00},
    # order 10
    {"order_id": 10, "product_name": "加湿器 卧室静音 3L", "product_sku": "SKU-HUM-023", "quantity": 1, "unit_price": 345.50},
]

LOGISTICS = [
    # order 1 — 顺丰，已签收
    {"order_id": 1, "tracking_no": "SF1234567890123", "carrier": "顺丰速运", "shipping_method": "标快",
     "receiver_name": "张三", "receiver_phone": "13800001001",
     "province": "广东省", "city": "深圳市", "district": "南山区",
     "address_full": "科技园南路 88 号 腾讯滨海大厦 15F",
     "weight_kg": 0.35, "est_delivery_date": "2025-06-18",
     "actual_delivery_date": "2025-06-18", "status": "delivered", "signed_by": "张三"},

    # order 2 — 中通，运输中（一单多件）
    {"order_id": 2, "tracking_no": "ZT9876543210987", "carrier": "中通快递", "shipping_method": "标准快递",
     "receiver_name": "李四", "receiver_phone": "13900002002",
     "province": "浙江省", "city": "杭州市", "district": "余杭区",
     "address_full": "文一西路 969 号 阿里巴巴西溪园区",
     "weight_kg": 3.80, "est_delivery_date": "2025-06-28",
     "actual_delivery_date": None, "status": "in_transit", "signed_by": None},
    # order 2 第二件包裹（一单多包）
    {"order_id": 2, "tracking_no": "ZT9876543210988", "carrier": "中通快递", "shipping_method": "标准快递",
     "receiver_name": "李四", "receiver_phone": "13900002002",
     "province": "浙江省", "city": "杭州市", "district": "余杭区",
     "address_full": "文一西路 969 号 阿里巴巴西溪园区",
     "weight_kg": 2.10, "est_delivery_date": "2025-06-29",
     "actual_delivery_date": None, "status": "picked_up", "signed_by": None},

    # order 8 — 京东，运输中
    {"order_id": 8, "tracking_no": "JD1122334455667", "carrier": "京东物流", "shipping_method": "京尊达",
     "receiver_name": "张三", "receiver_phone": "13800001001",
     "province": "广东省", "city": "深圳市", "district": "南山区",
     "address_full": "科技园南路 88 号 腾讯滨海大厦 15F",
     "weight_kg": 0.80, "est_delivery_date": "2025-06-29",
     "actual_delivery_date": None, "status": "out_for_delivery", "signed_by": None},

    # order 10 — EMS，运输中（偏远地区，超时风险）
    {"order_id": 10, "tracking_no": "EMS5566778899001", "carrier": "中国邮政EMS", "shipping_method": "EMS特快",
     "receiver_name": "冯十一", "receiver_phone": "13100009009",
     "province": "西藏自治区", "city": "拉萨市", "district": "城关区",
     "address_full": "江苏路 18 号",
     "weight_kg": 1.20, "est_delivery_date": "2025-06-27",
     "actual_delivery_date": None, "status": "in_transit", "signed_by": None},
]

LOGISTICS_TRACKING = [
    # 顺丰 SF1234567890123（完整签收链路）
    {"logistics_id": 1, "location": "深圳南山集散中心", "description": "快件到达深圳南山集散中心", "status_code": "arrived_hub", "recorded_at": "2025-06-16 22:15:00+08"},
    {"logistics_id": 1, "location": "深圳南山营业点", "description": "快件到达深圳南山营业点，准备派送", "status_code": "out_for_delivery", "recorded_at": "2025-06-18 08:30:00+08"},
    {"logistics_id": 1, "location": "深圳南山", "description": "您的快件已签收，签收人：本人签收", "status_code": "delivered", "recorded_at": "2025-06-18 14:30:00+08"},

    # 中通 ZT9876543210987（运输中）
    {"logistics_id": 2, "location": "杭州仓", "description": "快递已揽收", "status_code": "picked_up", "recorded_at": "2025-06-24 09:00:00+08"},
    {"logistics_id": 2, "location": "杭州分拣中心", "description": "快件到达杭州分拣中心", "status_code": "arrived_hub", "recorded_at": "2025-06-24 18:30:00+08"},
    {"logistics_id": 2, "location": "杭州转运中心", "description": "快件从杭州转运中心发出，下一站：余杭集散点", "status_code": "in_transit", "recorded_at": "2025-06-25 04:00:00+08"},
    {"logistics_id": 2, "location": "余杭集散点", "description": "快件到达余杭集散点", "status_code": "arrived_local", "recorded_at": "2025-06-26 07:00:00+08"},

    # 中通 ZT9876543210988（刚揽件）
    {"logistics_id": 3, "location": "杭州仓", "description": "快递已揽收", "status_code": "picked_up", "recorded_at": "2025-06-25 16:00:00+08"},

    # 京东 JD1122334455667（派送中）
    {"logistics_id": 4, "location": "北京分拣中心", "description": "快件到达北京分拣中心", "status_code": "arrived_hub", "recorded_at": "2025-06-27 23:00:00+08"},
    {"logistics_id": 4, "location": "深圳南山营业部", "description": "快件到达深圳南山营业部，快递员xxx（工号10086）正在为您派送", "status_code": "out_for_delivery", "recorded_at": "2025-06-28 08:45:00+08"},

    # EMS EMS5566778899001（偏远地区，有延迟）
    {"logistics_id": 5, "location": "成都集散中心", "description": "快件到达成都集散中心", "status_code": "arrived_hub", "recorded_at": "2025-06-20 14:00:00+08"},
    {"logistics_id": 5, "location": "成都集散中心", "description": "快件从成都集散中心发出，下一站：拉萨", "status_code": "in_transit", "recorded_at": "2025-06-21 06:00:00+08"},
    {"logistics_id": 5, "location": "拉萨转运中心", "description": "快件到达拉萨转运中心，因天气原因预计延迟1-2天", "status_code": "delayed", "recorded_at": "2025-06-25 11:00:00+08"},
]

ORDER_STATUS_LOG = [
    {"order_id": 1, "old_status": None, "new_status": "pending", "changed_by": "system", "note": "订单创建"},
    {"order_id": 1, "old_status": "pending", "new_status": "confirmed", "changed_by": "system", "note": "客户已支付"},
    {"order_id": 1, "old_status": "confirmed", "new_status": "shipped", "changed_by": "warehouse", "note": "仓库已发货"},
    {"order_id": 1, "old_status": "shipped", "new_status": "delivered", "changed_by": "system", "note": "物流显示已签收"},
    # order 7 — 催单 + 修改备注
    {"order_id": 7, "old_status": None, "new_status": "pending", "changed_by": "system", "note": "订单创建"},
    {"order_id": 7, "old_status": "pending", "new_status": "confirmed", "changed_by": "system", "note": "客户已支付"},
    {"order_id": 7, "old_status": "confirmed", "new_status": "confirmed", "changed_by": "agent_zhang", "note": "客户来电要求修改地址：改为北京市朝阳区望京SOHO T3 1508"},
    # order 9 — 客户催单
    {"order_id": 9, "old_status": None, "new_status": "processing", "changed_by": "system", "note": "订单创建并确认，待仓库发货"},
    {"order_id": 9, "old_status": "processing", "new_status": "processing", "changed_by": "system", "note": "客户催单：什么时候发货？急用"},
]


def seed_orders(tenant_id: int = 1):
    """插入示例订单&物流数据（幂等：已有数据则跳过）"""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM orders WHERE tenant_id=%s LIMIT 1", (tenant_id,))
            if cur.fetchone():
                logger.info(f"订单示例数据已存在: tenant_id={tenant_id}, 跳过")
                return 0

            # 插入订单，收集 (列表位置 → 真实 id) 的映射
            order_ids: dict[int, int] = {}
            for idx, o in enumerate(ORDERS):
                day = int(o["order_no"][7:9])
                fake_date = datetime.now(timezone.utc) - timedelta(days=28 - day)
                cur.execute(
                    """INSERT INTO orders (tenant_id, order_no, customer_name, customer_phone, status,
                       payment_status, payment_method, total_amount, discount_amount, shipping_fee,
                       customer_note, priority, delivered_at, created_at)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (tenant_id, o["order_no"], o["customer_name"], o["customer_phone"],
                     o["status"], o["payment_status"], o["payment_method"],
                     o["total_amount"], o["discount_amount"], o["shipping_fee"],
                     o["customer_note"], o["priority"], o["delivered_at"], fake_date),
                )
                order_ids[idx + 1] = cur.fetchone()["id"]  # 种子数据用 1-based

            # 插入订单项
            for item in ORDER_ITEMS:
                real_order_id = order_ids[item["order_id"]]
                cur.execute(
                    """INSERT INTO order_items (order_id, product_name, product_sku, quantity, unit_price)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (real_order_id, item["product_name"], item["product_sku"],
                     item["quantity"], item["unit_price"]),
                )

            # 插入物流，收集 logistics id 映射
            logistics_ids: dict[int, int] = {}
            for idx, l in enumerate(LOGISTICS):
                real_order_id = order_ids[l["order_id"]]
                cur.execute(
                    """INSERT INTO logistics (order_id, tracking_no, carrier, shipping_method,
                       receiver_name, receiver_phone, province, city, district, address_full,
                       weight_kg, est_delivery_date, actual_delivery_date, status, signed_by)
                       VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                       RETURNING id""",
                    (real_order_id, l["tracking_no"], l["carrier"], l["shipping_method"],
                     l["receiver_name"], l["receiver_phone"],
                     l["province"], l["city"], l["district"], l["address_full"],
                     l["weight_kg"], l["est_delivery_date"], l["actual_delivery_date"],
                     l["status"], l.get("signed_by")),
                )
                logistics_ids[idx + 1] = cur.fetchone()["id"]

            # 插入物流轨迹
            for t in LOGISTICS_TRACKING:
                real_logistics_id = logistics_ids[t["logistics_id"]]
                cur.execute(
                    """INSERT INTO logistics_tracking (logistics_id, location, description, status_code, recorded_at)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (real_logistics_id, t["location"], t["description"],
                     t["status_code"], t["recorded_at"]),
                )

            # 插入状态日志
            for s in ORDER_STATUS_LOG:
                real_order_id = order_ids[s["order_id"]]
                cur.execute(
                    """INSERT INTO order_status_log (order_id, old_status, new_status, changed_by, note)
                       VALUES (%s,%s,%s,%s,%s)""",
                    (real_order_id, s["old_status"], s["new_status"], s["changed_by"], s["note"]),
                )

            cur.execute("SELECT COUNT(*) FROM orders WHERE tenant_id=%s", (tenant_id,))
            count = cur.fetchone()["count"]

        conn.commit()
    logger.info(f"订单示例数据已就绪: tenant_id={tenant_id}, orders={count}")
    return count
