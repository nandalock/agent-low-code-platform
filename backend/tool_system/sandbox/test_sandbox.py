"""沙箱子系统自检（阶段 1a）

纯逻辑部分不需要 docker；docker e2e 部分在 docker 不可用时自动跳过。

Usage:
    docker compose exec backend python -m backend.tool_system.sandbox.test_sandbox
    # 或本机（仓库根目录下）：
    python backend/tool_system/sandbox/test_sandbox.py
"""
import asyncio
import json
import os
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from backend.agents.runtime.session.events import SANDBOX_MODE, TOOL_CALL, TOOL_RESULT, SessionEvent  # noqa: E402
from backend.agents.runtime.session.sandbox_projection import (  # noqa: E402
    SandboxModeProjection,
    project_sandbox_mode,
)
from backend.agents.runtime.session.trajectory_projection import TrajectoryProjection  # noqa: E402
from backend.tool_system.context import ToolContext  # noqa: E402
from backend.tool_system.events import APPROVAL_REQUEST as APPROVAL_REQUEST_EVENT  # noqa: E402
from backend.tool_system.events import SANDBOX_ESCALATION as SANDBOX_ESCALATION_EVENT  # noqa: E402
from backend.tool_system.events import ToolEvent  # noqa: E402
from backend.tool_system.registry.descriptor import SandboxToolConfig, ToolDescriptor  # noqa: E402
from backend.tool_system.runtime.executor import SandboxExecutor, build_sandbox_argv  # noqa: E402
from backend.tool_system.sandbox import (  # noqa: E402
    SandboxPolicy,
    SandboxUnavailableError,
    classify_outcome,
    classify_runner_failure,
    escalation_hint_marker,
    is_confined_mode,
    matches_signature,
    sandbox_denial_marker,
)
from backend.tool_system.sandbox.backends.docker import (  # noqa: E402
    DEFAULT_SANDBOX_IMAGE,
    DockerProvider,
    assert_no_isolation_injection,
)
from backend.interaction.approval import (  # noqa: E402
    ApprovalService,
    InProcessApprovalChannel,
    set_approval_service,
)
from backend.tool_system.sandbox.escalation import (  # noqa: E402
    ESCALATION_TARGETS,
    WIDER_MODES,
    EscalationDenied,
    EscalationInvalid,
    approve_escalation,
    assert_strictly_wider,
    validate_escalation_args,
)
from backend.tool_system.sandbox.policy import resolve_mode, resolve_policy  # noqa: E402
from backend.tool_system.sandbox.provider import (  # noqa: E402
    ConfinedArgv,
    RunnerFailureRule,
    SandboxProvider,
)
from backend.tool_system.sandbox.vocabulary import SandboxExecutionPolicy  # noqa: E402
from backend.tool_system.sandbox.workspace import (  # noqa: E402
    CONTAINER_READ_ROOT,
    READ_ROOTS_ENV,
    WORKSPACE_ROOT_ENV,
    assert_root_is_safe,
    build_read_mounts,
    normalize_host_path,
    parse_read_roots,
    probe_workspace_root,
    repo_root,
    resolve_workspace_root,
    session_workspace,
    to_container_path,
)

# ── 测试工具 ──


def expect_raises(exc_type, fn, *args, **kwargs):
    """断言 fn 抛出 exc_type。"""
    try:
        fn(*args, **kwargs)
    except exc_type:
        return
    except Exception as e:  # noqa: BLE001
        raise AssertionError(f"预期 {exc_type.__name__}，实际 {type(e).__name__}: {e}") from e
    raise AssertionError(f"预期 {exc_type.__name__}，但没有抛异常")


def _policy(mode="workspace-write", root="/tmp/ws"):
    return SandboxPolicy(mode=mode, workspace_root=root)


def _confined(signatures=(), rules=()) -> ConfinedArgv:
    return ConfinedArgv(
        argv=["true"],
        enforcement="full",
        denial_signatures=tuple(signatures),
        runner_failure_rules=tuple(rules),
    )


# ── 词汇 ──


def test_vocabulary_closed():
    """词汇封闭性：danger-full-access 不能构造受限策略。"""
    expect_raises(ValueError, SandboxPolicy, mode="danger-full-access", workspace_root="/tmp/ws")
    expect_raises(ValueError, SandboxPolicy, mode="bogus", workspace_root="/tmp/ws")
    expect_raises(ValueError, SandboxPolicy, mode="read-only", workspace_root="")
    assert _policy("read-only").mode == "read-only"
    assert _policy("workspace-write").mode == "workspace-write"


def test_is_confined_mode():
    assert is_confined_mode("read-only")
    assert is_confined_mode("workspace-write")
    assert not is_confined_mode("danger-full-access")


# ── 分类器 ──


def test_classify_normal():
    c = _confined(signatures=["read-only file system"])
    assert classify_outcome(0, "", c) == "normal"
    assert classify_outcome(1, "boom", c) == "normal"


def test_classify_denied():
    c = _confined(signatures=["read-only file system"])
    assert classify_outcome(1, "sh: can't create /x: Read-only file system", c) == "denied"
    # 退出码为 0 时不算拒绝
    assert classify_outcome(0, "Read-only file system", c) == "normal"
    # 信号终止（None）不算拒绝
    assert classify_outcome(None, "Read-only file system", c) == "normal"


def test_classify_runner_failure_exit_gate():
    """退出码门控：只有 125 才算 docker 自身失败。"""
    rules = (
        RunnerFailureRule(
            allowed_exit_codes=(125,),
            fatal_signatures=("cannot connect to the docker daemon",),
        ),
    )
    c = _confined(rules=rules)
    assert classify_outcome(125, "Cannot connect to the Docker daemon", c) == "runner_failed"
    # 同样的文本但退出码不对 → 不算 runner 故障
    assert classify_outcome(1, "Cannot connect to the Docker daemon", c) == "normal"


