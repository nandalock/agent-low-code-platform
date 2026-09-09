"""TrajectoryProjection — Session typed events → UI Trajectory nodes（Conversation Projection）

Event Log 是执行事实（真源）；本模块把 typed SessionEvent 投影为 UI 可直接渲染的
node 序列（think / tool / answer），并产出增量 UI 事件（traj/open|delta|update|close +
usage）供 SSE 实时推送。这是「Session Event → Conversation Node → UI Projection」
的中间层：前端 reducer 只按全量字段值更新，不做任何拼装/猜测。

命名（DSH 词汇对齐）：本包内三个派生视图按用途并列 —— surface.py（模型消息）、
trajectory_projection.py（UI/回放，本文件）、trace_projection.py（执行摘要/telemetry）。
三者都消费同一 Event Log，互不依赖；命名 = "<用途>_projection"。

设计约束：
  - 纯状态机：handle(ev) 逐事件消费；project_trajectory(events) == fold(handle)。
    实时增量与历史回放严格同构（同一事件流 → 同一 node 终态）。
  - Node identity：
      think   = t{turn}.s{step}.think        每 step 至多一个，reasoning delta 累积
      tool    = t{turn}.s{step}.tool.{call_id} tool_call_id 关联 call/progress/result
      answer  = t{turn}.answer               每 turn 一个，text delta 累积 + 段对账
  - 只消费 typed SessionEvent（assistant/chunk kind=thinking|text 等），
    不做字符串解析 / <think> 猜测 / 工具名硬编码。
  - 展示截断只发生在本层（result ≤ 4000 / args ≤ 2000 字符）；
    Session Log 永远保留全文。
"""
import json
import logging

from backend.agents.runtime.session.events import SessionEvent

logger = logging.getLogger(__name__)

MAX_RESULT_CHARS = 4000   # tool result 投影截断上限（Log 保留全文）
MAX_ARGS_CHARS = 2000     # tool args 投影截断上限
_MAX_SUMMARY_CHARS = 120  # 非结构化 result 的 summary 兜底长度

# UI node kind / status 常量
K_THINK = "think"
K_TOOL = "tool"
K_ANSWER = "answer"
ST_OPEN = "open"
ST_DONE = "done"
ST_RUNNING = "running"
ST_SUCCESS = "success"
ST_ERROR = "error"
ST_CANCELLED = "cancelled"


def _cap(s: str, n: int) -> str:
    """截断长文本；Log 保全文，仅投影值截断。"""
    if s is None:
        return s
    if len(s) <= n:
        return s
    return s[:n] + "…(已截断)"


def _classify_result(content: str, sandbox: dict | None = None) -> tuple[str, str | None, str | None]:
    """tool/result 的 content（JSON 字符串）+ 结构化 sandbox 事实 → (status, summary, error)

    沙箱工具：状态由 ``sandbox.outcome`` 决定（命令跑了但被挡住 ≠ 工具出错）。
    其余：error 字段 → error；rows → "N 条结果"；兜底取前 120 字符。
    result 全文（截断后）由调用方另存。
    """
    try:
        obj = json.loads(content)
    except (json.JSONDecodeError, TypeError):
        obj = None
    if isinstance(obj, dict):
        err = obj.get("error")
        if err:
            return ST_ERROR, None, str(err)
    if isinstance(sandbox, dict):
        outcome, mode = sandbox.get("outcome"), sandbox.get("mode")
        if outcome == "denied":
            return ST_ERROR, None, f"被沙箱拒绝（{mode} 模式）"
        if outcome == "runner_failed":
            return ST_ERROR, None, "沙箱基础设施故障（命令未执行）"
        code = obj.get("exit_code") if isinstance(obj, dict) else None
        return ST_SUCCESS, (f"exit {code}" if code else "完成"), None
    if isinstance(obj, dict):
        rows = obj.get("rows")
        if isinstance(rows, (list, dict)):
            return ST_SUCCESS, f"{len(rows)} 条结果", None
    # 非结构化 / 未知结构 → 兜底摘要（保证 summary 有值可显示）
    text = content.strip()
    return ST_SUCCESS, (text[:_MAX_SUMMARY_CHARS] + "…" if len(text) > _MAX_SUMMARY_CHARS else text or None), None


