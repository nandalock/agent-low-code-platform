"""标题文本的净化与 UTF-8 安全截断（对照 DSH 的 session-title/normalize.ts）

**照搬参考实现，不照搬技术栈**：正则顺序、字节截断语义、fallback 的「前导词」
都是从 ``packages/session/session-title/src/normalize.ts`` 逐行对齐的——标题是
会显示给人看的一行文本，而这行文本的来源是**不可信的**（用户输入、模型输出），
所以清洗顺序本身是契约的一部分，不是实现细节。

为什么必须是这个顺序（每一步都为下一步清场）：

  1. **去 OSC**（``ESC ]`` / C1 0x9D，以 BEL 或 ``ESC \\`` 或串尾结束）——
     先去掉「整体」，否则第 3 步的 ESC 规则会先把 ``ESC ]`` 啃掉，剩下尾巴
     变成可见的乱字符。``$`` 分支让**未终止**的 OSC 也从这里截断（宁可丢半截，
     不可把控制序列当正文显示）。
  2. **去 CSI**（``ESC [`` 开头的 ``[0-?]*[ -/]*[@-~]``）——SGR 颜色码走这条。
  3. **去剩余 ESC**（``ESC`` + ``@``–``_`` 的两字节序列）。
  4. **去非空白 C0/C1 控制符**——含 DEL(0x7F) 与 C1 区(0x80–0x9F)。刻意**保留**
     ``\\t \\n \\r``：它们是空白，交给第 6 步折叠成单个空格才符合直觉。
  5. **去零宽 / 双向控制符**——这一步不只是「好看」：双向覆写符(U+202A–U+202E)
     能让显示顺序与真实字符顺序不一致，是**欺骗性标题**的载体。
  6. **折叠空白 + 去首尾**。

**按字节截断、不切码点**：中文标题每字 3 字节、emoji 4 字节，按「字符数」截断
会让 80 字节的硬上限在中英混排下失效；而不切码点则是底线——截出半个 UTF-8 序列
只会得到 ``U+FFFD``（乱码）。见 :func:`truncate_title_utf8`。

限额常量对齐参考实现的 cordis.patch.yml（``fallbackMaxWords: 5`` /
``fallbackMaxBytes: 40`` / ``maxTitleBytes: 80``）：fallback 更短（40 字节），
因为它会被后来的 LLM 标题替换；80 字节是所有来源的硬顶。

**源码里不写不可见字符**：正则一律用码点拼装（:func:`_char_class` 与 ``chr()``
常量），不写字面 ESC / NUL。控制字符在源码里是隐形的——diff 看不见、复制粘贴会
丢、code review 审不出来，那才是真正的坑。
"""
import re

# ── 控制字符码点（用 chr() 而非字面量，理由见模块文档末段）──
_ESC = chr(0x1B)      # ESC：转义序列引入符
_BEL = chr(0x07)      # BEL：OSC 的一种终止符
_C1_OSC = chr(0x9D)   # C1 OSC
_C1_CSI = chr(0x9B)   # C1 CSI


def _char_class(ranges: "list[tuple[int, ...]]") -> str:
    """把码点区间拼成正则字符类——避免在源码里写不可见的控制字符。

    Args:
        ranges: 每项为 ``(码点,)`` 单点或 ``(起, 止)`` 闭区间。

    Returns:
        形如 ``[a-z]`` 的字符类字符串。
    """
    body = "".join(
        chr(r[0]) if len(r) == 1 else chr(r[0]) + "-" + chr(r[1]) for r in ranges
    )
    return "[" + body + "]"


# ── 清洗正则（顺序即契约，见模块文档）──

#: Operating-system-command 转义序列（含未终止的尾巴）。
#: 末尾 ``$`` 分支是刻意的：未终止的 OSC 只可能是被截断的垃圾，整段丢掉比留着强。
_OSC_SEQUENCE = re.compile(
    "(?:" + _ESC + r"\]|" + _C1_OSC + r")(?:(?!" + _BEL + r"|" + _ESC + r"\\)[\s\S])*"
    "(?:" + _BEL + r"|" + _ESC + r"\\|$)"
)

