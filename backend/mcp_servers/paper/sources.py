"""论文来源 — arxiv 搜索 + PDF 下载 + 文本解析（纯执行，无 LLM）

零依赖策略: arxiv 走官方 HTTP API（Atom XML，标准库解析），PDF 用 pypdf 解析。
"""
import io
import logging
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import aiohttp
from pypdf import PdfReader

logger = logging.getLogger(__name__)

ARXIV_API = "http://export.arxiv.org/api/query"
USER_AGENT = "agent-low-code-platform/0.1 (paper-mcp; contact: dev@localhost)"

# 单块目标字符数（中文 1 字 ≈ 1 token，英文 4 字符 ≈ 1 token，取保守值）
CHUNK_CHARS = 24000
# 最大取回页数
MAX_PDF_PAGES = 200
ARXIV_NS = {"a": "http://www.w3.org/2005/Atom"}


# ━━ 搜索 ━━

async def search_arxiv(query: str, max_results: int = 5) -> list[dict]:
    """arxiv 关键词搜索，返回论文元信息列表（标题/作者/年份/摘要/链接）"""
    params = {
        "search_query": f"all:{query}",
        "max_results": min(int(max_results), 20),
        "sortBy": "relevance",
        "sortOrder": "descending",
    }
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(
                ARXIV_API, params=params,
                headers={"User-Agent": USER_AGENT},
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                xml_text = await resp.text()
    except Exception as e:
        logger.warning(f"arxiv 搜索失败: query={query} error={e}")
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning(f"arxiv 响应解析失败: {e}")
        return []

    papers = []
    for entry in root.findall("a:entry", ARXIV_NS):
        def _text(tag: str) -> str:
            el = entry.find(f"a:{tag}", ARXIV_NS)
            return (el.text or "").strip() if el is not None else ""

        title = " ".join(_text("title").split())
        summary = " ".join(_text("summary").split())
        published = _text("published")[:10]  # YYYY-MM-DD
        authors = [
            (a.find("a:name", ARXIV_NS).text or "").strip()
            for a in entry.findall("a:author", ARXIV_NS)
        ]
        entry_id = _text("id")  # http://arxiv.org/abs/XXXX.XXXXX
        arxiv_id = entry_id.rstrip("/").rsplit("/", 1)[-1]
        papers.append({
            "arxiv_id": arxiv_id,
            "title": title,
            "authors": authors,
            "year": published[:4],
            "published": published,
            "summary": summary,
            "url": f"https://arxiv.org/abs/{arxiv_id}",
        })
    return papers


# ━━ PDF 下载 + 解析 ━━

def _to_pdf_url(url: str) -> str:
    """arxiv 页面/PDF 链接统一转 PDF 直链"""
    u = url.strip()
    if "/abs/" in u:
        arxiv_id = u.rsplit("/abs/", 1)[-1].split("v")[0].split("?")[0]
        return f"https://arxiv.org/pdf/{arxiv_id}"
    if "/pdf/" in u:
        return u
    return u  # 其他来源直接按原样下载


def _extract_arxiv_id(url: str) -> str | None:
    u = url.strip()
    for marker in ("/abs/", "/pdf/"):
        if marker in u:
            return u.rsplit(marker, 1)[-1].split("?")[0]
    return None


async def download_pdf(pdf_url: str) -> bytes:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            pdf_url,
            headers={"User-Agent": USER_AGENT},
            timeout=aiohttp.ClientTimeout(total=60),
        ) as resp:
            resp.raise_for_status()
            return await resp.read()


def parse_pdf(data: bytes) -> str:
    """PDF 字节 → 纯文本"""
    reader = PdfReader(io.BytesIO(data))
    if len(reader.pages) > MAX_PDF_PAGES:
        logger.info(f"PDF 共 {len(reader.pages)} 页，截取前 {MAX_PDF_PAGES} 页")
    pages = reader.pages[:MAX_PDF_PAGES]
    parts = []
    for i, page in enumerate(pages, 1):
        try:
            t = page.extract_text() or ""
        except Exception as e:
            logger.warning(f"第 {i} 页解析失败: {e}")
            t = ""
        if t.strip():
            parts.append(f"[第 {i} 页]\n{t}")
    return "\n\n".join(parts)


def split_chunks(text: str, chunk_chars: int = CHUNK_CHARS) -> list[str]:
    """按字符数分块，尽量在段落边界切分"""
    if not text:
        return []
    chunks = []
    while len(text) > chunk_chars:
        cut = text.rfind("\n\n", 0, chunk_chars)  # 段落边界优先
        if cut < chunk_chars // 2:  # 段落太长没找到，按字符切
            cut = chunk_chars
        chunks.append(text[:cut].strip())
        text = text[cut:]
    if text.strip():
        chunks.append(text.strip())
    return [c for c in chunks if c]


async def fetch_paper_text(url: str) -> dict:
    """下载 PDF → 解析 → 分块。返回 {title?, arxiv_id?, url, chunk_count, chunks}"""
    pdf_url = _to_pdf_url(url)
    logger.info(f"下载 PDF: {pdf_url}")
    data = await download_pdf(pdf_url)
    text = parse_pdf(data)
    chunks = split_chunks(text)
    return {
        "url": url,
        "arxiv_id": _extract_arxiv_id(url),
        "pdf_url": pdf_url,
        "char_count": len(text),
        "chunk_count": len(chunks),
        "chunks": chunks,
    }