def test_classify_runner_failure_outranks_denial():
    """命令没跑（runner 故障）优先于「被挡住」（拒绝）。"""
    rules = (RunnerFailureRule(allowed_exit_codes=(125,), fatal_signatures=("no such image",)),)
    c = _confined(signatures=["read-only file system"], rules=rules)
    stderr = "Read-only file system\nUnable to find image: no such image"
    assert classify_outcome(125, stderr, c) == "runner_failed"


def test_classify_informational_lines_excluded():
    """信息行不构成故障证据；旁边的致命行仍有效。"""
    rules = (
        RunnerFailureRule(
            allowed_exit_codes=(125,),
            fatal_signatures=("landlock-run:",),
            informational_lines=("landlock-run: partial enforcement (older landlock abi)",),
        ),
    )
    c = _confined(rules=rules)
    info_only = "landlock-run: partial enforcement (older Landlock ABI)"
    assert classify_runner_failure(125, info_only, rules) is None
    assert classify_outcome(125, info_only, c) == "normal"
    assert classify_runner_failure(125, f"{info_only}\nlandlock-run: boom", rules) is not None


def test_classify_blank_signatures_ignored():
    rules = (RunnerFailureRule(allowed_exit_codes=(125,), fatal_signatures=("   ",)),)
    assert classify_runner_failure(125, "anything", rules) is None


def test_matches_signature():
    assert matches_signature(1, "READ-ONLY FILE SYSTEM", ["read-only file system"])
    assert not matches_signature(0, "read-only file system", ["read-only file system"])
    assert not matches_signature(1, "", [""])


def test_denial_marker():
    """拒绝标记（已迁到 escalation：要与升权提示成对出现）。"""
    assert sandbox_denial_marker("read-only") == "[sandbox: file access denied under read-only mode]"


# ── DockerProvider ──


def test_docker_argv_shape():
    p = DockerProvider(image=DEFAULT_SANDBOX_IMAGE)
    c = p.confine(["bash", "-c", "ls -v"], _policy(root="/tmp/ws"))

    assert c.argv[0] == "docker"
    assert c.enforcement == "full"
    assert "read-only file system" in c.denial_signatures
    assert c.runner_failure_rules and c.runner_failure_rules[0].allowed_exit_codes == (125,)

    # 关键隔离参数
    assert "--read-only" in c.argv
    assert ["-v", "/tmp/ws:/workspace"] == c.argv[c.argv.index("-v"):c.argv.index("-v") + 2]
    assert c.argv[c.argv.index("-w") + 1] == "/workspace"
    assert "--cap-drop" in c.argv and c.argv[c.argv.index("--cap-drop") + 1] == "ALL"

    # 调用方 argv 必须完整位于镜像名之后（这是真正的注入边界）
    img_at = c.argv.index(DEFAULT_SANDBOX_IMAGE)
    assert c.argv[img_at + 1:] == ["bash", "-c", "ls -v"]


def test_docker_read_only_mounts_workspace_ro():
    """read-only 必须把工作区挂成 :ro——绑定挂载会覆盖根文件系统的只读属性。

    回归用例：早期实现只加 --read-only 而未收窄绑定挂载，导致 read-only 模式下
    工作区仍可写（端到端实测发现）。
    """
    p = DockerProvider()
    ro = p.confine(["bash", "-c", "true"], _policy(mode="read-only", root="/tmp/ws"))
    assert ro.argv[ro.argv.index("-v") + 1] == "/tmp/ws:/workspace:ro"
    assert "--read-only" in ro.argv
    assert "--tmpfs" not in ro.argv, "临时区域只在 workspace-write 提供"

    rw = p.confine(["bash", "-c", "true"], _policy(mode="workspace-write", root="/tmp/ws"))
    assert rw.argv[rw.argv.index("-v") + 1] == "/tmp/ws:/workspace"
    assert "--tmpfs" in rw.argv


def test_docker_rejects_injection():
    p = DockerProvider()
    for bad in (["--privileged"], ["-v", "/:/host"], ["--network", "host"], ["--cap-add", "SYS_ADMIN"]):
        expect_raises(ValueError, p.confine, bad, _policy())
    # 命令行里的短选项在单个元素内，不是独立 token，不应误伤
    assert_no_isolation_injection(["bash", "-c", "ls -v -p --network"])


def test_docker_rejects_empty_and_relative_root():
    p = DockerProvider()
    expect_raises(ValueError, p.confine, [], _policy())
    expect_raises(SandboxUnavailableError, p.confine, ["true"], _policy(root="relative/path"))


# ── 只读挂载根（对 DeepSeek 的扩展：Docker 后端的读轴）──


def test_normalize_host_path():
    """绝对性校验。相对路径必须被拒——docker 会把它当成 named volume 静默
    创建一个空卷挂上去，表现为「挂载成功但目录是空的」，最难排查的一类失败。
    """
    assert normalize_host_path("D:/jk/Papers") == "D:/jk/Papers"
    assert normalize_host_path("D:\\jk\\Papers") == "D:/jk/Papers"
    assert normalize_host_path("/srv/data") == "/srv/data"
    assert normalize_host_path("  D:/jk  ") == "D:/jk"
    for bad in ("relative/path", "jk", "./x", "", "   "):
        expect_raises(ValueError, normalize_host_path, bad)


def test_parse_read_roots():
    assert parse_read_roots(None) == ()
    assert parse_read_roots("") == ()
    assert parse_read_roots("D:/a") == ("D:/a",)
    assert parse_read_roots("D:/a, D:/b ,") == ("D:/a", "D:/b")


