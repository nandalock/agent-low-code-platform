"""会话标题自检（净化 / 字节截断 / fallback / 钉住 / 不进模型输入）

纯内存，不需要 docker / DB / 网络（第 8 项用临时目录，也不碰 DB）。

Usage:
    docker compose exec backend python backend/agents/runtime/session/tests/test_title.py
"""
import os
import sys
import tempfile
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "..", "..", ".."))

from backend.agents.runtime.session import (  # noqa: E402
    ASSISTANT_MESSAGE,
    TITLE,
    USER_MESSAGE,
    Session,
    SessionEvent,
    SessionHeader,
    fold_session_title,
    get_session_title_service,
    normalize_session_title,
    truncate_title_utf8,
)
from backend.agents.runtime.session.title import (  # noqa: E402
    FALLBACK_MAX_BYTES,
    MAX_TITLE_BYTES,
    clean_title_text,
    fallback_session_title,
)
from backend.agents.runtime.session.title import (  # noqa: E402
    SOURCE_FALLBACK,
    SOURCE_PROVIDER,
    SOURCE_USER,
    collect_title_messages,
    fold_title_input,
)

ESC = chr(0x1B)
BEL = chr(0x07)
ZWSP = chr(0x200B)       # 零宽空格
RLO = chr(0x202E)        # 双向覆写（右到左）
C1_CSI = chr(0x9B)


def _session(sid: str = "s1") -> Session:
    return Session(header=SessionHeader(version=1, id=sid, created_at=time.time(), seed_length=0))


def _ev(seq: int, type_: str, data: dict) -> SessionEvent:
    return SessionEvent(type=type_, seq=seq, time=time.time(), data=data)


# ── 净化（验收 4）──


def test_clean_strips_ansi_osc_and_c1():
    """ANSI 颜色码 / OSC 标题串 / C1 CSI 全部去掉，正文留下"""
    assert clean_title_text("a" + ESC + "[31mred" + ESC + "[0m") == "ared"
    assert clean_title_text("x" + C1_CSI + "1;2my") == "xy"
    assert clean_title_text("hi" + ESC + "]0;title" + BEL + "there") == "hithere"
    # 未终止的 OSC：整段丢掉（宁可丢半截，不可把控制序列当正文显示）
    assert clean_title_text("un" + ESC + "]0;unterminated") == "un"


def test_clean_strips_zero_width_and_bidi():
    """零宽 / 双向控制符去掉（双向覆写是「欺骗性标题」的载体）"""
    assert clean_title_text("zero" + ZWSP + "width" + RLO + "rtl") == "zerowidthrtl"


def test_clean_collapses_whitespace_but_keeps_content():
    """空白（含制表/换行）折叠成单个空格，非空白 C0/DEL 直接删"""
    assert clean_title_text("a\tb\nc   d") == "a b c d"
    assert clean_title_text("nul" + chr(0) + "del" + chr(127)) == "nuldel"


def test_normalize_rejects_all_control_title():
    """全是控制符 → 净化后为空串（调用方据此拒绝，见 rename）"""
    assert normalize_session_title(ESC + "[31m" + ESC + "[0m") == ""


# ── 字节截断（验收 5）──


