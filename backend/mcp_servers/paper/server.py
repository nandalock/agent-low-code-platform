"""论文域 MCP Server — 4 个粗粒度工具（FastMCP，进程 :9002）

设计要点（模式 B: 全文在工具内消化，AgentRuntime 循环只看到列表和成品 MD）:
  - search_papers:       arxiv 搜索 → 论文列表（秒级）
  - fetch_paper_text:    下载 PDF → 解析 → 分块（有磁盘缓存）
  - summarize_paper:     全文 → map-reduce LLM 总结 → MD 成品（★ 重活在工具内）
  - list_papers:         会话内已处理论文（进程内状态，避免 LLM 重复下载）
"""
import logging

from mcp.server.fastmcp import FastMCP

from backend.mcp_servers.paper import sources
from backend.mcp_servers.paper.cache import get as cache_get, set as cache_set
from backend.mcp_servers.paper.llm import LLMNotConfigured, summarize_paper_llm

logger = logging.getLogger(__name__)
mcp = FastMCP("paper-mcp")

# 会话内已处理论文: url → {title, arxiv_id, chunk_count, summarized}
_seen: dict[str, dict] = {}


def get_stage(url: str) -> str:
    """当前论文处理阶段（供 AgentRuntime 轮询 → tool_progress 事件）"""
    meta = _seen.get(url)
    return (meta or {}).get("stage", "")


@mcp.tool()
async def search_papers(query: str, max_results: int = 5) -> list[dict]:
    """搜索 arxiv 学术论文。返回论文列表（标题/作者/年份/摘要/链接），供进一步获取全文或总结。"""
    papers = await sources.search_arxiv(query, max_results)
    for p in papers:
        _seen.setdefault(p["url"], {"title": p["title"], "arxiv_id": p["arxiv_id"], "chunk_count": 0, "summarized": False})
    return papers


@mcp.tool()
async def fetch_paper_text(url: str) -> dict:
    """下载论文 PDF 全文并分块（arxiv 链接或 PDF 直链）。返回文本块列表供阅读。"""
    cached = cache_get("papers", url)
    if cached:
        return cached
    meta = _seen.setdefault(url, {"title": "", "arxiv_id": None, "chunk_count": 0, "summarized": False, "stage": ""})
    meta["stage"] = "下载 PDF"
    data = await sources.fetch_paper_text(url)
    if data["chunk_count"] == 0:
        meta["stage"] = ""
        raise RuntimeError(f"论文解析失败或内容为空: {url}")
    cache_set("papers", url, data)
    meta["chunk_count"] = data["chunk_count"]
    meta["stage"] = ""
    return {"url": url, "chunk_count": data["chunk_count"], "char_count": data["char_count"], "chunks": data["chunks"]}


@mcp.tool()
async def summarize_paper(url: str, language: str = "中文") -> str:
    """把论文全文总结成结构化 markdown（标题/摘要/核心贡献/方法/关键结论/局限）。语言默认中文，可传 'English'。"""
    meta = _seen.setdefault(url, {"title": "", "arxiv_id": None, "chunk_count": 0, "summarized": False, "stage": ""})

    def _stage(s: str):
        meta["stage"] = s  # 阶段状态供 tool_progress 轮询

    md_key = f"{language}::{url}"
    cached = cache_get("summaries", md_key)
    if cached:
        meta["stage"] = ""
        return cached["md"]

    # 全文优先取已 fetch 的缓存，避免重复下载
    _stage("下载 PDF")
    paper = cache_get("papers", url)
    if not paper:
        paper = await sources.fetch_paper_text(url)
        if paper["chunk_count"] == 0:
            raise RuntimeError(f"论文解析失败或内容为空: {url}")
        cache_set("papers", url, paper)
    _stage("解析完成，准备总结")

    title = meta.get("title") or paper.get("arxiv_id") or url
    try:
        md = await summarize_paper_llm(title=title, url=url, chunks=paper["chunks"], language=language, on_stage=_stage)
    except LLMNotConfigured as e:
        meta["stage"] = ""
        raise RuntimeError(f"总结功能未配置: {e}. 请配置 llm.config.json 或 PAPER_LLM_* 环境变量")

    cache_set("summaries", md_key, {"md": md})
    meta["title"] = title
    meta["summarized"] = True
    meta["stage"] = ""
    return md


@mcp.tool()
async def list_papers() -> list[dict]:
    """列出本次会话已处理过的论文（已搜索/已获取/已总结），避免重复下载。"""
    return [
        {"url": url, **info}
        for url, info in _seen.items()
    ]
