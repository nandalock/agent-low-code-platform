import json

import psycopg2.errors
from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from backend.db.connection import get_conn

router = APIRouter(prefix="/api/workflows", tags=["workflows"])


class RunWorkflowPayload(BaseModel):
    input: str = ""
    user_id: str = ""


class CreateWorkflowPayload(BaseModel):
    name: str = "未命名工作流"
    nodes: list | None = None
    edges: list | None = None


class SaveWorkflowPayload(BaseModel):
    name: str | None = None
    status: str | None = None
    nodes: list | None = None
    edges: list | None = None


@router.get("")
def list_workflows(tenant_id: int = 1):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """SELECT id, name, status, jsonb_array_length(nodes) as node_count,
                          created_at, updated_at
                   FROM workflows WHERE tenant_id = %s ORDER BY updated_at DESC""",
                (tenant_id,),
            )
            rows = cur.fetchall()
    return {"items": rows}


@router.post("")
def create_workflow(payload: CreateWorkflowPayload, tenant_id: int = 1):
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO workflows (tenant_id, name, nodes, edges)
                       VALUES (%s, %s, %s::jsonb, %s::jsonb) RETURNING id""",
                    (tenant_id, payload.name,
                     json.dumps(payload.nodes) if payload.nodes else '[]',
                     json.dumps(payload.edges) if payload.edges else '[]'),
                )
                new_id = cur.fetchone()["id"]
            conn.commit()
        return {"id": new_id}
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "工作流名称已存在")


@router.get("/{workflow_id}")
def get_workflow(workflow_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM workflows WHERE id = %s",
                (workflow_id,),
            )
            row = cur.fetchone()
    if not row:
        raise HTTPException(404, "工作流不存在")
    return row


@router.put("/{workflow_id}")
def save_workflow(workflow_id: int, payload: SaveWorkflowPayload):
    sets = []
    params = []
    if payload.name is not None:
        sets.append("name = %s")
        params.append(payload.name)
    if payload.status is not None:
        sets.append("status = %s")
        params.append(payload.status)
    if payload.nodes is not None:
        sets.append("nodes = %s::jsonb")
        params.append(json.dumps(payload.nodes))
    if payload.edges is not None:
        sets.append("edges = %s::jsonb")
        params.append(json.dumps(payload.edges))
    if not sets:
        raise HTTPException(400, "没有要更新的字段")
    sets.append("updated_at = now()")
    params.append(workflow_id)
    try:
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    f"UPDATE workflows SET {', '.join(sets)} WHERE id = %s",
                    params,
                )
            conn.commit()
        return {"ok": True}
    except psycopg2.errors.UniqueViolation:
        raise HTTPException(409, "工作流名称已存在")


@router.post("/{workflow_id}/run")
async def run_workflow(workflow_id: int, payload: RunWorkflowPayload):
    from backend.engine.run import run_workflow as _run
    return StreamingResponse(
        _run(workflow_id, {"input": payload.input, "user_id": payload.user_id}),
        media_type="text/event-stream",
    )


@router.delete("/{workflow_id}")
def delete_workflow(workflow_id: int):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM workflows WHERE id = %s", (workflow_id,))
        conn.commit()
    return {"ok": True}
