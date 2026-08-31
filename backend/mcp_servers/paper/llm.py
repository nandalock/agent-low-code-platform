"""工具内部 LLM — OpenAI 兼容调用，map-reduce 长文总结

配置优先级（与 AgentRuntime 的 agent 配置隔离）:
  1. JSON 文件: 本目录 llm.config.json（已 gitignore，不提交）
     {"base_url": "https://api.openai.com/v1", "api_key": "...", "model": "..."}
     模板见 llm.config.example.json；bind-mount 下修改即时生效
  2. 环境变量: PAPER_LLM_BASE_URL / PAPER_LLM_API_KEY / PAPER_LLM_MODEL

总结策略（借鉴 paper-qa 的 map-reduce）:
  map:   每块文本 → 独立要点摘要（并发调用，保留块内关键术语/数字）
  reduce:所有块摘要 → 结构化 MD 成品（标题/摘要/核心贡献/方法/结论/局限）
"""
import asyncio
import json
import logging
import os

import aiohttp

logger = logging.getLogger(__name__)

# JSON 配置文件（gitignore，不提交）
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "llm.config.json")

# 推理模型（如 deepseek-v4-pro）会先消耗 token 做推理再输出答案，
# max_tokens 必须给足，否则 content 为空
SUMMARY_MAX_TOKENS = 4096
MAP_MAX_TOKENS = 2048
REQUEST_TIMEOUT = 180  # 长文总结是重活，放宽超时


class LLMNotConfigured(RuntimeError):
    pass


def _llm_env() -> dict:
    """读取 LLM 配置: 1) llm.config.json 优先 2) 环境变量 fallback"""
    # 1) JSON 文件（本目录 llm.config.json，已 gitignore）
    try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        base_url = (cfg.get("base_url") or "").strip().rstrip("/")
        api_key = (cfg.get("api_key") or "").strip()
        model = (cfg.get("model") or "").strip()
        if base_url and api_key and model:
            return {"base_url": base_url, "api_key": api_key, "model": model}
        if any((base_url, api_key, model)):
            logger.warning("llm.config.json 字段不完整（需要 base_url/api_key/model），回退环境变量")
    except FileNotFoundError:
        pass  # 未创建配置文件，走环境变量
    except (json.JSONDecodeError, OSError) as e:
        logger.warning(f"llm.config.json 读取失败: {e}，回退环境变量")

    # 2) 环境变量（docker-compose 注入）
    base_url = (os.getenv("PAPER_LLM_BASE_URL") or "").strip().rstrip("/")
    api_key = (os.getenv("PAPER_LLM_API_KEY") or "").strip()
    model = (os.getenv("PAPER_LLM_MODEL") or "").strip()
    if not base_url or not api_key or not model:
        raise LLMNotConfigured(
            f"LLM 未配置: 请填写 {CONFIG_FILE}（模板见 llm.config.example.json），"
            "或设置环境变量 PAPER_LLM_BASE_URL / PAPER_LLM_API_KEY / PAPER_LLM_MODEL"
        )
    return {"base_url": base_url, "api_key": api_key, "model": model}


async def chat(messages: list[dict], *, max_tokens: int = SUMMARY_MAX_TOKENS, temperature: float = 0.3) -> str:
    """单次 OpenAI 兼容对话，返回文本内容"""
    env = _llm_env()
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{env['base_url']}/v1/chat/completions",
            headers={"Authorization": f"Bearer {env['api_key']}", "Content-Type": "application/json"},
            json={"model": env["model"], "messages": messages, "max_tokens": max_tokens, "temperature": temperature},
            timeout=aiohttp.ClientTimeout(total=REQUEST_TIMEOUT),
        ) as resp:
            if resp.status != 200:
                body = await resp.text()
                raise RuntimeError(f"LLM 调用失败 HTTP {resp.status}: {body[:300]}")
            result = await resp.json()
    return result.get("choices", [{}])[0].get("message", {}).get("content", "")


# ━━ map-reduce 总结 ━━

_MAP_PROMPT = """你是严谨的论文阅读助手。以下是论文《{title}》的第 {idx}/{total} 部分（共 {chars} 字符）。

要求：
1. 用 {language} 输出这部分的核心要点，markdown 列表格式
2. 保留关键术语、方法名称、数字、结论，不要遗漏
3. 只输出要点本身，不要任何开场白或总结语

文本内容：
{chunk}"""

_REDUCE_PROMPT = """你是严谨的论文总结助手。以下是论文《{title}》的分段要点摘录（来自多个部分）。

请综合这些要点，用 {language} 输出完整的论文总结 markdown，格式如下：

# {title}

> 来源: {url}

## 摘要
（用 3-5 句话概括论文核心内容）

## 核心贡献
（列出主要贡献点，每点一条）

## 方法
（介绍主要方法与技术路线）

## 关键结论
（实验结果与重要结论，带具体数字）

## 局限与不足
（作者指出或可推断的局限）

要求：忠实于原文要点，不编造、不夸大。若某部分信息不足，写"原文未详述"。

分段要点：
{bullet_points}"""


async def _summarize_chunk(title: str, idx: int, total: int, chunk: str, language: str) -> str:
    prompt = _MAP_PROMPT.format(title=title, idx=idx, total=total, chars=len(chunk), language=language, chunk=chunk)
    return await chat(
        [{"role": "system", "content": "你是论文阅读助手，输出简洁、准确、结构化。"},
         {"role": "user", "content": prompt}],
        max_tokens=MAP_MAX_TOKENS,
    )


async def summarize_paper_llm(title: str, url: str, chunks: list[str], language: str = "中文", on_stage=None) -> str:
    """map-reduce: 全文分块 → 并发摘要 → 合并为最终 MD。on_stage(str) 报告阶段进度。"""
    if not chunks:
        raise RuntimeError("论文文本为空，无法总结")

    def _stage(s: str):
        if on_stage:
            on_stage(s)

    # map: 分块并发摘要
    _stage(f"总结中 1/{len(chunks)}")
    if len(chunks) == 1:
        part_summaries = await _summarize_chunk(title, 1, 1, chunks[0], language)
    else:
        part_summaries_list = []
        total = len(chunks)
        sem = asyncio.Semaphore(2)  # 限制并发，避免网关限流

        async def _one(i: int, c: str) -> str:
            async with sem:
                r = await _summarize_chunk(title, i, total, c, language)
            _stage(f"总结中 {i}/{total}")
            return r

        part_summaries_list = await asyncio.gather(*(
            _one(i, c) for i, c in enumerate(chunks, 1)
        ))
        part_summaries = "\n\n".join(part_summaries_list)

    # reduce: 合成最终 MD
    _stage("合并生成 markdown")
    prompt = _REDUCE_PROMPT.format(title=title, url=url, language=language, bullet_points=part_summaries)
    md = await chat(
        [{"role": "system", "content": "你是论文总结助手，输出结构化 markdown。"},
         {"role": "user", "content": prompt}],
        max_tokens=SUMMARY_MAX_TOKENS,
    )
    _stage("完成")
    return md
