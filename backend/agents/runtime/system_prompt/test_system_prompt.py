"""SystemPrompt 框架层自检（注册 / 组装 / 严格渲染）

纯内存，不需要 docker / DB / 网络。

Usage:
    docker compose exec backend pytest backend/agents/runtime/system_prompt/test_system_prompt.py -v
    # 或本机（仓库根目录下）：
    python backend/agents/runtime/system_prompt/test_system_prompt.py
"""
import math
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", ".."))

from backend.agents.runtime.system_prompt import (  # noqa: E402
    CONTEXT_ORDERS,
    SECTION_ORDERS,
    AssembleContext,
    PromptContext,
    PromptSection,
    SystemPrompt,
    ToolProviderResult,
    ToolSchema,
    render_context_snapshot,
    render_prompt,
    render_system_prompt,
)


def _sp() -> SystemPrompt:
    """每个用例独立的注册表（不碰模块级单例，避免用例间污染）"""
    return SystemPrompt()


def _tool(name: str, guidance: str | None = None) -> tuple[ToolSchema, dict[str, str]]:
    schema = ToolSchema(name=name, function={"name": name, "description": "", "parameters": {}})
    return schema, ({name: guidance} if guidance else {})


# ── 注册期校验 ──


def test_register_and_order_sections():
    """V16：按 order 升序；同 order 按 name 码点序（跨机器确定）"""
    sp = _sp()
    sp.section(PromptSection(name="c", order=100, text="C"))
    sp.section(PromptSection(name="a", order=100, text="A"))
    sp.section(PromptSection(name="first", order=-1, text="FIRST"))
    sp.section(PromptSection(name="b", order=100, text="B"))

    result = render_prompt(sp.assemble(AssembleContext()))
    assert result == "FIRST\n\nA\n\nB\n\nC", result


def test_duplicate_registration_raises():
    """V1/V2/V3：同名段 / 上下文 / 变量重复注册均抛错"""
    sp = _sp()
    sp.section(PromptSection(name="dup", order=0, text="x"))
    _expect_raises(ValueError, lambda: sp.section(PromptSection(name="dup", order=1, text="y")))

    sp.context(PromptContext(name="cdup", order=0, text="x"))
    _expect_raises(ValueError, lambda: sp.context(PromptContext(name="cdup", order=1, text="y")))

    sp.variable("vdup", lambda ctx: "x")
    _expect_raises(ValueError, lambda: sp.variable("vdup", lambda ctx: "y"))


def test_non_finite_order_raises():
    """V5：NaN / inf / 布尔 / 非数字 order 均抛错"""
    for bad in (math.nan, math.inf, -math.inf, True, "100", None):
        sp = _sp()
        _expect_raises(
            ValueError,
            lambda bad=bad, sp=sp: sp.section(PromptSection(name="s", order=bad, text="x")),
        )
        sp2 = _sp()
        _expect_raises(
            ValueError,
            lambda bad=bad, sp2=sp2: sp2.context(PromptContext(name="c", order=bad, text="x")),
        )


def test_invalid_variable_name_raises():
    """V4：变量名必须匹配 [a-z][a-z0-9_]*，注册即校验"""
    sp = _sp()
    for bad in ("Cwd", "1abc", "a b", "a-b", "", "工作目录"):
        _expect_raises(ValueError, lambda bad=bad, sp=sp: sp.variable(bad, lambda ctx: "x"))


def test_unknown_section_order_name_raises():
    """具名 order 只认常量表内的名字"""
    sp = _sp()
    _expect_raises(KeyError, lambda: sp.get_section_order("NOT_A_SLOT"))
    _expect_raises(KeyError, lambda: sp.get_context_order("NOT_A_SLOT"))
    assert sp.get_section_order("AGENT_IDENTITY") == -1000
    assert sp.get_context_order("UPSTREAM_CONTEXT") == 9000


def test_reserved_tool_prefix_rejected():
    """V9：注册段不得占用 tool: 保留前缀"""
    sp = _sp()
    sp.section(PromptSection(name="tool:bash", order=1000, text="手写的重复说明书"))
    _expect_raises(ValueError, lambda: sp.assemble(AssembleContext()))