def test_truncate_by_bytes_not_chars():
    """中文按字节算：40 字节 ≈ 13 个汉字，不是 40 个"""
    text = "一二三四五六七八九十甲乙丙丁戊己庚辛"
    out = truncate_title_utf8(text, FALLBACK_MAX_BYTES)
    assert len(out.encode("utf-8")) <= FALLBACK_MAX_BYTES
    assert out == text[: FALLBACK_MAX_BYTES // 3]     # 每字 3 字节，取整
    assert out.encode("utf-8").decode("utf-8") == out  # 不切码点


def test_truncate_never_splits_code_point():
    """预算刚好卡在多字节字符中间时，宁可少一个字，也不吐出半个"""
    # 3 字节的汉字卡在 4 字节预算上：2 字节的 "ab" 留下，汉字整个丢掉
    assert truncate_title_utf8("ab中", 4) == "ab"
    assert truncate_title_utf8("ab中", 5) == "ab中"     # 刚好放得下 → 原样
    # 4 字节的 emoji 同理
    assert truncate_title_utf8("ab" + chr(0x1F600), 5) == "ab"
    # 预算够 → 原样
    assert truncate_title_utf8("ab" + chr(0x1F600), 6) == "ab" + chr(0x1F600)


def test_normalize_truncates_to_hard_cap():
    """任何来源的标题都截到 80 字节，且尾部不留悬挂空格"""
    out = normalize_session_title("中" * 100, MAX_TITLE_BYTES)
    assert len(out.encode("utf-8")) <= MAX_TITLE_BYTES
    assert out.encode("utf-8").decode("utf-8") == out
    assert out.endswith("中")          # 没有半截字符，也没有被切出来的空格


def test_long_ascii_and_cjk_mix():
    """中英混排：按字节截断不出现乱码半个字（逐个码点校验）"""
    out = normalize_session_title("hello 世界 " * 40)
    assert len(out.encode("utf-8")) <= MAX_TITLE_BYTES
    assert "\ufffd" not in out and out.encode("utf-8").decode("utf-8") == out


# ── fallback（验收 1 / 2）──


def test_fallback_takes_leading_words():
    """fallback = 首条消息前导词：5 词 / 40 字节，两个限额一起管"""
    assert fallback_session_title("Hello world from the deep end of it") == \
        "Hello world from the deep"
    assert fallback_session_title("   ") == ""          # 纯空白 → 空
    assert fallback_session_title("") == ""
    cjk = fallback_session_title("这是一个非常长的中文标题需要被截断到四十个字节以内才行")
    assert len(cjk.encode("utf-8")) <= FALLBACK_MAX_BYTES


def test_first_message_yields_title_then_llm_replaces():
    """验收 1：首条消息出 fallback 标题，随后可被 provider 版替换"""
    svc = get_session_title_service()
    s = _session()
    assert svc.settle(s) is None                     # 还没有消息 → 没有标题
    s.append(USER_MESSAGE, {"content": "帮我总结这篇论文"})
    after_fallback = svc.settle(s)
    assert after_fallback is not None and after_fallback.source == SOURCE_FALLBACK
    # provider 版落地（这里直接 append，真实路径见 title_service._run）
    s.append(TITLE, {"title": "论文总结助手", "message_seqs": [1],
                     "source": {"kind": SOURCE_PROVIDER, "provider": "session-title-llm"}})
    latest = svc.get(s)
    assert latest is not None
    assert (latest.title, latest.source) == ("论文总结助手", SOURCE_PROVIDER)
    # 再来一条消息也不会把它拽回 fallback（已有标题 → settle 是 no-op）
    s.append(USER_MESSAGE, {"content": "再详细点"})
    assert svc.settle(s).title == "论文总结助手"


def test_image_only_message_keeps_title_unchanged():
    """验收 2：纯图片消息不合格 → 标题不变，继续等下一次真正有文本的输入"""
    svc = get_session_title_service()
    s = _session("img")
    s.append(USER_MESSAGE, {"content": [{"type": "image_url", "image_url": {"url": "x"}}]})
    assert svc.settle(s) is None                      # 没有可用文本 → 不写标题事件
    assert fold_session_title(s.events) is None
    assert fold_title_input(s.events).count == 0

    s.append(USER_MESSAGE, {"content": "这次有文字了"})
    assert svc.settle(s).title == "这次有文字了"

    # 已经有标题之后，再发纯图片 → 依旧不变
    before = svc.get(s).title
    s.append(USER_MESSAGE, {"content": [{"type": "image_url"}]})
    assert svc.settle(s).title == before


def test_mixed_content_uses_text_blocks_only():
    """多模态消息只取文本块；图片块既不进标题也不产生多余空白"""
    s = _session("mix")
    s.append(USER_MESSAGE, {"content": [
        {"type": "image_url", "image_url": {"url": "x"}},
        {"type": "text", "text": "看看这张图"},
    ]})
    msgs = collect_title_messages(s.events)
    assert [m.text for m in msgs] == ["看看这张图"]


# ── 钉住（验收 3）──


def test_rename_pins_title_against_later_messages():
    """验收 3：改名后发消息 → 标题保持（钉住）"""
    svc = get_session_title_service()
    s = _session("pin")
    s.append(USER_MESSAGE, {"content": "第一条消息"})
    svc.settle(s)
    pinned = svc.rename(s, "我起的名")
    assert (pinned.title, pinned.source) == ("我起的名", SOURCE_USER)
    assert pinned.pinned

    s.append(USER_MESSAGE, {"content": "第二条消息"})
    assert svc.settle(s).title == "我起的名"           # 后续自动生成全部停摆
    assert svc.get(s).source == SOURCE_USER


def test_rename_normalizes_and_rejects_empty():
    """改名走同一套净化 + 字节上限；净化为空则拒绝（调用方翻 400）"""
    svc = get_session_title_service()
    s = _session("pin2")
    got = svc.rename(s, "  " + ESC + "[31m我的" + ESC + "[0m" + ZWSP + "标题  ")
    assert got.title == "我的标题"                    # 控制符被清掉、首尾空白折叠
    assert len(svc.rename(_session("pin3"), "中" * 100).title.encode("utf-8")) <= MAX_TITLE_BYTES
    for bad in ("", "   ", ESC + "[31m" + ESC + "[0m"):
        try:
            svc.rename(_session("pin4"), bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"应当拒绝空标题: {bad!r}")


def test_rename_does_not_reregister_fallback():
    """改名之后 settle 不会再补 fallback（否则钉住形同虚设）"""
    svc = get_session_title_service()
    s = _session("pin5")
    svc.rename(s, "钉住的")
    assert svc.settle(s).title == "钉住的"
    assert len([e for e in s.events if e.type == TITLE]) == 1


# ── 可重放（验收 6）──


def test_title_survives_replay():
    """验收 6：标题是事件 → 冷恢复 replay 出的标题与在线时逐字一致"""
    svc = get_session_title_service()
    s = _session("replay")
    s.append(USER_MESSAGE, {"content": "离线也要记得我叫什么"})
    svc.settle(s)
    online = svc.get(s)

    rebuilt = Session.from_events(s.header, s.events)
    replayed = fold_session_title(rebuilt.events)
    assert replayed is not None
    assert replayed.title == online.title
    assert replayed.source == online.source == SOURCE_FALLBACK


def test_fold_takes_latest_not_first():
    """fold 取**最新**（追加型日志的语义），不是第一条"""
    events = [
        _ev(1, USER_MESSAGE, {"content": "abc"}),
        _ev(2, TITLE, {"title": "第一版", "message_seqs": [1], "source": SOURCE_FALLBACK}),
        _ev(3, TITLE, {"title": "第二版", "message_seqs": [1], "source": SOURCE_USER}),
    ]
    assert fold_session_title(events).title == "第二版"


def test_fold_is_defensive_about_bad_rows():
    """读取侧防御：坏 data 不该让整个会话列表 500"""
    assert fold_session_title([_ev(1, TITLE, {"title": ""})]) is None
    assert fold_session_title([_ev(1, TITLE, {})]) is None
    assert fold_session_title([_ev(1, TITLE, "not-a-dict")]) is None
    # 来源非法 → 回落成 fallback，而不是抛
    bogus = fold_session_title([_ev(1, TITLE, {"title": "x", "source": "bogus"})])
    assert bogus is not None and bogus.source == SOURCE_FALLBACK


# ── 不进模型输入（验收 7）──


def test_title_never_enters_model_input():
    """验收 7：标题**不出现在发给模型的请求体里**（零 token、不动前缀缓存）"""
    s = _session("noleak")
    s.append(USER_MESSAGE, {"content": "用户说的话"})
    svc = get_session_title_service()
    svc.settle(s)
    s.append(ASSISTANT_MESSAGE, {"content": "模型的回答"})
    assert fold_session_title(s.events) is not None      # 标题确实存在

    from backend.agents.runtime.session.events import SURFACE_EVENT_TYPES
    assert TITLE not in SURFACE_EVENT_TYPES               # log-only 的机制保证

    # 关键断言用**不可能出现在消息里**的标题：fallback 标题本就是首条消息的前缀，
    # 拿它当探针等于什么都没测（它当然「在」消息里 —— 因为它就是从那儿来的）。
    svc.rename(s, "绝密标题ZZZ987")
    blob = repr(s.derive_messages())
    assert "绝密标题ZZZ987" not in blob
    assert "session/title" not in blob
    assert "message_seqs" not in blob
    assert all(m["role"] in ("user", "assistant") for m in s.derive_messages())


def test_settle_does_not_leak_into_derived_messages():
    """补 fallback 前后，派生出的 LLM 消息**逐字不变** —— 标题是纯旁路事实"""
    s = _session("noleak2")
    s.append(USER_MESSAGE, {"content": "只改标题不改上下文"})
    s.append(ASSISTANT_MESSAGE, {"content": "ok"})
    before = s.derive_messages()
    get_session_title_service().settle(s)
    assert s.derive_messages() == before


def test_surface_manager_ignores_title_events():
    """SurfaceManager 不收 session/title → derive_messages 看不到它"""
    s = _session("surface")
    s.append(USER_MESSAGE, {"content": "hi"})
    get_session_title_service().settle(s)
    assert [e.type for e in s.surface.events] == [USER_MESSAGE]


# ── 项目归属（验收 8）──


def test_canonical_project_path_normalizes_spellings():
    """同一个目录的多种写法归一到同一个 key（唯一性是**路径字符串相等**）"""
    from backend.api.workspace import _canonical_project_path as canon
    assert canon("D:/jk/Nexus/proj") == "D:/jk/Nexus/proj"
    assert canon("D:\\jk\\Nexus\\proj\\") == "D:/jk/Nexus/proj"      # 反斜杠 + 尾斜杠
    assert canon("d:/jk/Nexus/proj") == "D:/jk/Nexus/proj"           # 盘符大小写不敏感
    assert canon("D:/jk/Nexus/./proj") == "D:/jk/Nexus/proj"         # 当前目录
    assert canon("D:/jk/Nexus/sub/../proj") == "D:/jk/Nexus/proj"    # 词法展开 ..
    assert canon("D:/jk//Nexus///proj") == "D:/jk/Nexus/proj"        # 重复分隔符
    assert canon("C:/") == "C:/"                                     # 根：尾斜杠要留着


def test_project_label_is_last_segment():
    """项目显示名 = 末段；根目录没有末段 → 回落成整串（不能显示空标题）"""
    from backend.api.workspace import _project_label as label
    assert label("D:/jk/Nexus/proj") == "proj"
    assert label("C:/") == "C:/"
    assert label("//server/share") == "share"


def test_project_membership_needs_ledger_and_live_dir():
    """项目归属 = **账目** + 读时二次校验，两者缺一不可。

    账目是唯一的成员来源：没登记过（或登记已被移除）→ 未分组，绝不会因为「那个
    目录恰好还在」又自己冒出来 —— 这正是「移除项目」能生效、且旧会话不复活的原因。
    账目在但目录没了 → 也读作未分组（而不是让会话从列表里消失）。
    """
    from pathlib import Path
    from backend.api.workspace import _canonical_project_path, _project_for

    with tempfile.TemporaryDirectory() as tmp:
        alive = os.path.join(tmp, "alive")
        os.makedirs(alive)
        canonical = _canonical_project_path(alive)
        row = {"id": 7, "canonical_path": canonical, "title": "alive", "sort_order": 10.0}
        ledger = {"s1": row}
        alive_path = Path(alive)

        # 账目在 + cwd 对得上 + 目录还在 → 有归属
        got = _project_for("s1", alive, alive_path, ledger)
        assert got is not None and got["id"] == 7 and got["key"] == canonical
        assert got["label"] == "alive"

        # 账目里没有这个会话（没登记过 / 登记被移除）→ 未分组
        assert _project_for("s2", alive, alive_path, ledger) is None

        # 目录已被删（账目还在）→ 未分组
        gone = os.path.join(tmp, "gone")
        gone_ledger = {"s1": {**row, "canonical_path": _canonical_project_path(gone)}}
        assert _project_for("s1", gone, Path(gone), gone_ledger) is None
        # 目录解析不出来（调用方给 None）同样未分组
        assert _project_for("s1", alive, None, ledger) is None

        # 账目在，但会话现在的 cwd 不是这个目录（目录被改名 / 换了工作区）→ 未分组
        assert _project_for("s1", os.path.join(tmp, "moved"), alive_path, ledger) is None

        # 没有 cwd（自动工作区 <root>/<session_id>）→ 未分组：目录名是会话 id
        assert _project_for("s1", None, alive_path, ledger) is None
        assert _project_for("s1", "", alive_path, ledger) is None


def test_project_identity_is_path_not_label():
    """两个不同目录同名 → 两个项目（按路径区分）；同一个目录只算一个"""
    from backend.api.workspace import _canonical_project_path, _project_label
    a = _canonical_project_path("D:/jk/Nexus/test1")
    b = _canonical_project_path("D:/jk/Else/test1")
    assert a != b                                   # key 不同 → 两个项目
    assert _project_label(a) == _project_label(b) == "test1"   # 但显示名可以重名


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
