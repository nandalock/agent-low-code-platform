"""审批子系统自检

纯逻辑，不需要 docker / DB。审批是通用能力，这些用例**不含任何沙箱语义** ——
这正是它该待在这里（而不是 sandbox 的测试文件里）的理由。

Usage:
    docker compose exec backend python -m backend.interaction.approval.test_approval
    # 或本机（仓库根目录下）：
    python backend/interaction/approval/test_approval.py
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.interaction.approval import (  # noqa: E402
    ApprovalRequest,
    ApprovalService,
    InProcessApprovalChannel,
    new_approval_id,
)
from backend.interaction.approval.models import ALLOWED  # noqa: E402


def _req(approval_id="a1", **kw) -> ApprovalRequest:
    base = dict(
        reason="做一件需要确认的事",
        tool_name="some_tool",
        tool_call_id="c1",
        agent="agent-a",
        metadata={"kind": "demo"},
    )
    return ApprovalRequest(approval_id=approval_id, **{**base, **kw})


# ── 模型 ──


def test_approval_id_is_unique_and_short():
    ids = {new_approval_id() for _ in range(100)}
    assert len(ids) == 100          # 不撞
    assert all(len(i) == 12 for i in ids)


def test_request_to_dict_is_json_safe():
    """agent 是任意对象 —— 序列化时必须转成字符串，不能把对象泄进 JSON。"""
    req = _req(agent=object())
    d = req.to_dict()
    assert isinstance(d["agent"], str)
    assert d["reason"] and d["tool"] == "some_tool" and d["tool_call_id"] == "c1"
    assert d["metadata"] == {"kind": "demo"}


def test_request_carries_no_domain_semantics():
    """审批词汇保持领域中立：字段里不该出现任何沙箱概念。"""
    fields = set(ApprovalRequest.__dataclass_fields__)
    assert fields == {"approval_id", "reason", "tool_name", "tool_call_id", "agent", "metadata", "signal"}
    assert ALLOWED == "allowed-once"


# ── 通道 ──


def test_channel_resolve_wakes_waiter():
    ch = InProcessApprovalChannel(timeout_s=5)
    assert ch.resolve("nope", "allow-once") is False   # 无人在等

    async def scenario():
        task = asyncio.create_task(ch.request(_req()))
        await asyncio.sleep(0)
        assert [r.approval_id for r in ch.pending()] == ["a1"]
        assert ch.resolve("a1", "allow-once") is True
        assert await task == "allowed-once"
        assert ch.pending() == []                       # 取走后清理，不留残项
        assert ch.resolve("a1", "allow-once") is False   # 重复点击：不是幂等成功

    asyncio.run(scenario())


def test_channel_maps_decisions_and_fails_closed_on_unknown():
    ch = InProcessApprovalChannel(timeout_s=5)

    async def scenario(decision, expected):
        task = asyncio.create_task(ch.request(_req()))
        await asyncio.sleep(0)
        ch.resolve("a1", decision)
        assert await task == expected

    asyncio.run(scenario("reject", "rejected"))
    asyncio.run(scenario("cancel", "cancelled"))
    # 未知裁决值 → 拒绝，不是「还没决定」：不能让它永远悬着
    asyncio.run(scenario("bogus", "rejected"))


def test_channel_timeout_and_cancel_are_fail_closed():
    ch = InProcessApprovalChannel(timeout_s=0.01)
    # 没人回应 ≠ 同意
    assert asyncio.run(ch.request(_req("a2"))) == "unavailable"
    assert ch.pending() == []

    async def cancelled():
        """取消（客户端断连）：异常向上传播 + 注册表清理干净。"""
        task = asyncio.create_task(ch.request(_req("a3")))
        await asyncio.sleep(0)
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert ch.pending() == []

    asyncio.run(cancelled())


# ── 服务 ──


def test_service_delegates_and_reports_availability():
    service = ApprovalService(InProcessApprovalChannel(timeout_s=5))
    assert service.available is True      # 装配了服务 = 有人可问
    assert service.pending() == []

    async def scenario():
        task = asyncio.create_task(service.request(_req("a4")))
        await asyncio.sleep(0)
        assert [r.approval_id for r in service.pending()] == ["a4"]
        service.resolve("a4", "allow-once")
        assert await task == "allowed-once"

    asyncio.run(scenario())


def test_approval_never_imports_a_domain_package():
    """依赖方向守卫：approval 是通用能力，**不得**反向依赖任何业务域。

    这条不是洁癖 —— 一旦 approval 认识沙箱，它就不能给第二个消费者用，
    「通用的人机确认」这个定位也就没了。
    """
    import pathlib

    here = pathlib.Path(__file__).parent
    offenders = []
    for f in here.glob("*.py"):
        if f.name == "test_approval.py":
            continue
        text = f.read_text(encoding="utf-8")
        for module in ("backend.tool_system", "backend.agents", "backend.api", "backend.services"):
            if module in text:
                offenders.append(f"{f.name} → {module}")
    assert not offenders, f"approval 反向依赖了业务域: {offenders}"


# ── 运行器 ──


def main() -> int:
    tests = [(name, fn) for name, fn in sorted(globals().items()) if name.startswith("test_")]
    failed = 0
    for name, fn in tests:
        print(f"  {name} ...", end="", flush=True)
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            failed += 1
            print(f"\r  ✗ {name}: {type(e).__name__}: {e}")
        else:
            print(f"\r  ✓ {name}")
    print()
    if failed:
        print(f"{failed}/{len(tests)} 失败 ✗")
        return 1
    print(f"全部通过 ✓ ({len(tests)} 项)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
