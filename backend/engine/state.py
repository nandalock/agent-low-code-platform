from typing import Annotated, TypedDict


def _merge_node_results(existing: dict | None, incoming: dict | None) -> dict:
    """Reducer：合并而非覆盖"""
    return {**(existing or {}), **(incoming or {})}


def _append_timeline(existing: list | None, incoming: list | None) -> list:
    """Reducer：追加而非替换"""
    return (existing or []) + (incoming or [])


class WorkflowState(TypedDict):
    input: str
    user_id: str
    tenant_id: int  # 运行租户：run_workflow 从 workflows 行注入（节点不再硬编码 tenant_id=1）
    node_results: Annotated[dict, _merge_node_results]
    node_timeline: Annotated[list, _append_timeline]
    output: str
