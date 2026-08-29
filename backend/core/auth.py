"""认证：模型 + JWT 服务"""
import os
from datetime import datetime, timedelta, timezone

import jwt
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class LoginResponse(BaseModel):
    ok: bool
    token: str = ""
    error: str = ""


SECRET_KEY = os.getenv("JWT_SECRET", "dev-secret-change-in-production")
ALGORITHM = "HS256"
EXPIRE_HOURS = 24

# Hardcoded user — replace with DB later
USER = {"username": "jk", "password": "123456", "role": "admin"}


def verify_password(plain: str, hashed: str) -> bool:
    # Placeholder — use bcrypt later
    return plain == hashed


def create_token(username: str, role: str) -> str:
    payload = {
        "sub": username,
        "role": role,
        "exp": datetime.now(timezone.utc) + timedelta(hours=EXPIRE_HOURS),
    }
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
    except jwt.PyJWTError:
        return None


def login(username: str, password: str) -> tuple[bool, str, str]:
    if username == USER["username"] and password == USER["password"]:
        token = create_token(username, USER["role"])
        return True, token, ""
    return False, "", "账号或密码错误"