def test_build_read_mounts_names_and_dedup():
    """挂载名取 basename —— 模型 ``ls /mnt/read`` 就能自发现，不依赖外部映射表。
    重名加序号；同一路径配两次会让 docker 因挂载点冲突启动失败，故去重。
    """
    m = build_read_mounts(["D:/jk/Papers", "D:/other/Papers", "D:/jk/Papers"])
    assert m == [
        ("D:/jk/Papers", f"{CONTAINER_READ_ROOT}/Papers"),
        ("D:/other/Papers", f"{CONTAINER_READ_ROOT}/Papers-2"),
    ]
    assert build_read_mounts(["D:\\jk\\Docs"])[0][0] == "D:/jk/Docs"
    assert build_read_mounts([]) == []
    # 配置错误在解析期就炸，不留到容器启动时才报
    expect_raises(ValueError, build_read_mounts, ["relative"])


def test_docker_mounts_read_roots_ro():
    """只读挂载恒为 :ro，且两种受限模式下完全一致——它只拓宽「读得到」，
    不拓宽「写得进」。这是本轴与 danger-full-access 的本质区别。
    """
    p = DockerProvider()
    for mode in ("read-only", "workspace-write"):
        c = p.confine(
            ["bash", "-c", "true"],
            SandboxPolicy(
                mode=mode, workspace_root="/tmp/ws",
                read_roots=("D:/jk/Papers", "D:/jk/Docs"),
            ),
        )
        for name in ("Papers", "Docs"):
            spec = f"D:/jk/{name}:{CONTAINER_READ_ROOT}/{name}:ro"
            assert spec in c.argv, f"{mode} 下 {name} 未挂载或不是 :ro"
            assert c.argv[c.argv.index(spec) - 1] == "-v"
        # 首个 -v 仍是工作区（调用方与既有测试据此定位主挂载）
        tail = "/workspace" if mode == "workspace-write" else "/workspace:ro"
        assert c.argv[c.argv.index("-v") + 1] == f"/tmp/ws:{tail}"


def test_docker_no_read_roots_leaves_argv_unchanged():
    """未配置只读根时不产生任何多余参数（扩展对既有部署零影响）。"""
    c = DockerProvider().confine(["bash", "-c", "true"], _policy())
    joined = " ".join(c.argv)
    assert CONTAINER_READ_ROOT not in joined
    assert READ_ROOTS_ENV not in joined


def test_resolve_policy_carries_read_roots():
    """读轴必须被策略解析带到底——executor 构造 SandboxPolicy 时靠它传递。"""
    assert resolve_policy(workspace_root="/tmp/ws", read_roots=("D:/jk",)).read_roots == ("D:/jk",)
    assert resolve_policy(workspace_root="/tmp/ws").read_roots == ()


def test_tool_schema_carries_read_roots():
    """工具描述带上只读目录映射——不说，模型就不知道那些路径存在，会去猜
    ``D:\\...`` 然后撞 No such file or directory，并误判成「文件不存在」。
    """
    from backend.tool_system.sandbox.runtime import _BASH_SCHEMA, _schema_with_read_roots
    old = os.environ.get(READ_ROOTS_ENV)
    try:
        os.environ[READ_ROOTS_ENV] = "D:/jk/Papers"
        s = _schema_with_read_roots(_BASH_SCHEMA)
        assert f"{CONTAINER_READ_ROOT}/Papers" in s["description"]
        assert "D:/jk/Papers" in s["description"]
        # 必须是副本：不能污染模块级常量（否则开关切换后残留）
        assert CONTAINER_READ_ROOT not in _BASH_SCHEMA["description"]

        os.environ.pop(READ_ROOTS_ENV, None)
        assert _schema_with_read_roots(_BASH_SCHEMA) is _BASH_SCHEMA
    finally:
        if old is None:
            os.environ.pop(READ_ROOTS_ENV, None)
        else:
            os.environ[READ_ROOTS_ENV] = old


# ── 工作区 ──


def test_assert_root_is_safe_rejects_repo_and_backend():
    repo = str(repo_root())
    expect_raises(ValueError, assert_root_is_safe, repo)
    expect_raises(ValueError, assert_root_is_safe, str(repo_root() / "backend"))
    expect_raises(ValueError, assert_root_is_safe, str(repo_root() / "backend" / "tool_system"))
    expect_raises(ValueError, assert_root_is_safe, "/")           # 仓库根的祖先
    expect_raises(ValueError, assert_root_is_safe, "relative/dir")  # 非绝对路径


def test_assert_root_is_safe_allows_independent_dir():
    with tempfile.TemporaryDirectory() as d:
        assert_root_is_safe(d)  # 不应抛异常


def test_resolve_workspace_root():
    with tempfile.TemporaryDirectory() as d:
        root = os.path.join(d, "workspaces")
        assert resolve_workspace_root(root) == str(os.path.realpath(root))
        assert os.path.isdir(root)
    if not os.environ.get("SANDBOX_WORKSPACE_ROOT"):
        expect_raises(SandboxUnavailableError, resolve_workspace_root, None)
    expect_raises(ValueError, resolve_workspace_root, str(repo_root()))


def test_session_workspace_and_container_path():
    with tempfile.TemporaryDirectory() as d:
        ws = session_workspace(d, "abc123")
        assert ws == os.path.join(os.path.realpath(d), "abc123")
        # 被挂载到 /workspace 的是**会话工作区**，不是全局根
        assert to_container_path(ws, ws) == "/workspace"
        assert to_container_path(os.path.join(ws, "a", "b.txt"), ws) == "/workspace/a/b.txt"
        # 会话工作区在全局根之下，但不该被映射到 /workspace（那样会跨会话可见）
        assert to_container_path(ws, d) == "/workspace/abc123"
        expect_raises(ValueError, to_container_path, "/etc/passwd", ws)
        expect_raises(ValueError, session_workspace, d, "../escape")
        expect_raises(ValueError, session_workspace, d, "")


# ── docker e2e ──


def _docker_available() -> bool:
    if shutil.which("docker") is None:
        return False
    try:
        return subprocess.run(["docker", "info"], capture_output=True, timeout=30).returncode == 0
    except Exception:  # noqa: BLE001
        return False


