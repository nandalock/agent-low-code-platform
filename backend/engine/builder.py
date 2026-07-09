import logging
from collections import defaultdict

from langgraph.graph import StateGraph, END

from backend.engine.state import WorkflowState
from backend.engine import handlers

logger = logging.getLogger(__name__)

MAX_TOTAL_STEPS = 20


def _find_node(nodes: list, node_id: str) -> dict | None:
    for n in nodes:
        if n["id"] == node_id:
            return n
    return None


def _node_type(n: dict) -> str:
    return n.get("data", {}).get("nodeType", "")


def build_workflow(nodes: list, edges: list):
    handlers.reset_visit_counts()

    start_nodes = [n for n in nodes if _node_type(n) == "start"]
    end_nodes = [n for n in nodes if _node_type(n) == "end"]
    if not start_nodes:
        raise ValueError("工作流缺少 start 节点")
    if not end_nodes:
        raise ValueError("工作流缺少 end 节点")

    graph = StateGraph(WorkflowState)

    # 按 source 分组出边
    outgoing_map: dict[str, list[str]] = defaultdict(list)
    for e in edges:
        outgoing_map[e["source"]].append(e["target"])

    start_id = start_nodes[0]["id"]
    end_id = end_nodes[0]["id"]

    # 1. 注册节点
    for n in nodes:
        nid = n["id"]
        ntype = _node_type(n)
        config = n.get("data", {}).get("config", {})

        if ntype == "start":
            graph.add_node(nid, handlers.start_handler)
        elif ntype == "agent":
            agent_key = config.get("agent_key", "")
            if not agent_key:
                raise ValueError(f"Agent 节点 {nid} 缺少 agent_key")
            graph.add_node(nid, handlers.make_agent_handler(nid, agent_key))
        elif ntype == "condition":
            # handler 只做透传记录，路由交给 conditional_edges 的 router
            graph.add_node(nid, handlers.make_condition_handler(nid, config))
        elif ntype == "router":
            agent_key = config.get("agent_key", "router")
            graph.add_node(nid, handlers.make_router_handler(nid, agent_key))
        elif ntype == "end":
            graph.add_node(nid, handlers.end_handler)
        else:
            raise ValueError(f"未知节点类型: {ntype} ({nid})")

    # 2. 处理边
    for src_id, targets in outgoing_map.items():
        src_node = _find_node(nodes, src_id)
        if not src_node:
            continue

        if _node_type(src_node) == "condition":
            config = src_node.get("data", {}).get("config", {})
            branches = config.get("branches", [])
            # path_map: branch_label → target node ID
            path_map: dict[str, str] = {b["label"]: b["target"] for b in branches}
            if not path_map and targets:
                path_map = {t: t for t in targets}

            router_fn = handlers.make_condition_router(src_id, config, path_map)
            graph.add_conditional_edges(src_id, router_fn, {**path_map, "__end__": END})
        elif _node_type(src_node) == "router":
            # path_map: agent_key → target node ID (根据下游节点的 agent_key config)
            path_map: dict[str, str] = {}
            for t in targets:
                tn = _find_node(nodes, t)
                tk = tn.get("data", {}).get("config", {}).get("agent_key", "") if tn else ""
                if tk:
                    path_map[tk] = t
                else:
                    path_map[t] = t
            if not path_map:
                path_map = {t: t for t in targets}

            router_fn = handlers.make_router_after(src_id, path_map)
            graph.add_conditional_edges(src_id, router_fn, {**path_map, "__end__": END})
        else:
            for t in targets:
                graph.add_edge(src_id, t)

    graph.set_entry_point(start_id)
    graph.set_finish_point(end_id)
    return graph.compile()
