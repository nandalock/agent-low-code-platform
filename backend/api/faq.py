from fastapi import APIRouter, File, Header, HTTPException, Query, UploadFile

from backend.faq.models import (
    FAQBackfillResult,
    FAQCreate,
    FAQImportResult,
    FAQMatchRequest,
    FAQMatchResponse,
    FAQResponse,
    FAQUpdate,
    FAQVectorizeResult,
)
from backend.faq import service

router = APIRouter(prefix="/api/faqs", tags=["FAQ"])


@router.get("")
def list_faqs(
    tag: str = Query(default=""),
    page: int = Query(default=1, ge=1),
    size: int = Query(default=20, ge=1, le=100),
    x_tenant_id: int = Header(alias="X-Tenant-ID"),
) -> dict:
    rows, total = service.list_faqs(x_tenant_id, tag=tag, page=page, size=size)
    return {"items": rows, "total": total, "page": page, "size": size}


@router.post("", status_code=201)
def create_faq(body: FAQCreate, x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQResponse:
    row = service.create_faq(x_tenant_id, body.question, body.answer, body.tags, vectorize=body.vectorize)
    return FAQResponse(**row)


@router.get("/{faq_id}")
def get_faq(faq_id: int, x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQResponse:
    row = service.get_faq(x_tenant_id, faq_id)
    if not row:
        raise HTTPException(404, "FAQ 不存在")
    return FAQResponse(**row)


@router.put("/{faq_id}")
def update_faq(faq_id: int, body: FAQUpdate, x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQResponse:
    row = service.update_faq(x_tenant_id, faq_id, **body.model_dump(exclude_none=True))
    if not row:
        raise HTTPException(404, "FAQ 不存在")
    return FAQResponse(**row)


@router.delete("/{faq_id}", status_code=204)
def delete_faq(faq_id: int, x_tenant_id: int = Header(alias="X-Tenant-ID")):
    if not service.delete_faq(x_tenant_id, faq_id):
        raise HTTPException(404, "FAQ 不存在")


@router.post("/{faq_id}/vectorize")
def vectorize_faq(faq_id: int, x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQVectorizeResult:
    ok, error = service.vectorize_faq(x_tenant_id, faq_id)
    return FAQVectorizeResult(faq_id=faq_id, success=ok, error=error)


@router.post("/backfill")
def backfill_faqs(x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQBackfillResult:
    result = service.backfill_embeddings(x_tenant_id)
    return FAQBackfillResult(**result)


@router.post("/match")
def match_faq(body: FAQMatchRequest, x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQMatchResponse:
    result = service.match_faq(x_tenant_id, body.question, body.direct_threshold)
    if result:
        faq = FAQResponse(**{k: v for k, v in result.items() if not k.startswith("_")})
        return FAQMatchResponse(
            matched=True,
            faq=faq,
            score=result.get("_score", 0),
            tier=result.get("_tier", ""),
        )
    return FAQMatchResponse(matched=False, faq=None, score=0, tier="none")


@router.post("/import")
def import_faqs(file: UploadFile = File(...), x_tenant_id: int = Header(alias="X-Tenant-ID")) -> FAQImportResult:
    if not file.filename or not file.filename.endswith(".csv"):
        raise HTTPException(400, "只支持 CSV 文件")
    content = file.file.read()
    return service.import_csv(x_tenant_id, content)
