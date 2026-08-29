import json
import time
import logging
from typing import Callable

from backend.engine.state import WorkflowState

logger = logging.getLogger(__name__)

DEFAULT_MAX_RETRIES = 3
_visit_counts: dict[str, int] = {}


def reset_visit_counts():
    _visit_counts.clear()


def _timeline_entry(node_id: str, label: str, node_type: str, output: str, ms: int) -> list:
    """每个 handler 只返回自己的单条 timeline entry，由 reducer 追加"""
    return [{"node_id": node_id, "label": label, "node_type": node_type, "output": output, "ms": ms}]


async def start_handler(state: WorkflowState) -> dict:
    return {
        "node_timeline": _timeline_entry("start", "开始", "start", state["input"], 0),
    }


def _build_context(state: WorkflowState) -> dict:
    """从 node_results + node_timeline 构建所有上游节点输出的上下文"""
    timeline = state.get("node_timeline") or []
    node_results = state.get("node_results") or {}

    node_info: dict[str, dict] = {}
    for entry in timeline:
        nid = entry["node_id"]
        if nid not in node_info:
            node_info[nid] = {
                "label": entry.get("label", nid),
                "type": entry.get("node_type", "unknown"),
            }

    result = {}
    for nid, output in node_results.items():
        info = node_info.get(nid, {})
        key = info.get("label", nid)
        result[key] = {
            "output": output,
            "type": info.get("type", "unknown"),
        }

    return result


def make_agent_handler(node_id: str, agent_key: str, cache_config: dict | None = None) -> Callable:
    from backend.engine.middlewares import CacheMiddleware
    from backend.agents.config_service import get_agent_definition

    # 读 agent 定义的 cache_policy，用工作流节点配置覆写后传给 CacheMiddleware → SemanticCache → CachePolicyEngine
    cache_policy = None
    if cache_config and cache_config.get("enabled"):
        definition = get_agent_definition(agent_key) or {}
        base_policy = definition.get("cache_policy") or {"base_score": 0.50, "cacheable_intents": [], "block_entities": []}
        overrides: dict[str, object] = {}
        for field in ("base_score", "cacheable_intents", "block_entities", "content_hint", "scorer_weights"):
            if field in (cache_config or {}):
                overrides[field] = cache_config[field]
        cache_policy = {**base_policy, **overrides}

    async def core(state: WorkflowState) -> dict:
        """核心逻辑：调用 agent.reply()"""
        from backend.agents import get_agent

        _visit_counts[node_id] = _visit_counts.get(node_id, 0) + 1

        context = _build_context(state)

        t0 = time.time()
        try:
            agent = get_agent(agent_key)
            reply = await agent.reply(tenant_id=1, question=state["input"], context=context)
            answer = reply.answer
        except Exception as e:
            logger.error(f"[engine] agent {agent_key} error: {e}")
            answer = f"Agent 调用失败: {e}"

        elapsed_ms = int((time.time() - t0) * 1000)

        return {
            "node_results": {node_id: answer},
            "node_timeline": _timeline_entry(node_id, agent_key, "agent", answer, elapsed_ms),
        }

    # 组装 middleware 链（洋葱模型）
    handler = core
    middlewares = []

    if cache_config and cache_config.get("enabled"):
        middlewares.append(CacheMiddleware(node_id, cache_config, cache_policy))

    for mw in reversed(middlewares):
        prev = handler

        async def _wrapped(state, _mw=mw, _prev=prev):
            return await _mw.process(state, node_id, _prev)

        handler = _wrapped

    handler.__name__ = f"agent_{node_id}"
    return handler


def make_condition_handler(node_id: str, config: dict) -> Callable:
    """只做透传记录，不写路由决策。决策由 make_condition_router 完成。"""

    async def handler(state: WorkflowState) -> dict:
        _visit_counts[node_id] = _visit_counts.get(node_id, 0) + 1
        return {
            "node_timeline": _timeline_entry(node_id, "条件判断", "condition", "", 0),
        }

    handler.__name__ = f"condition_{node_id}"
    return handler