def test_docker_e2e():
    """真实容器：工作区内可写、工作区外被 EROFS 拒绝、read-only 下工作区不可写。

    必须使用**配置的工作区根**而非容器内临时目录——Docker-outside-of-Docker 下
    daemon 解析的是宿主路径，容器内 ``tempfile`` 目录在 daemon 侧不存在
    （见 docs/sandbox-design.md §8.1）。
    """
    if not _docker_available():
        print("      SKIP — docker 不可用（容器内需挂载 /var/run/docker.sock 并安装 docker CLI）")
        return
    if not os.environ.get(WORKSPACE_ROOT_ENV):
        print(f"      SKIP — 未设置 {WORKSPACE_ROOT_ENV}（daemon 侧无法解析容器内临时目录）")
        return
    try:
        root = resolve_workspace_root()
    except Exception as e:  # noqa: BLE001
        print(f"      SKIP — 工作区根不可用: {e}")
        return

    provider = DockerProvider()
    ws = session_workspace(root, "selftest-e2e")
    probe_workspace_root(root)
    assert not os.path.exists(os.path.join(root, ".sandbox-probe")), "探测文件应被清理"

    def run(argv, policy):
        c = provider.confine(argv, policy)
        proc = subprocess.run(c.argv, capture_output=True, text=True, timeout=180)
        return proc, c

    rw = _policy(mode="workspace-write", root=ws)
    ro = _policy(mode="read-only", root=ws)

    inside, c1 = run(["sh", "-c", "echo hi > inside.txt && cat inside.txt"], rw)
    assert inside.returncode == 0, inside.stderr
    assert classify_outcome(inside.returncode, inside.stderr, c1) == "normal"
    assert os.path.exists(os.path.join(ws, "inside.txt")), "工作区内写入应落到宿主"

    outside, c2 = run(["sh", "-c", "echo hi > /outside.txt"], rw)
    assert outside.returncode != 0, "工作区外写入应当失败"
    assert classify_outcome(outside.returncode, outside.stderr, c2) == "denied", outside.stderr

    # 回归：read-only 必须把工作区挂成 :ro，否则绑定挂载会绕过只读根文件系统
    blocked, c3 = run(["sh", "-c", "echo hi > blocked.txt"], ro)
    assert blocked.returncode != 0, "read-only 下写工作区应当失败"
    assert classify_outcome(blocked.returncode, blocked.stderr, c3) == "denied", blocked.stderr
    assert not os.path.exists(os.path.join(ws, "blocked.txt"))

    try:
        os.remove(os.path.join(ws, "inside.txt"))
    except OSError:
        pass


# ── 策略解析 ──


def test_resolve_mode_priority():
    assert resolve_mode(
        explicit_mode="read-only", session_override="workspace-write", config_default="danger-full-access"
    ) == "read-only"
    assert resolve_mode(session_override="read-only", config_default="danger-full-access") == "read-only"
    assert resolve_mode(config_default="read-only") == "read-only"
    assert resolve_mode() == "workspace-write"
    expect_raises(ValueError, resolve_mode, config_default="bogus")


def test_resolve_policy_carries_root_and_session():
    p = resolve_policy(workspace_root="/tmp/ws", session_id="s1", config_default="read-only")
    assert p.mode == "read-only"
    assert p.workspace_root == "/tmp/ws"
    assert p.session_id == "s1"
    # danger-full-access 也走同一解析（消费方据此决定是否绕过 provider）
    assert resolve_policy(workspace_root="/tmp/ws", explicit_mode="danger-full-access").mode == "danger-full-access"


# ── SandboxExecutor（FakeProvider，不需要 docker）──


class _IdentityProvider(SandboxProvider):
    """把 argv 原样返回的 provider：只验证执行链与分类，不做真实约束。"""

    def __init__(self, signatures=(), rules=(), fail_with=None):
        self._signatures = tuple(signatures)
        self._rules = tuple(rules)
        self._fail_with = fail_with

    def confine(self, argv, policy):
        if self._fail_with is not None:
            raise SandboxUnavailableError(policy.mode, self._fail_with)
        return ConfinedArgv(
            argv=list(argv),
            enforcement="full",
            denial_signatures=self._signatures,
            runner_failure_rules=self._rules,
        )


def _descriptor(runtime="shell", timeout_s=30.0):
    return ToolDescriptor(
        name="bash" if runtime == "shell" else "python",
        type="sandbox",
        transport="",
        server_id=0,
        schema={"name": "x", "description": "", "inputSchema": {}},
        sandbox=SandboxToolConfig(runtime=runtime, image="unused", timeout_s=timeout_s),
    )


def _ctx(policy):
    return ToolContext(session_id="s1", sandbox_policy=policy)


def test_build_sandbox_argv():
    shell = SandboxToolConfig(runtime="shell", image="x")
    assert build_sandbox_argv(shell, {"command": "ls"}) == ["bash", "-c", "ls"]
    assert build_sandbox_argv(SandboxToolConfig(runtime="python", image="x"), {"code": "print(1)"}) == [
        "python", "-c", "print(1)"
    ]
    expect_raises(ValueError, build_sandbox_argv, shell, {})
    expect_raises(ValueError, build_sandbox_argv, shell, {"command": "   "})
    expect_raises(ValueError, build_sandbox_argv, SandboxToolConfig(runtime="ruby", image="x"), {"command": "x"})


def test_executor_normal():
    ex = SandboxExecutor(provider=_IdentityProvider())
    res = asyncio.run(ex.execute(_descriptor(), {"command": "echo hi"}, _ctx(_policy())))
    assert res["exit_code"] == 0
    assert res["stdout"].strip() == "hi"
    assert res["sandbox"] == {"mode": "workspace-write", "enforcement": "full", "outcome": "normal"}


def test_executor_python_runtime():
    ex = SandboxExecutor(provider=_IdentityProvider())
    res = asyncio.run(ex.execute(_descriptor("python"), {"code": "print(1 + 1)"}, _ctx(_policy())))
    assert res["stdout"].strip() == "2"


