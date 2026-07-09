import json
import logging
from typing import AsyncGenerator

from backend.engine.state import WorkflowState
from backend.engine.builder import build_workflow, MAX_TOTAL_STEPS
from backend.engine.handlers import reset_visit_counts, _visit_counts

logger = logging.getLogger(__name__)


def _load_workflow(workflow_id: int) -> dict:
    from backend.db.connection import get_conn
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM workflows WHERE id = %s", (workflow_id,))
            row = cur.fetchone()
    if not row:
        raise ValueError(f"工作流 {workflow_id} 不存在")
    return row


async def run_workflow(workflow_id: int, payload: dict) -> AsyncGenerator[str, None]:
    try:
        wf = _load_workflow(workflow_id)
    except ValueError as e:
        yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        return

    nodes = wf["nodes"] or []
    edges = wf["edges"] or []

    if not nodes:
        yield f"data: {json.dumps({'type': 'error', 'message': '工作流没有节点'})}\n\n"
        return

    try:
        graph = build_workflow(nodes, edges)
    except Exception as e:
        yield f"data: {json.dumps({'type': 'error', 'message': f'构建工作流失败: {e}'})}\n\n"
        return

    reset_visit_counts()

    initial_state: WorkflowState = {
        "input": payload.get("input", ""),
        "user_id": payload.get("user_id", ""),
        "node_results": {},
        "node_timeline": [],
        "output": "",
    }

    logger.info(f"[engine] 开始执行工作流 {workflow_id}, nodes={len(nodes)}, edges={len(edges)}")
    for n in nodes:
        logger.info(f"[engine] node: id={n.get('id')}, type={n.get('data',{}).get('nodeType')}, config={n.get('data',{}).get('config')}")

    final_output = ""

    try:
        async for chunk in graph.astream(initial_state, {"recursion_limit": MAX_TOTAL_STEPS}):
            logger.info(f"[engine] astream chunk keys: {list(chunk.keys())}")
            for v in chunk.values():
                for entry in (v.get("node_timeline") or []):
                    yield f"data: {json.dumps({'type': 'node_done', **entry}, ensure_ascii=False)}\n\n"
                out = v.get("output")
                if out:
                    final_output = str(out)

        yield f"data: {json.dumps({'type': 'workflow_done', 'output': final_output}, ensure_ascii=False)}\n\n"

    except Exception as e:
        logger.error(f"[engine] 执行异常: {e}")
        yield f"data: {json.dumps({'type': 'error', 'message': f'执行异常: {e}'})}\n\n"
