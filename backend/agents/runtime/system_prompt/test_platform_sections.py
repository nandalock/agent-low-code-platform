"""平台层自检（identity / persona / 上游上下文 / 变量 / 工具指导）

用单例注册表（platform_sections 在 import 包时已注册），工具来源以假 Registry
注入，不连 DB / 网络。

Usage:
    docker compose exec backend python backend/agents/runtime/system_prompt/test_platform_sections.py
"""
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from backend.agents.runtime.system_prompt import (  # noqa: E402
    AssembleContext,
    assemble,
    get_system_prompt,
    render_system_prompt,
)
from backend.agents.runtime.system_prompt.platform_sections import (  # noqa: E402
    PLATFORM_TOOL_GUIDANCE,
)
from backend.tool_system.sandbox.workspace import CONTAINER_WORKSPACE  # noqa: E402


def _assemble(ctx: AssembleContext):
    return asyncio.run(assemble(ctx))


def _ctx(**kwargs) -> AssembleContext:
    base = dict(
        tenant_id=1,
        agent_key="paper_agent",
        agent_name="PaperAgent",
        agent_description="学术论文研究助手",
        config={},
    )
    base.update(kwargs)
    return AssembleContext(**base)


# ── 段 ──


def test_identity_from_name_and_description():
    """身份段由 agent 定义派生：有描述 / 无描述两种形态"""
    assert render_system_prompt(_assemble(_ctx())) == "你是「PaperAgent」。学术论文研究助手"
    assert render_system_prompt(_assemble(_ctx(agent_description=""))) == "你是「PaperAgent」。"


def test_identity_falls_back_to_key_without_name():
    """name 缺失时用 agent_key 兜底，不产生空身份"""
    text = render_system_prompt(_assemble(_ctx(agent_name="")))
    assert text == "你是「paper_agent」。学术论文研究助手"


def test_persona_from_config_and_empty_disappears():
    """persona 段取 config.system_prompt；为空则该段消失（不留空行）"""
    with_persona = render_system_prompt(
        _assemble(_ctx(config={"system_prompt": "你只回答论文相关问题。"}))
    )
    assert with_persona == (
        "你是「PaperAgent」。学术论文研究助手\n\n你只回答论文相关问题。"
    )

    without = render_system_prompt(_assemble(_ctx(config={"system_prompt": "   "})))
    assert without == "你是「PaperAgent」。学术论文研究助手"


def test_identity_precedes_persona():
    """identity 在前、persona 在后（order -1000 vs 0）"""
    text = render_system_prompt(
        _assemble(_ctx(agent_description="描述", config={"system_prompt": "人设"}))
    )
    assert text.index("你是「PaperAgent」") < text.index("人设")


# ── 上游上下文（golden：必须与重构前的 seed 第二段逐字一致） ──


def test_upstream_context_format_golden():
    """上游输出格式与旧 _build_init_messages 逐字一致（存量会话重建不出偏差）"""
    payload = {"检索节点": {"output": "找到 3 篇", "type": "agent"}}

    # 旧实现（重构前的 agent_runtime._build_init_messages 第二段）
    legacy = (
        "【上游节点输出，供你参考】\n"
        + json.dumps(payload, ensure_ascii=False, indent=2)
    )

    text = render_system_prompt(_assemble(_ctx(upstream_context=payload)))
    assert text.endswith(legacy), text


def test_upstream_context_absent_contributes_nothing():
    """无上游输出时上下文段不贡献内容"""
    text = render_system_prompt(_assemble(_ctx(upstream_context=None)))
    assert "上游节点输出" not in text
    assert text == "你是「PaperAgent」。学术论文研究助手"


def test_upstream_ordered_after_sections():
    """上游上下文恒在全部段之后（尾部保缓存前缀）"""
    text = render_system_prompt(
        _assemble(_ctx(
            config={"system_prompt": "人设"},
            upstream_context={"n": {"output": "o", "type": "agent"}},
        ))
    )
    assert text.index("人设") < text.index("【上游节点输出")
    assert text.startswith("你是「PaperAgent」")


# ── 变量 ──


def test_variables_registered():
    """平台变量已注册：tenant_id / agent_key / cwd"""
    registered = get_system_prompt().variables()
    for name in ("tenant_id", "agent_key", "cwd"):
        assert name in registered, name


def test_variable_values_from_context():
    """变量取值来自组装上下文（tenant_id / agent_key）"""
    assembly = _assemble(_ctx(tenant_id=7, agent_key="faqagent"))
    assert assembly.variables["tenant_id"] == "7"
    assert assembly.variables["agent_key"] == "faqagent"