class TrajectoryProjection:
    """增量投影：每 SessionEvent → 0..N 个 UI 事件（dict）。

    单实例只服务一个「事件流消费端」（一次 SSE 请求 / 一次回放）。
    turn/step 从 1 编号，node id 为不透明键（UI 只按 id 引用，不解析）。
    """

    def __init__(self):
        self.turn = 0
        self.cur_step = 0
        self.nodes: list[dict] = []        # 有序 node（插入序 = 执行序）
        self._by_id: dict[str, dict] = {}
        self.usage: list[dict] = []
        self._seg_buf = ""                 # 当前 assistant 段的 text chunk 累积（对账用）
        self._last_msg_at = -1             # 上一个 assistant/message 的 seq（对账边界）

    # ── 外部接口 ──

    def handle(self, ev: SessionEvent) -> list[dict]:
        """消费一条 SessionEvent，返回产生的 UI 事件列表（可为空）。"""
        t, d = ev.type, ev.data
        if t == "turn/start":
            self.turn += 1
            return []
        if t == "step/start":
            self.cur_step = int(d.get("step", 1))
            out = {"type": "step", "step": self.cur_step}
            if d.get("total") is not None:
                out["total"] = d["total"]
            return [out]
        if t == "assistant/chunk":
            return self._on_chunk(d)
        if t == "assistant/message":
            return self._on_assistant_message(d)
        if t == "tool/call":
            return self._on_tool_call(d)
        if t == "tool/progress":
            return self._on_tool_progress(d)
        if t == "tool/result":
            return self._on_tool_result(d)
        if t == "step/end":
            return self._on_step_end(d)
        if t == "turn/end":
            return self._on_turn_end()
        if t == "llm/usage":
            return self._on_usage(d)
        # session/seed、user/message 等：无 UI 投影
        return []

    def finish(self) -> dict:
        """返回当前投影快照（nodes + usage）。事件流完整（turn/end 已到）时即终态。"""
        return {"nodes": list(self.nodes), "usage": list(self.usage)}

    # ── node 基础操作（内部）──

    def _open(self, node: dict, ev_type: str) -> list[dict]:
        self.nodes.append(node)
        self._by_id[node["id"]] = node
        # 事件必须携带发射时刻的快照（拷贝），不是内部 node 的活引用：
        # 后续 _on_chunk 的 node["text"] += / _update 的 node.update 会改写内部 node，
        # 若事件共享同一 dict，SSE 出队序列化时 open 负载已被污染 → 前端把首 delta
        # 既从 open.text 又经 traj/delta 各加一次（每段首词双写）。dict() 浅拷贝即够
        # （node 字段均为字符串/数字等不可变值）。
        return [{"type": ev_type, "node": dict(node)}]

    def _find(self, node_id: str) -> dict | None:
        return self._by_id.get(node_id)

    def _update(self, node: dict, patch: dict) -> list[dict]:
        node.update(patch)
        return [{"type": "traj/update", "id": node["id"], "patch": patch}]

    # ── 事件处理 ──

    def _on_chunk(self, d: dict) -> list[dict]:
        kind, delta = d.get("kind"), d.get("delta")
        if not delta:
            return []
        if kind == "thinking":
            nid = f"t{self.turn}.s{self.cur_step}.think"
            node = self._find(nid)
            if node is None:
                node = {
                    "id": nid, "kind": K_THINK, "status": ST_OPEN,
                    "turn": self.turn, "step": self.cur_step, "text": "",
                }
                out = self._open(node, "traj/open")
            else:
                out = []
            node["text"] += delta  # 首 delta 也须累积（open 时 text=""，统一走 +=）
            out.append({"type": "traj/delta", "id": nid, "field": "thinking", "delta": delta})
            return out
        if kind == "text":
            self._seg_buf += delta
            nid = f"t{self.turn}.answer"
            node = self._find(nid)
            if node is None:
                node = {
                    "id": nid, "kind": K_ANSWER, "status": ST_OPEN,
                    "turn": self.turn, "step": self.cur_step, "text": "",
                }
                out = self._open(node, "traj/open")
            else:
                out = []
            node["text"] += delta
            out.append({"type": "traj/delta", "id": nid, "field": "text", "delta": delta})
            return out
        return []

    def _on_assistant_message(self, d: dict) -> list[dict]:
        """assistant/message = 本 assistant 段的权威文本。

        正常流式下 content == 本段 chunk 累积（_seg_buf）→ no-op 防重复；
        非流式（无 chunk）→ 打开 answer 并写入全文；
        段不一致（流式丢段等）→ 用 content 替换本段累积文本（全量 patch）。
        _seg_buf 在消费后清零，跨步累积文本不受影响。
        """
        content = d.get("content") or ""
        nid = f"t{self.turn}.answer"
        node = self._find(nid)
        seg = self._seg_buf
        out: list[dict] = []
        if node is None:
            if content:
                node = {
                    "id": nid, "kind": K_ANSWER, "status": ST_DONE,
                    "turn": self.turn, "step": self.cur_step, "text": content,
                }
                out = self._open(node, "traj/open")
        else:
            if not content:
                pass  # 纯 tool_calls 步：文本为空，保留已累积
            elif seg and content == seg:
                pass  # 流式完整：与 delta 累积一致，无需变更
            elif seg:
                # 段不一致 → 用权威 content 替换本段累积（全量文本 patch）
                new_text = node["text"][:-len(seg)] + content
                node["text"] = new_text
                out = self._update(node, {"text": new_text})
            else:
                # 无 chunk 段的非空 content（非流式兜底）→ 追加
                new_text = node["text"] + content
                node["text"] = new_text
                out = self._update(node, {"text": new_text})
        self._seg_buf = ""
        return out

    def _on_tool_call(self, d: dict) -> list[dict]:
        call_id = d.get("tool_call_id")
        if not call_id:
            return []
        node = {
            "id": f"t{self.turn}.s{self.cur_step}.tool.{call_id}",
            "kind": K_TOOL, "status": ST_RUNNING,
            "turn": self.turn, "step": self.cur_step,
            "tool": d.get("tool"), "args": _cap(json.dumps(d.get("args", {}), ensure_ascii=False), MAX_ARGS_CHARS),
            "call_id": call_id,
            "stage": None, "seconds": None, "summary": None, "error": None, "result": None,
            "sandbox": None,   # 沙箱事实（mode/enforcement/outcome），tool/result 到达时填充
        }
        return self._open(node, "traj/open")

    def _on_tool_progress(self, d: dict) -> list[dict]:
        node = self._find_tool(d)
        if node is None:
            logger.debug("tool/progress 无对应 tool node（孤儿）: %s", d)
            return []
        return self._update(node, {"stage": d.get("stage"), "seconds": d.get("seconds")})

    def _on_tool_result(self, d: dict) -> list[dict]:
        node = self._find_tool(d)
        if node is None:
            logger.debug("tool/result 无对应 tool node（孤儿）: %s", d)
            return []
        content = d.get("content") or ""
        # 沙箱事实来自事件的结构化字段（AgentLoop 从执行结果提取），不是 content 内的文本
        sandbox = d.get("sandbox") if isinstance(d.get("sandbox"), dict) else None
        status, summary, error = _classify_result(content, sandbox)
        patch: dict = {
            "status": status, "summary": summary, "error": error,
            "result": _cap(content, MAX_RESULT_CHARS),
        }
        if sandbox is not None:
            patch["sandbox"] = sandbox
        return self._update(node, patch)

    def _find_tool(self, d: dict) -> dict | None:
        call_id = d.get("tool_call_id")
        if not call_id:
            return None
        return self._find(f"t{self.turn}.s{self.cur_step}.tool.{call_id}")

    def _on_step_end(self, d: dict) -> list[dict]:
        """关闭本 step 的 think node（reasoning 已完整；无 reasoning 则无 node）。

        幂等：已关闭的 node 不再重复发 close（重复 step/end 事件不产生冗余帧）。
        """
        step = int(d.get("step", self.cur_step))
        node = self._find(f"t{self.turn}.s{step}.think")
        if node is None or node["status"] != ST_OPEN:
            return []
        node["status"] = ST_DONE
        return [{"type": "traj/close", "id": node["id"]}]

    def _on_turn_end(self) -> list[dict]:
        """轮末兜底收口：关闭 open think/answer；running tool 标记 cancelled。

        正常路径（每步 step/end + assistant/message）已逐个关闭；
        异常中断（无 step/end）由这里兜底，保证投影无永久 open node。
        """
        out: list[dict] = []
        for node in self.nodes:
            if node["kind"] == K_TOOL and node["status"] == ST_RUNNING:
                out.extend(self._update(node, {"status": ST_CANCELLED}))
            elif node["kind"] in (K_THINK, K_ANSWER) and node["status"] == ST_OPEN:
                node["status"] = ST_DONE
                out.append({"type": "traj/close", "id": node["id"]})
        return out

    def _on_usage(self, d: dict) -> list[dict]:
        u = {
            "step": d.get("step"),
            "prompt_tokens": d.get("prompt_tokens"),
            "completion_tokens": d.get("completion_tokens"),
            "prompt_cache_hit_tokens": d.get("prompt_cache_hit_tokens"),
            "prompt_cache_miss_tokens": d.get("prompt_cache_miss_tokens"),
        }
        self.usage.append(u)
        return [{"type": "usage", **u}]


def project_trajectory(events: list[SessionEvent]) -> dict:
    """纯函数（回放 fold）：完整事件流 → 终态快照 {"nodes": [...], "usage": [...]}。

    与实时增量投影严格同构：等价于对同一事件流逐个 handle() 后 finish()。
    命名与 trace_projection.project_trace 对称（<视图>_project 均为回放 fold）。
    """
    p = TrajectoryProjection()
    for ev in events:
        p.handle(ev)
    return p.finish()