def test_duplicate_tool_across_providers_raises():
    """V6：跨 provider 提供同名工具 → 组装失败"""
    sp = _sp()
    sp.tools(lambda ctx: ToolProviderResult(schemas=[_tool("bash")[0]]))
    sp.tools(lambda ctx: ToolProviderResult(schemas=[_tool("bash")[0]]))
    _expect_raises(ValueError, lambda: sp.assemble(AssembleContext()))


def test_non_str_provider_result_raises():
    """V8：provider 返回非 str → 组装失败并指明段名"""
    sp = _sp()
    sp.section(PromptSection(name="bad", order=0, text=lambda ctx: 42))
    try:
        sp.assemble(AssembleContext())
    except TypeError as e:
        assert "bad" in str(e), e
    else:
        raise AssertionError("应抛 TypeError")
    sp2 = _sp()
    sp2.tools(lambda ctx: "不是 ToolProviderResult")
    _expect_raises(TypeError, lambda: sp2.assemble(AssembleContext()))


# ── 渲染期校验 ──


def test_render_variable_interpolation():
    """变量插值；空串是合法值"""
    sp = _sp()
    sp.variable("who", lambda ctx: "客服助手")
    sp.variable("nothing", lambda ctx: "")
    sp.section(PromptSection(name="s", order=0, text="你是 {{who}}。{{nothing}}结束"))
    assert render_prompt(sp.assemble(AssembleContext())) == "你是 客服助手。结束"


def test_render_unknown_variable_raises_with_owner():
    """V10：未注册变量抛错，消息含段名与已注册变量列表"""
    sp = _sp()
    sp.variable("cwd", lambda ctx: "/tmp")
    sp.section(PromptSection(name="my:section", order=0, text="目录 {{modle}} 无效"))
    try:
        sp.assemble(AssembleContext())
        render_prompt(sp.assemble(AssembleContext()))
    except ValueError as e:
        assert "modle" in str(e) and "my:section" in str(e) and "cwd" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")


def test_render_none_value_raises():
    """V11：已注册但 provider 返回 None → 抛错"""
    sp = _sp()
    sp.variable("cwd", lambda ctx: None)
    sp.section(PromptSection(name="s", order=0, text="目录 {{cwd}}"))
    try:
        render_prompt(sp.assemble(AssembleContext()))
    except ValueError as e:
        assert "cwd" in str(e), e
    else:
        raise AssertionError("应抛 ValueError")


def test_render_malformed_group_raises():
    """V12：{{}} / {{a b}} / 组不完整但后面有 }} → 抛错"""
    for bad in ("{{}}", "{{a b}}", "{{ x }}", "前 {{a 后 }} 尾"):
        sp = _sp()
        sp.variable("a", lambda ctx: "A")
        sp.section(PromptSection(name="s", order=0, text=bad))
        try:
            render_prompt(sp.assemble(AssembleContext()))
        except ValueError as e:
            assert "s" in str(e), (bad, e)
        else:
            raise AssertionError(f"{bad!r} 应抛 ValueError")


def test_render_lone_open_brace_is_literal():
    """V13：孤立未闭合的 {{ 当作字面行文"""
    sp = _sp()
    sp.section(PromptSection(name="s", order=0, text="花括号 {{ 是字面量"))
    assert render_prompt(sp.assemble(AssembleContext())) == "花括号 {{ 是字面量"


def test_render_no_rescan_of_substituted_values():
    """V14：替换后的值不再重扫描（值里的 {{ 原样保留）"""
    sp = _sp()
    sp.variable("raw", lambda ctx: "{{who}}")
    sp.section(PromptSection(name="s", order=0, text="值：{{raw}}"))
    assert render_prompt(sp.assemble(AssembleContext())) == "值：{{who}}"


def test_render_empty_sections_dropped_and_all_empty_returns_empty():
    """V15：空段渲染后消失；全空 → 空串（调用方不应发空 system 消息）"""
    sp = _sp()
    sp.section(PromptSection(name="empty", order=0, text=""))
    sp.section(PromptSection(name="blank", order=10, text=lambda ctx: ""))
    sp.section(PromptSection(name="real", order=20, text="正文"))
    assert render_prompt(sp.assemble(AssembleContext())) == "正文"

    sp_all_empty = _sp()
    sp_all_empty.section(PromptSection(name="empty", order=0, text=""))
    assert render_prompt(sp_all_empty.assemble(AssembleContext())) == ""
    assert render_system_prompt(sp_all_empty.assemble(AssembleContext())) == ""