def test_executor_denied_carries_marker():
    ex = SandboxExecutor(provider=_IdentityProvider(signatures=("read-only file system",)))
    res = asyncio.run(ex.execute(
        _descriptor(), {"command": "echo 'Read-only file system' >&2; exit 1"}, _ctx(_policy())
    ))
    assert res["sandbox"]["outcome"] == "denied"
    # notice = 拒绝标记（首行）+ 升权提示（存在更宽模式时追加）
    assert res["notice"].splitlines()[0] == sandbox_denial_marker("workspace-write")


def test_executor_runner_failed():
    rules = (
        RunnerFailureRule(allowed_exit_codes=(125,), fatal_signatures=("cannot connect to the docker daemon",)),
    )
    ex = SandboxExecutor(provider=_IdentityProvider(rules=rules))
    res = asyncio.run(ex.execute(
        _descriptor(),
        {"command": "echo 'Cannot connect to the Docker daemon' >&2; exit 125"},
        _ctx(_policy()),
    ))
    assert res["sandbox"]["outcome"] == "runner_failed"
    assert "基础设施故障" in res["notice"]


def test_executor_fail_closed_without_policy():
    ex = SandboxExecutor(provider=_IdentityProvider())
    res = asyncio.run(ex.execute(_descriptor(), {"command": "echo hi"}, None))
    assert "error" in res


def test_executor_fail_closed_without_provider():
    ex = SandboxExecutor(provider=None)  # 装配层也没装配
    res = asyncio.run(ex.execute(_descriptor(), {"command": "echo hi"}, _ctx(_policy())))
    assert "error" in res


def test_executor_provider_unavailable():
    ex = SandboxExecutor(provider=_IdentityProvider(fail_with="daemon unreachable"))
    res = asyncio.run(ex.execute(_descriptor(), {"command": "echo hi"}, _ctx(_policy())))
    assert res["sandbox"]["unavailable"] is True


def test_executor_danger_full_access_bypasses_provider():
    ex = SandboxExecutor(provider=_IdentityProvider(fail_with="provider 不应被调用"))
    policy = SandboxExecutionPolicy(mode="danger-full-access", workspace_root="/tmp/ws")
    res = asyncio.run(ex.execute(_descriptor(), {"command": "echo hi"}, _ctx(policy)))
    assert res["exit_code"] == 0
    assert "sandbox" not in res


def test_executor_missing_config():
    ex = SandboxExecutor(provider=_IdentityProvider())
    d = ToolDescriptor(name="x", type="sandbox", transport="", server_id=0, schema={})
    res = asyncio.run(ex.execute(d, {"command": "echo hi"}, _ctx(_policy())))
    assert "error" in res


# ── 会话模式投影 + 轨迹投影 ──


def _ev(seq, type_, data):
    return SessionEvent(type=type_, seq=seq, time=float(seq), data=data)


def test_sandbox_projection_find_last_and_isomorphism():
    events = [
        _ev(1, "session/seed", {"role": "system", "content": ""}),
        _ev(2, SANDBOX_MODE, {"mode": "read-only"}),
        _ev(3, "user/message", {"content": "hi"}),
        _ev(4, SANDBOX_MODE, {"mode": "workspace-write"}),
    ]
    assert project_sandbox_mode(events) == "workspace-write"
    p = SandboxModeProjection()
    for e in events:
        p.handle(e)
    assert p.snapshot() == project_sandbox_mode(events), "增量投影与回放 fold 必须同构"


def test_sandbox_projection_empty_and_invalid():
    assert project_sandbox_mode([]) is None
    assert project_sandbox_mode([_ev(1, SANDBOX_MODE, {"mode": "bogus"})]) is None
    assert project_sandbox_mode([_ev(1, SANDBOX_MODE, {})]) is None
    assert project_sandbox_mode([_ev(1, "user/message", {"content": "x"})]) is None


def _tool_result_events(sandbox, result):
    return [
        _ev(1, "turn/start", {}),
        _ev(2, "step/start", {"step": 1}),
        _ev(3, TOOL_CALL, {"tool": "bash", "args": {"command": "ls"}, "tool_call_id": "c1"}),
        _ev(4, TOOL_RESULT, {
            "tool": "bash", "tool_call_id": "c1",
            "content": json.dumps(result, ensure_ascii=False),
            "sandbox": sandbox,
        }),
    ]


def test_trajectory_projection_carries_sandbox_facts():
    events = _tool_result_events(
        {"mode": "workspace-write", "enforcement": "full", "outcome": "normal"},
        {"exit_code": 0, "stdout": "x"},
    )
    p = TrajectoryProjection()
    patch = {}
    for e in events:
        for uev in p.handle(e):
            if uev.get("type") == "traj/update":
                patch = uev["patch"]
    assert patch["sandbox"] == {"mode": "workspace-write", "enforcement": "full", "outcome": "normal"}
    assert patch["status"] == "success"
    assert patch["summary"] == "完成"


def test_trajectory_projection_denied_and_runner_failed():
    for outcome, needle in (("denied", "被沙箱拒绝"), ("runner_failed", "沙箱基础设施故障")):
        events = _tool_result_events(
            {"mode": "read-only", "enforcement": "full", "outcome": outcome},
            {"exit_code": 1, "stderr": "boom"},
        )
        p = TrajectoryProjection()
        patch = {}
        for e in events:
            for uev in p.handle(e):
                if uev.get("type") == "traj/update":
                    patch = uev["patch"]
        assert patch["status"] == "error"
        assert needle in patch["error"]


def test_trajectory_projection_tool_node_has_sandbox_key():
    """tool/call 建立的 node 必须带 sandbox 键（无沙箱工具为 None），UI 类型才稳定。"""
    p = TrajectoryProjection()
    out = []
    for e in (_ev(1, "turn/start", {}), _ev(2, "step/start", {"step": 1}),
              _ev(3, TOOL_CALL, {"tool": "query_orders", "args": {}, "tool_call_id": "c1"})):
        out.extend(p.handle(e))
    node = next(u["node"] for u in out if u.get("type") == "traj/open")
    assert "sandbox" in node and node["sandbox"] is None