def make_condition_router(node_id: str, config: dict, path_map: dict[str, str]) -> Callable:
    """路由决策唯一入口：读 state → 判断条件 → 返回目标 node ID"""

    def router(state: WorkflowState) -> str:
        field = config.get("field", "")
        raw = state.get("node_results", {})
        for part in field.split("."):
            if isinstance(raw, dict) and part in raw:
                raw = raw[part]
            elif isinstance(raw, str):
                break
            else:
                raw = ""
                break
        raw = str(raw)

        op = config.get("op", "contains")
        cmp = config.get("value", "")
        branches: list = config.get("branches", [])

        matched = False
        if op == "equals" and raw == cmp:
            matched = True
        elif op == "contains" and cmp in raw:
            matched = True
        elif op == "startsWith" and raw.startswith(cmp):
            matched = True

        if matched and branches:
            label = branches[0]["label"]
        elif branches:
            label = branches[-1]["label"]
        else:
            return list(path_map.keys())[-1] if path_map else "__end__"

        if label in path_map:
            return label
        return list(path_map.keys())[-1] if path_map else "__end__"

    router.__name__ = f"router_{node_id}"
    return router


def make_router_handler(node_id: str, agent_key: str) -> Callable:

    async def handler(state: WorkflowState) -> dict:
        from backend.agents import get_agent

        _visit_counts[node_id] = _visit_counts.get(node_id, 0) + 1
        context = _build_context(state)

        t0 = time.time()
        try:
            agent = get_agent(agent_key)
            reply = await agent.reply(tenant_id=1, question=state["input"], context=context)
            raw = reply.answer
            trace = reply.trace or {}
            level = trace.get("route_level", "?")
            confidence = trace.get("confidence", 0)
            try:
                parsed = json.loads(raw) if isinstance(raw, str) else {}
                intent = parsed.get("agent_key", raw)
                level = parsed.get("route_level", level)
                confidence = parsed.get("confidence", confidence)
                clarify_msg = parsed.get("message", "")
            except (json.JSONDecodeError, TypeError):
                intent = raw if isinstance(raw, str) else "human_handoff"
                clarify_msg = ""
        except Exception as e:
            logger.error(f"[engine] router {agent_key} error: {e}")
            intent = "human_handoff"
            level = "error"
            confidence = 0
            clarify_msg = ""

        elapsed_ms = int((time.time() - t0) * 1000)

        # router 拦截（置信度过低），直接终止工作流
        if intent is None:
            return {
                "node_results": {node_id: "__clarify__"},
                "node_timeline": _timeline_entry(
                    node_id, "路由拦截", "router", clarify_msg, elapsed_ms,
                ),
            }

        return {
            "node_results": {node_id: intent},
            "node_timeline": _timeline_entry(
                node_id, f"路由 → {intent}", "router",
                f"[{level}] {intent} ({confidence:.0%})", elapsed_ms,
            ),
        }

    handler.__name__ = f"router_{node_id}"
    return handler


def make_router_after(node_id: str, path_map: dict[str, str]) -> Callable:

    def router_fn(state: WorkflowState) -> str:
        intent = state.get("node_results", {}).get(node_id)
        if intent is None:
            return list(path_map.keys())[0] if path_map else "__end__"
        # 路由器拦截，直接结束工作流，返回澄清消息
        if intent == "__clarify__":
            return "__end__"
        intent_str = str(intent)
        if intent_str in path_map:
            return intent_str
        if path_map:
            return list(path_map.keys())[0]
        return "__end__"

    router_fn.__name__ = f"router_after_{node_id}"
    return router_fn


async def end_handler(state: WorkflowState) -> dict:
    last = state["input"]
    timeline = state.get("node_timeline") or []
    if timeline:
        last = str(timeline[-1].get("output", last))
    return {
        "output": last,
        "node_timeline": _timeline_entry("end", "结束", "end", last, 0),
    }
