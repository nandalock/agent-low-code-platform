"""论文域磁盘缓存 — 避免重复下载 PDF / 重复调 LLM 总结"""
import json
import logging
import os
import hashlib

logger = logging.getLogger(__name__)

# 容器可写层，重建后自动清空（可接受）；可用 PAPER_CACHE_DIR 覆盖
CACHE_DIR = os.getenv("PAPER_CACHE_DIR", "/root/.cache/paper-mcp")


def _dir_for(kind: str) -> str:
    d = os.path.join(CACHE_DIR, kind)
    os.makedirs(d, exist_ok=True)
    return d


def _key_path(kind: str, key: str) -> str:
    """key → 安全文件名（hash 化，避免非法字符）"""
    h = hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]
    return os.path.join(_dir_for(kind), f"{h}.json")


def get(kind: str, key: str):
    """读缓存，不存在返回 None"""
    p = _key_path(kind, key)
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"缓存读取失败 {p}: {e}")
        return None


def set(kind: str, key: str, value) -> None:
    """写缓存（value 必须是 JSON 可序列化的）"""
    p = _key_path(kind, key)
    try:
        with open(p, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False)
    except OSError as e:
        logger.warning(f"缓存写入失败 {p}: {e}")