def test_cwd_has_no_value_without_session():
    """无会话时 cwd 无值 —— 引用它的段会渲染失败（而不是渲染成空串骗模型）"""
    assembly = _assemble(_ctx())
    assert assembly.variables["cwd"] is None


def test_variable_usable_in_section_text():
    """变量可在段文本里引用（{{tenant_id}}）"""
    sp = get_system_prompt()
    from backend.agents.runtime.system_prompt import PromptSection

    # 临时注册的段用完即删，避免污染单例（其它用例按名字取用不到它）
    sp.section(PromptSection(name="test:tmp", order=5000, text="租户 {{tenant_id}}"))
    try:
        text = render_system_prompt(_assemble(_ctx(tenant_id=3)))
        assert "租户 3" in text
    finally:
        sp._sections.pop("test:tmp", None)


# ── 工具指导 ──


def test_platform_guidance_covers_builtin_mcp_tools():
    """平台自有 MCP 工具的指导表覆盖 6 个工具"""
    assert set(PLATFORM_TOOL_GUIDANCE) == {
        "query_orders", "get_order_detail", "track_logistics",
        "search_faqs", "list_faq_tags", "get_user_profile",
    }
    assert all(text.strip() for text in PLATFORM_TOOL_GUIDANCE.values())


def test_tool_provider_without_registry_yields_nothing():
    """Registry 未装配 → 空工具 + 正常组装（装配顺序不该决定对话能否进行）"""
    # 测试进程未 init_registry()，get_registry() 会抛 RuntimeError
    assembly = _assemble(_ctx())
    assert assembly.tools == []
    assert render_system_prompt(assembly) == "你是「PaperAgent」。学术论文研究助手"


def _sandbox_tool_descriptions(cwd: str | None) -> dict[str, str]:
    """用假 Registry 跑一次组装，返回 {工具名: 描述}；``cwd`` 为会话工作区宿主路径。"""
    from backend.agents.runtime.session import SessionStore
    from backend.tool_system.registry import registry as registry_mod
    from backend.tool_system.registry.descriptor import SandboxToolConfig, ToolDescriptor

    tools = [
        ToolDescriptor(
            name="bash", type="sandbox", transport="", server_id=0,
            schema={"name": "bash", "description": "执行命令", "inputSchema": {}},
            sandbox=SandboxToolConfig(runtime="shell", image="alpine", timeout_s=1.0),
        ),
        ToolDescriptor(
            name="search", type="mcp", transport="http", server_id=1,
            schema={"name": "search", "description": "检索", "inputSchema": {}},
        ),
    ]

    class _FakeRegistry:
        async def get_descriptors_for(self, agent_key):
            return tools

    session = SessionStore().create()
    session.header.cwd = cwd

    saved = registry_mod.get_registry
    registry_mod.get_registry = lambda: _FakeRegistry()
    try:
        assembly = _assemble(_ctx(session=session))
    finally:
        registry_mod.get_registry = saved
    return {t.name: t.function["description"] for t in assembly.tools}


def test_sandbox_tool_description_carries_workspace_host():
    """沙箱工具的描述带上会话工作区的**宿主路径**，与读/写两条轴同格式。

    注册侧拼不了这一段（发生在启动时、没有会话），由 provider 按 ctx.session 补。
    漏了它，模型只能去挂载表里推——而 Docker Desktop 的 drvfs 只报盘符根
    （绑 ``D:/jk/Nexus/test1`` 与绑 ``D:/jk/Nexus`` 显示完全相同），推出来必然是
    「整个 D 盘」，并会这样转述给用户。
    """
    descs = _sandbox_tool_descriptions("D:/jk/Nexus/proj")
    assert f"可写位置：会话工作区 {CONTAINER_WORKSPACE}（宿主 D:/jk/Nexus/proj）" in descs["bash"]
    # 挂载说明是沙箱工具的专属事实，不该糊到别的工具上
    assert "可写位置" not in descs["search"]


def test_sandbox_tool_description_without_cwd_omits_host():
    """未绑定文件夹（cwd 为空）时不编造宿主路径 —— 只报容器内路径。"""
    desc = _sandbox_tool_descriptions(None)["bash"]
    assert f"可写位置：会话工作区 {CONTAINER_WORKSPACE}。" in desc
    assert "（宿主 None）" not in desc


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