#: Control-sequence-introducer 转义（``ESC [`` / C1 0x9B），如 SGR 颜色码。
_CSI_SEQUENCE = re.compile("(?:" + _ESC + r"\[|" + _C1_CSI + r")[0-?]*[ -/]*[@-~]")

#: 剩余的两字节 ESC 控制序列。
_ESC_SEQUENCE = re.compile(_ESC + "[@-_]")

#: 非空白的 C0/C1 控制符（制表符 / 换行 / 回车刻意不在内——它们是空白）。
_CONTROL_CHARACTER = re.compile(
    _char_class([(0x00, 0x08), (0x0B,), (0x0C,), (0x0E, 0x1F), (0x7F, 0x9F)])
)

#: 零宽与双向控制符（可让显示内容具有欺骗性）。
_DIRECTIONAL_CONTROL = re.compile(
    _char_class([
        (0x200B,), (0x200E, 0x200F), (0x202A, 0x202E),
        (0x2060, 0x2064), (0x2066, 0x206F), (0xFEFF,),
    ])
)


# ── 公开限额（对齐参考实现 cordis.patch.yml 的取值）──

#: fallback 的最大词数（按空白切分的「词」）。
FALLBACK_MAX_WORDS = 5
#: fallback 的最大 UTF-8 字节数。必须 ≤ :data:`MAX_TITLE_BYTES`。
FALLBACK_MAX_BYTES = 40
#: **任何来源**（fallback / provider / 用户改名）的标题硬上限（UTF-8 字节）。
MAX_TITLE_BYTES = 80


def _assert_positive_integer(name: str, value: int) -> None:
    """拒绝非法的公开限额（宁可炸在调用点，也不静默截成 0 字节）。"""
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def clean_title_text(text: str) -> str:
    """去控制序列 + 折叠空白 → 一行干净的文本（可能为空串）。

    清洗顺序见模块文档——顺序是契约，改动前先读那一节。
    """
    out = _OSC_SEQUENCE.sub("", text)
    out = _CSI_SEQUENCE.sub("", out)
    out = _ESC_SEQUENCE.sub("", out)
    out = _CONTROL_CHARACTER.sub("", out)
    out = _DIRECTIONAL_CONTROL.sub("", out)
    return " ".join(out.split())


def truncate_title_utf8(text: str, max_bytes: int) -> str:
    """按 **UTF-8 字节**预算截断，**绝不在码点中间切开**。

    逐码点累加字节数，超预算即停——Python 的 ``for ch in str`` 按码点迭代
    （与 JS ``for...of`` 同语义），所以增补平面字符（emoji 等 4 字节）不会被
    劈成两个代理对残留。

    Args:
        text: 已净化的标题文本。
        max_bytes: 正的 UTF-8 字节预算。

    Returns:
        预算内最长的码点前缀。
    """
    _assert_positive_integer("max_bytes", max_bytes)
    if len(text.encode("utf-8")) <= max_bytes:
        return text
    used = 0
    parts: list[str] = []
    for ch in text:
        n = len(ch.encode("utf-8"))
        if used + n > max_bytes:
            break
        parts.append(ch)
        used += n
    return "".join(parts)


def normalize_session_title(text: str, max_bytes: int = MAX_TITLE_BYTES) -> str:
    """净化一条**已被接受**的标题并施加字节预算。

    净化后可能变成空串（标题全是控制符）——调用方负责拒绝，本函数不替它决定。

    Returns:
        终端安全的单行标题（尾部已去空白）。
    """
    return truncate_title_utf8(clean_title_text(text), max_bytes).rstrip()


def fallback_session_title(
    text: str,
    max_words: int = FALLBACK_MAX_WORDS,
    max_bytes: int = FALLBACK_MAX_BYTES,
) -> str:
    """确定性 fallback：取首条合格人类消息的**前导词**。

    纯函数、无 IO、无模型——这正是它能在主回复路径上同步跑完的原因。
    净化后为空（纯空白 / 纯图片消息）返回空串，调用方据此**不写**标题事件。

    Returns:
        两个限额内最长的前导词文本（可能为空）。
    """
    _assert_positive_integer("max_words", max_words)
    words = [w for w in clean_title_text(text).split(" ") if w][:max_words]
    return truncate_title_utf8(" ".join(words), max_bytes).rstrip()
