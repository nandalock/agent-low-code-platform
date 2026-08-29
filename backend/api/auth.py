from fastapi import APIRouter

from backend.core.auth import LoginRequest, LoginResponse
from backend.core.auth import login

router = APIRouter(prefix="/api/auth", tags=["Auth"])


@router.post("/login")
def login_route(body: LoginRequest) -> LoginResponse:
    ok, token, error = login(body.username, body.password)
    if ok:
        return LoginResponse(ok=True, token=token)
    return LoginResponse(ok=False, error=error)