def test_complete_section_semantics():
    """V7：单个 complete 段独占；多于一个抛错"""
    sp = _sp()
    sp.section(PromptSection(name="identity", order=-1000, text="身份"))
    sp.section(PromptSection(name="full", order=0, text="完整提示词", complete=True))
    assert render_prompt(sp.assemble(AssembleContext())) == "完整提示词"

    sp2 = _sp()
    sp2.section(PromptSection(name="a", order=0, text="A", complete=True))
    sp2.section(PromptSection(name="b", order=10, text="B", complete=True))
    _expect_raises(ValueError, lambda: sp2.assemble(AssembleContext()))


def test_contexts_ordered_after_sections():
    """V17：上下文恒排在全部段之后（尾部保缓存前缀）"""
    sp = _sp()
    sp.section(PromptSection(name="first", order=-1000, text="身份"))
    sp.section(PromptSection(name="last", order=SECTION_ORDERS["TOOL_GUIDANCE"], text="工具指导"))
    sp.context(PromptContext(name="up", order=CONTEXT_ORDERS["UPSTREAM_CONTEXT"], text="上游输出"))
    sp.context(PromptContext(name="mem", order=CONTEXT_ORDERS["MEMORY_CONTEXT"], text="记忆"))

    result = render_system_prompt(sp.assemble(AssembleContext()))
    assert result == "身份\n\n工具指导\n\n记忆\n\n上游输出", result


def test_dynamic_provider_reads_visible_tool_names():
    """组装阶段序：tool provider 求值时可见集为 None；section 求值时已回填"""
    seen: dict[str, object] = {}
    sp = _sp()
    sp.tools(lambda ctx: (seen.__setitem__("tools", ctx.visible_tool_names), ToolProviderResult(schemas=[_tool("bash")[0]]))[1])
    sp.section(PromptSection(
        name="cond",
        order=0,
        text=lambda ctx: (seen.__setitem__("section", ctx.visible_tool_names), "有条件段")[1],
    ))
    ctx = AssembleContext()
    sp.assemble(ctx)

    assert seen["tools"] is None, "tool provider 求值时可见集尚未回填"
    assert seen["section"] == frozenset({"bash"}), seen["section"]
    assert ctx.visible_tool_names is None, "组装不应改写调用方传入的上下文"


def test_guidance_section_synthesized_only_for_visible_tools():
    """工具指导段：仅对可见且有 guidance 的工具合成，按工具名排序"""
    sp = _sp()
    bash, g1 = _tool("bash", "检查退出码")
    python, g2 = _tool("python", "先读报错行")
    search, _ = _tool("search")
    sp.tools(lambda ctx: ToolProviderResult(schemas=[python, bash, search], guidance={**g1, **g2}))

    result = render_system_prompt(sp.assemble(AssembleContext()))
    assert result == "检查退出码\n\n先读报错行", result  # bash 在 python 前（码点序）
    assert "search" not in result, "无 guidance 的工具不应产生段"


def test_guidance_absent_when_tool_invisible():
    """工具不可见时说明书一并消失（provider 只返回 guidance、不返回该 schema）"""
    sp = _sp()
    bash, _ = _tool("bash")
    sp.tools(lambda ctx: ToolProviderResult(schemas=[bash], guidance={"bash": "b", "ghost": "g"}))
    result = render_system_prompt(sp.assemble(AssembleContext()))
    assert result == "b", result
    assert "g" not in result


def test_render_context_snapshot_excludes_sections():
    """render_context_snapshot 只渲染上下文，用于独立展示动态部分"""
    sp = _sp()
    sp.section(PromptSection(name="s", order=0, text="正文"))
    sp.context(PromptContext(name="c", order=9000, text="上下文"))
    assembly = sp.assemble(AssembleContext())
    assert render_context_snapshot(assembly) == "上下文"
    assert render_prompt(assembly) == "正文"
    assert render_system_prompt(assembly) == "正文\n\n上下文"


# ── 运行器 ──


def _expect_raises(exc_type, fn):
    try:
        fn()
    except exc_type:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"期望 {exc_type.__name__}，实际 {type(e).__name__}: {e}")
    raise AssertionError(f"期望 {exc_type.__name__}，但未抛错")


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