def test_registry_hot_registered_builtin_is_listed():
    """内建工具注册后必须同时进统一索引（回归：曾在 init() 后注册而列表里看不到）。"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    r.register_builtin(_descriptor())
    assert [d.name for d in r.descriptors()] == ["bash"]
    assert r.source_label(r.descriptors()[0]) == "builtin"


def test_registry_unregister_builtin():
    """能力停用：注销后工具从统一索引消失（模型看不到，而不是调用才失败）。"""
    from backend.tool_system.registry.registry import ToolRegistry

    r = ToolRegistry()
    r.register_builtin(_descriptor())
    assert [d.name for d in r.descriptors()] == ["bash"]
    r.unregister_builtin(["bash"])
    assert r.descriptors() == []
    r.unregister_builtin(["bash", "nonexistent"])  # 幂等


# ── 升权：完整升权链（approve_escalation）；审批通道本身的用例在
#    backend/interaction/approval/test_approval.py ──


class _FakeApprover:
    """替身审批方：记录收到的申请，返回预设结果（实现 EscalationApprover 协议）。"""

    def __init__(self, outcome: str = "allowed-once"):
        self.outcome = outcome
        self.requests = []

    async def request(self, req):
        self.requests.append(req)
        return self.outcome


class _RecordingSink:
    """记录 emit 到的事件（替身：只验证 Executor 发了什么，不做持久化决策）。"""

    def __init__(self):
        self.events = []

    async def emit(self, event):
        self.events.append(event)


_ESCALATION_KW = dict(agent="agent-a", tool_name="bash", tool_call_id="c1")


def _esc_error(coro, exc_type):
    """跑一次升权并返回指定异常（断言它一定抛）。"""
    try:
        asyncio.run(coro)
    except exc_type as e:
        return e
    raise AssertionError(f"预期 {exc_type.__name__}，但没有抛")


def test_escalation_wider_modes_is_a_strict_ladder():
    assert WIDER_MODES["read-only"] == ("workspace-write", "danger-full-access")
    assert WIDER_MODES["workspace-write"] == ("danger-full-access",)
    # 最宽模式无条目：没有可升的目标（DSH 的 WIDER_MODES 同构）
    assert "danger-full-access" not in WIDER_MODES
    # 升权目标不含 read-only —— 升权只往宽走
    assert "read-only" not in ESCALATION_TARGETS


def test_validate_escalation_args_pairing():
    # 两个都不给 = 普通调用，不是错误
    assert validate_escalation_args({}) == (None, "")
    # 理由去除首尾空白
    assert validate_escalation_args(
        {"sandbox_permissions": "workspace-write", "justification": "  要写工作区  "}
    ) == ("workspace-write", "要写工作区")

    e = _esc_error(
        _returning(validate_escalation_args, {"sandbox_permissions": "workspace-write"}),
        EscalationInvalid,
    )
    assert e.reason == "missing-justification"
    assert "sandbox_permissions requires a justification" in str(e)

    e = _esc_error(_returning(validate_escalation_args, {"justification": "孤立的理由"}), EscalationInvalid)
    assert e.reason == "invalid-pairing"
    assert "only valid together with sandbox_permissions" in str(e)
    assert e.justification == "孤立的理由"       # 原文留痕，供审计

    e = _esc_error(_returning(
        validate_escalation_args,
        {"sandbox_permissions": "workspace-write", "justification": "   "},
    ), EscalationInvalid)
    assert e.reason == "empty-justification"
    assert "expected a non-empty sentence" in str(e)


async def _returning(fn, *args):
    """把同步函数包成 awaitable，走同一条 _esc_error 断言路径。"""
    return fn(*args)


def test_assert_strictly_wider():
    assert_strictly_wider("workspace-write", "read-only")            # 合法
    assert_strictly_wider("danger-full-access", "workspace-write")    # 合法

    e = _esc_error(_returning(assert_strictly_wider, "read-only", "workspace-write"), EscalationInvalid)
    assert e.reason == "not-strictly-wider"
    assert 'to "read-only" is not strictly wider than' in str(e)

    # 同级 / 最宽同级 / 词汇外 —— 一律 fail-closed
    _esc_error(_returning(assert_strictly_wider, "workspace-write", "workspace-write"), EscalationInvalid)
    _esc_error(_returning(assert_strictly_wider, "danger-full-access", "danger-full-access"), EscalationInvalid)
    _esc_error(_returning(assert_strictly_wider, "bogus", "read-only"), EscalationInvalid)


def test_approve_escalation_returns_none_when_nothing_requested():
    """未请求 / 请求等同当前模式 → **没这回事**（None），不是错误、不是拒绝。

    归一化是 DSH #4359 的补丁：会话已在最宽模式时模型仍会反射性地填字段，
    不归一化就会每次被打回、烧 token 甚至死循环。审批方一次都不该被调用。
    """
    approver = _FakeApprover()
    kw = dict(approval=approver, **_ESCALATION_KW)

    assert asyncio.run(approve_escalation({"command": "ls"}, current="read-only", **kw)) is None
    assert asyncio.run(approve_escalation(
        {"sandbox_permissions": "read-only", "justification": "x"}, current="read-only", **kw,
    )) is None
    assert asyncio.run(approve_escalation(
        {"sandbox_permissions": "workspace-write", "justification": "x"}, current="workspace-write", **kw,
    )) is None
    assert approver.requests == []   # 没这回事 → 不打扰审批方


def test_approve_escalation_requires_channel_and_agent():
    """两种「没法问」：没有审批通道、没有 agent —— 都 fail-closed 到拒绝。"""
    args = {"sandbox_permissions": "workspace-write", "justification": "要写工作区"}

    e = _esc_error(
        approve_escalation(args, current="read-only", approval=None, **_ESCALATION_KW),
        EscalationDenied,
    )
    assert e.reason == "no-approval-channel"
    assert e.requested == "workspace-write" and e.justification == "要写工作区"

    e = _esc_error(
        approve_escalation(
            args, current="read-only", approval=_FakeApprover(), agent=None,
            tool_name="bash", tool_call_id="c1",
        ),
        EscalationDenied,
    )
    assert e.reason == "no-agent"


def test_approve_escalation_allowed_once_returns_target_mode():
    approver = _FakeApprover("allowed-once")
    mode = asyncio.run(approve_escalation(
        {"sandbox_permissions": "danger-full-access", "justification": "要装包"},
        current="workspace-write", approval=approver, **_ESCALATION_KW,
    ))
    assert mode == "danger-full-access"

    # 发给审批方的申请：DSH 的 reason 拼法 + 全部上下文
    req = approver.requests[0]
    assert req.reason == "escalate sandbox to danger-full-access: 要装包"
    # 沙箱的模式信息住在 metadata（审批侧不解释它，只透传给 UI）
    assert req.metadata == {
        "kind": "sandbox-escalation", "from": "workspace-write",
        "to": "danger-full-access", "justification": "要装包",
    }
    assert req.agent == "agent-a" and req.tool_name == "bash" and req.tool_call_id == "c1"
    assert req.approval_id           # 与审批结果配对用


def test_approve_escalation_maps_denied_outcomes():
    """三种失败各自映射到机器可读原因 —— 不混成一个「失败」。"""
    for outcome, reason in (("rejected", "human-refused"), ("cancelled", "cancelled"),
                            ("unavailable", "unavailable")):
        e = _esc_error(
            approve_escalation(
                {"sandbox_permissions": "workspace-write", "justification": "要写"},
                current="read-only", approval=_FakeApprover(outcome), **_ESCALATION_KW,
            ),
            EscalationDenied,
        )
        assert e.reason == reason, outcome
        assert e.requested == "workspace-write" and e.approval_id


def test_approve_escalation_calls_on_request_before_awaiting():
    """申请回调必须在**挂起之前**触发，否则前端看不到「正在等谁批」。"""
    order = []

    class _Ordered(_FakeApprover):
        async def request(self, req):
            order.append("approver")
            return await super().request(req)

    async def on_request(req):
        order.append(("on_request", req.metadata["to"]))

    mode = asyncio.run(approve_escalation(
        {"sandbox_permissions": "workspace-write", "justification": "要写"},
        current="read-only", approval=_Ordered(), **_ESCALATION_KW,
        on_request=on_request,
    ))
    assert mode == "workspace-write"
    assert order == [("on_request", "workspace-write"), "approver"]


def test_escalation_hint_marker_visibility():
    hint = escalation_hint_marker("read-only", can_ask_human=True)
    assert "escalation available" in hint and "narrowest wider mode" in hint
    assert "retry this exact command once" in hint
    # 与 DSH 一致：每次升权都问人，尾句恒在
    assert "the approval prompt asks the user" in hint
    # 没有审批通道 → 不提示（升权恒不可用，等价 DSH 的 never）
    assert escalation_hint_marker("read-only", can_ask_human=False) == ""
    # 最宽模式没有可升的目标 → 不提示（提示一个必然失败的动作是误导）
    assert escalation_hint_marker("danger-full-access", can_ask_human=True) == ""


def test_sandbox_denial_marker():
    assert sandbox_denial_marker("read-only") == "[sandbox: file access denied under read-only mode]"


def test_tool_schema_carries_escalation_params():
    from backend.tool_system.registry.registry import _to_openai_function
    from backend.tool_system.sandbox.runtime import _BASH_SCHEMA, _with_escalation

    schema = _with_escalation(_BASH_SCHEMA)
    props = schema["inputSchema"]["properties"]
    assert set(ESCALATION_TARGETS) == set(props["sandbox_permissions"]["enum"])
    assert "justification" in props
    assert schema["inputSchema"]["required"] == ["command"]  # 升权参数是可选，不改 required
    # 模块级常量不被改动（返回副本）
    assert "sandbox_permissions" not in _BASH_SCHEMA["inputSchema"]["properties"]

    # enum 必须活到模型手里 —— 它是封闭词汇的可取值面，丢了模型只能猜。
    wire = _to_openai_function(schema)["function"]["parameters"]["properties"]
    assert wire["sandbox_permissions"]["enum"] == list(ESCALATION_TARGETS)


# ── Executor 接线（策略落地 + 审计 + 提示） ──


class _Channel:
    """临时装配审批服务（测试用）。退出时清空，避免测试间串味。

    返回的是通道本身（供测试直接 resolve/pending）；挂到 ToolContext 上的
    则是 ApprovalService —— 消费者拿到的是服务，不是裸通道。
    """

    def __init__(self, timeout_s=5.0):
        self.channel = InProcessApprovalChannel(timeout_s=timeout_s)
        self.service = ApprovalService(self.channel)

    def __enter__(self):
        set_approval_service(self.service)
        return self.channel

    def __exit__(self, *exc):
        set_approval_service(None)
        return False


def _exec_ctx(policy, *, approval=None, sink=None):
    return ToolContext(
        session_id="s1", tool_call_id="c1", agent_id="agent-a",
        sandbox_policy=policy, approval=approval, event_sink=sink,
    )


async def _execute_with_decision(ex, args, policy, ch, decision, sink):
    """跑一次执行；出现待批准申请就按 ``decision`` 裁决（None = 不裁决）。"""
    from backend.interaction.approval import get_approval_service

    task = asyncio.create_task(ex.execute(
        _descriptor(), dict(args),
        _exec_ctx(policy, approval=get_approval_service(), sink=sink),
    ))
    if decision is not None:
        for _ in range(200):
            await asyncio.sleep(0)
            pending = ch.pending()
            if pending:
                ch.resolve(pending[0].approval_id, decision)
                break
        else:
            raise AssertionError("升权申请始终没有挂起——它应当等人工裁决")
    return await task


def test_executor_escalation_requires_human_approval():
    """与 DSH 一致：升权**必须**有人批；批了才按更宽策略执行这一次。"""
    sink = _RecordingSink()
    with _Channel() as ch:
        res = asyncio.run(_execute_with_decision(
            SandboxExecutor(provider=_IdentityProvider()),
            {"command": "echo hi", "sandbox_permissions": "workspace-write",
             "justification": "要写工作区"},
            _policy("read-only"), ch, "allow-once", sink,
        ))

    assert res["exit_code"] == 0
    # sandbox 事实记录的是**实际执行的模式**（获批后），不是请求前的
    assert res["sandbox"]["mode"] == "workspace-write"
    types = [e.type for e in sink.events]
    # 申请事件必须先于结果事件 —— 否则前端在等待期间看不到待批准卡片
    assert types.index(APPROVAL_REQUEST_EVENT) < types.index(SANDBOX_ESCALATION_EVENT)
    ev = sink.events[-1]
    assert ev.data["granted"] is True and ev.data["reason"] == "granted"
    assert ev.data["from"] == "read-only" and ev.data["to"] == "workspace-write"


def test_executor_escalation_refused_keeps_policy():
    """人工拒绝 → 策略不变、照常执行、事实入事件（**不是执行失败**）。"""
    sink = _RecordingSink()
    with _Channel() as ch:
        res = asyncio.run(_execute_with_decision(
            SandboxExecutor(provider=_IdentityProvider()),
            {"command": "echo hi", "sandbox_permissions": "danger-full-access",
             "justification": "要装包"},
            _policy("workspace-write"), ch, "reject", sink,
        ))

    assert res["exit_code"] == 0                         # 不是执行失败
    assert res["sandbox"]["mode"] == "workspace-write"   # 回退到原策略
    ev = sink.events[-1]
    assert ev.data["granted"] is False and ev.data["reason"] == "human-refused"
    assert ev.data["requested"] == "danger-full-access"  # 请求与理由留痕


def test_executor_invalid_escalation_fails_the_call():
    """升权参数非法 → 整条调用作废，错误文本原样回给模型（与 DSH 同：让调用失败而不是静默降级）。"""
    sink = _RecordingSink()
    with _Channel() as ch:
        res = asyncio.run(SandboxExecutor(provider=_IdentityProvider()).execute(
            _descriptor(),
            {"command": "echo hi", "sandbox_permissions": "read-only"},   # 缺 justification
            _exec_ctx(_policy("workspace-write"), approval=ApprovalService(ch), sink=sink),
        ))
    assert "error" in res
    assert "stdout" not in res          # 命令根本没跑
    assert "sandbox_permissions requires a justification" in res["error"]
    assert res["sandbox"]["escalation_invalid"] == "missing-justification"
    # 非法请求**不该打扰审批方**
    assert ch.pending() == []
    # 但事实要留痕
    assert sink.events[-1].data["reason"] == "missing-justification"


def test_executor_denied_notice_carries_escalation_hint():
    ex = SandboxExecutor(provider=_IdentityProvider(signatures=("read-only file system",)))
    with _Channel():
        res = asyncio.run(ex.execute(
            _descriptor(), {"command": "echo 'Read-only file system' >&2; exit 1"},
            _exec_ctx(_policy("read-only")),
        ))
    assert res["notice"].startswith(sandbox_denial_marker("read-only"))
    assert "escalation available" in res["notice"]

    # 没有审批通道时不提示 —— 升权恒不可用，提示只会让模型白烧一轮
    set_approval_service(None)
    res = asyncio.run(ex.execute(
        _descriptor(), {"command": "echo 'Read-only file system' >&2; exit 1"},
        _exec_ctx(_policy("read-only")),
    ))
    assert "escalation available" not in res["notice"]


def test_executor_suppresses_hint_after_refused_escalation():
    """刚被拒的升权请求**不得**再收到「可以升权」提示——那等于请模型重试。"""
    ex = SandboxExecutor(provider=_IdentityProvider(signatures=("read-only file system",)))
    args = {
        "command": "echo 'Read-only file system' >&2; exit 1",
        "sandbox_permissions": "danger-full-access",
        "justification": "要装包",
    }
    with _Channel() as ch:
        res = asyncio.run(_execute_with_decision(
            ex, args, _policy("workspace-write"), ch, "reject", _RecordingSink(),
        ))
    assert res["sandbox"]["outcome"] == "denied"
    assert "escalation available" not in res["notice"]     # ← 熔断点
    assert "escalation refused (human-refused)" in res["notice"]
    assert "do not retry" in res["notice"]


def test_escalation_event_lands_in_session_log():
    """装配层出口：sandbox.escalation → Event Log（**记录**，不被任何投影折叠）。"""
    from backend.agents.runtime.session import SessionStore
    from backend.agents.runtime.session.events import SANDBOX_ESCALATION
    from backend.agents.runtime.session.sandbox_projection import project_sandbox_mode
    from backend.agents.runtime.tool_event_sink import SessionToolEventSink

    session = SessionStore().create()
    sink = SessionToolEventSink(session, "call_1")
    asyncio.run(sink.emit(ToolEvent(
        type=SANDBOX_ESCALATION_EVENT, tool_name="bash", session_id=session.header.id,
        data={"from": "read-only", "requested": "workspace-write", "to": "workspace-write",
              "granted": True, "reason": "granted", "justification": "写总结"},
    )))
    evs = [e for e in session.events if e.type == SANDBOX_ESCALATION]
    assert len(evs) == 1
    assert evs[0].data["tool_call_id"] == "call_1" and evs[0].data["granted"] is True
    # 关键：升权事件**不**参与策略解析（否则一次性授权会变成持久放权）
    assert project_sandbox_mode(session.events) is None
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
