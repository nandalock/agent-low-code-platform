'use client';

// AgentTurn —— 助手回合的「日志流」渲染（参考实现的核心观感）
//
// 与改造前的差别：不再是一个大气泡里塞白色工具卡，而是全宽分组的内容流：
//
//   Think 收起行  ── 24px 一行，展开看全文
//   工具行 ×N     ── 24px 一行、无卡片边框，展开才出 IO 卡
//   markdown 正文 ── 无气泡，直接铺在列宽上
//   尾部统计行    ── token / 耗时 pill + 时间
//
// 数据仍是 lib/trajectory.ts 的 TrajNode[]（那个文件是纯数据层，一行没动）；
// 这里只负责投影。agents 页那份 TrajectoryTimeline 保持原样，不受影响。

import { useEffect, useRef, useState } from 'react';
import {
  Braces, Brain, ChevronDown, ChevronRight, Clock, Database, FileText,
  FolderSearch, Globe, Library, Pencil, Search, Sparkles, Terminal, Wrench,
} from 'lucide-react';
import { W, R } from '../theme';
import {
  type TrajNode, type TrajThinkNode, type TrajToolNode, type UsageRow, type TraceSummary,
  formatToolArgs, formatDuration, formatTokens, formatSandbox, stopReasonLabel,
} from '@/lib/trajectory';
import Markdown from './Markdown';

// ── 状态色 ──
// success 刻意不上色：每个成功的工具都点一个绿点会糊成一片，安静才是默认态。
// 只有「需要你知道」的状态才拿颜色说话。
const STATUS_COLOR: Record<TrajToolNode['status'], string> = {
  running: W.ongoing,
  success: W.secondary,
  error: W.danger,
  cancelled: W.warning,
};

const STATUS_LABEL: Record<TrajToolNode['status'], string> = {
  running: '执行中',
  success: '完成',
  error: '失败',
  cancelled: '已中断',
};

/** 10px 状态点：半透明光晕 + 实心核（参考实现的 StateDot） */
function StateDot({ color, size = 8 }: { color: string; size?: number }) {
  return (
    <span className="ws-round" style={{
      width: size, height: size, borderRadius: '50%', background: color,
      boxShadow: `0 0 0 2.5px ${color}29`, flexShrink: 0, display: 'inline-block',
    }} />
  );
}

/** 2×2 的圆点分隔符（参考实现用它代替 · 字符，观感更稳） */
function Dot() {
  return <span className="ws-round" style={{ width: 2, height: 2, borderRadius: '50%', background: W.dimmed, flexShrink: 0, display: 'inline-block' }} />;
}

/** 工具名 → 图标。**显式白名单**：匹配不上就退回扳手。
 *
 * 刻意不用正则猜语义——工具名是各 agent 绑定时自由取的（见 agent_tool_bindings
 * 表），靠 /bash|shell|exec|run/ 这种模式去猜，猜错的代价是用户看着一个像模像样
 * 的图标，点开却是别的东西。显示「不认识」比显示错误的信息好。
 */
const TOOL_ICONS: Record<string, typeof Wrench> = {
  // 本平台当前注册的工具
  bash: Terminal,
  python: Braces,
  search_papers: Search,
  list_papers: Library,
  fetch_paper_text: FileText,
  summarize_paper: Sparkles,
  // 常见编码类工具名：别的 agent 绑定时也能落到合适图标
  read_file: FileText,
  read: FileText,
  write_file: Pencil,
  edit_file: Pencil,
  str_replace_editor: Pencil,
  grep: Search,
  grep_search: Search,
  glob: FolderSearch,
  list_dir: FolderSearch,
  web_search: Globe,
  web_fetch: Globe,
  fetch_url: Globe,
};

function toolIcon(tool: string) {
  return TOOL_ICONS[tool] ?? Wrench;
}

/** 展开卡里的 JSON 美化：解析失败就原样返回（后端可能已截断成非合法 JSON）。
 *  与 lib/trajectory.ts 的 formatToolArgs 同一套约定——剥掉系统注入的 tenant_id，
 *  它对用户没有意义，留着只会让每一张 IN 卡都多一行噪音。 */
function prettyJson(s: string, max = 4000): string {
  try {
    const obj = JSON.parse(s);
    if (obj && typeof obj === 'object' && !Array.isArray(obj)) delete obj.tenant_id;
    return JSON.stringify(obj, null, 2).slice(0, max);
  } catch {
    return s.slice(0, max);
  }
}

// ── Think 收起行 ──

function ThinkRow({ node, running }: { node: TrajThinkNode; running: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const text = node.text.trimEnd();
  const nl = text.indexOf('\n');
  // 流式时跟读最后一行（像尾巴在长），落定后显示首行摘要
  const summary = running
    ? text.slice(text.lastIndexOf('\n') + 1)
    : text.slice(0, nl === -1 ? text.length : nl);

  return (
    <div className="ws-fade-in">
      <div onClick={() => text && setExpanded(v => !v)}
        className={`ws-hover${running ? ' ws-sweep' : ''}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, minHeight: 24, padding: '0 6px',
          margin: '0 -6px', borderRadius: R.xs, cursor: text ? 'pointer' : 'default',
          position: 'relative', overflow: 'hidden',
        }}>
        <Brain size={14} color={running ? W.ongoing : W.dimmed} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 13, lineHeight: '24px', fontWeight: 500, color: running ? W.secondary : W.tertiary, flexShrink: 0 }}>
          {running ? '思考中' : '思考'}
        </span>
        {summary && <Dot />}
        <span style={{
          fontSize: 13, lineHeight: '20px', color: W.tertiary, flex: 1, minWidth: 0,
          overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{summary}</span>
        {text && (
          <span style={{ color: W.dimmed, flexShrink: 0, display: 'flex' }}>
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        )}
      </div>
      {expanded && (
        <div style={{
          paddingLeft: 22, margin: '2px 0 8px', fontSize: 13, lineHeight: '22px',
          color: W.tertiary, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
          maxHeight: 320, overflowY: 'auto',
        }}>{text}</div>
      )}
    </div>
  );
}

// ── 工具行 ──

function IOSection({ label, body, tone }: { label: string; body: string; tone?: string }) {
  return (
    <div style={{ display: 'flex', gap: 14, padding: '10px 14px', borderTop: `0.5px solid ${W.borderSoft}` }}>
      <span style={{ fontSize: 11, lineHeight: '18px', color: W.dimmed, flexShrink: 0, width: 24, letterSpacing: '0.04em' }}>{label}</span>
      <pre style={{
        flex: 1, minWidth: 0, margin: 0, maxHeight: 150, overflow: 'auto',
        fontFamily: W.mono, fontSize: 11.5, lineHeight: '19px',
        color: tone || W.secondary, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
      }}>{body}</pre>
    </div>
  );
}

function ToolRow({ node }: { node: TrajToolNode }) {
  const [expanded, setExpanded] = useState(false);
  const Icon = toolIcon(node.tool);
  const color = STATUS_COLOR[node.status];
  const failed = node.status === 'error';
  const summary = node.summary || formatToolArgs(node.args, 160) || '';
  const sandboxLine = formatSandbox(node.sandbox);
  const hasDetail = !!(node.args || node.result || node.error || sandboxLine);

  return (
    <div className="ws-fade-in">
      <div onClick={() => hasDetail && setExpanded(v => !v)}
        className={`ws-hover${node.status === 'running' ? ' ws-sweep' : ''}`}
        title={`${node.tool} · ${STATUS_LABEL[node.status]}`}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, minHeight: 24, padding: '0 6px',
          margin: '0 -6px', borderRadius: R.xs, cursor: hasDetail ? 'pointer' : 'default',
          position: 'relative', overflow: 'hidden',
        }}>
        <Icon size={14} color={color} style={{ flexShrink: 0 }} />
        <span style={{ fontSize: 13, lineHeight: '24px', fontWeight: 500, color: W.text, flexShrink: 0 }}>{node.tool}</span>
        {(failed || node.status === 'cancelled') && <StateDot color={color} />}
        {summary && <Dot />}
        <span style={{
          fontSize: 13, lineHeight: '20px', color: failed ? W.danger : W.tertiary,
          flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
        }}>{summary}</span>
        {node.seconds != null && (
          <span style={{ fontSize: 11, color: W.dimmed, flexShrink: 0, fontVariantNumeric: 'tabular-nums' }}>
            {formatDuration(node.seconds * 1000)}
          </span>
        )}
        {hasDetail && (
          <span style={{ color: W.dimmed, flexShrink: 0, display: 'flex' }}>
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        )}
      </div>

      {expanded && hasDetail && (
        <div style={{ margin: '4px 0 8px 22px' }}>
          <div style={{ border: `0.5px solid ${W.borderSoft}`, borderRadius: R.md, background: W.bg, overflow: 'hidden' }}>
            {node.args && <IOSection label="IN" body={prettyJson(node.args)} />}
            {node.error && <IOSection label="ERR" body={node.error} tone={W.danger} />}
            {!node.error && node.result && <IOSection label="OUT" body={prettyJson(node.result)} />}
            {sandboxLine && (
              <div style={{ padding: '7px 14px', borderTop: `0.5px solid ${W.borderSoft}`, fontSize: 11, color: W.dimmed }}>
                {/* formatSandbox 的首段已经写明「沙箱内执行 / 被沙箱拒绝」，
                    这里再加「沙箱：」前缀就成了「沙箱：沙箱内执行」 */}
                {sandboxLine}
              </div>
            )}
            {!node.args && !node.result && !node.error && !sandboxLine && (
              <div style={{ padding: '10px 14px', fontSize: 12, color: W.dimmed }}>这轮调用没有可展示的输入输出</div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ── 尾部统计 ──

interface TailProps {
  usage?: UsageRow[];
  trace?: TraceSummary;
  time?: string;
}

const USAGE_PANEL_W = 300;

function UsageTail({ usage, trace, time }: TailProps) {
  const [open, setOpen] = useState(false);
  // 浮层坐标：这个组件住在 transcript 的 overflow:auto 里，position:absolute 会被
  // 滚动容器裁掉。改成 fixed + 按视口 clamp——祖先链上没有 transform/filter/contain，
  // fixed 能逃出 overflow 裁剪，也就不需要 portal。
  const [pos, setPos] = useState<{ left: number; bottom: number } | null>(null);
  const anchorRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') setOpen(false); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);

  function toggle() {
    if (open) { setOpen(false); return; }
    const r = anchorRef.current?.getBoundingClientRect();
    if (!r) return;
    setPos({
      left: Math.min(Math.max(12, r.left), Math.max(12, window.innerWidth - USAGE_PANEL_W - 12)),
      bottom: window.innerHeight - r.top + 8,
    });
    setOpen(true);
  }

  const prompt = (usage || []).reduce((a, r) => a + (r.prompt_tokens ?? 0), 0);
  const completion = (usage || []).reduce((a, r) => a + (r.completion_tokens ?? 0), 0);
  const total = prompt + completion;
  const ms = trace?.total_ms ?? null;
  const llmCalls = trace?.usage?.llm_calls ?? (usage?.length || 0);
  const steps = trace?.steps?.length ?? 0;
  const cacheHit = trace?.usage?.cache_hit_tokens ?? null;
  const cacheMiss = trace?.usage?.cache_miss_tokens ?? null;

  if (!total && ms == null && !time) return null;

  const rows: [string, string][] = [];
  if (llmCalls) rows.push(['LLM 调用', `×${llmCalls}`]);
  if (steps) rows.push(['步骤', `${steps} 步`]);
  if (prompt) rows.push(['输入 tokens', String(prompt)]);
  if (completion) rows.push(['输出 tokens', String(completion)]);
  if (cacheHit != null || cacheMiss != null) {
    rows.push(['缓存命中', `${formatTokens(cacheHit ?? 0)} / ${formatTokens((cacheHit ?? 0) + (cacheMiss ?? 0))}`]);
  }
  if (ms != null) rows.push(['耗时', formatDuration(ms)]);
  if (trace?.stop_reason) rows.push(['停止原因', stopReasonLabel(trace.stop_reason)]);

  // 背景交给 .ws-pill 持有：行内 background 会压死 :hover 蒙层
  const pill: React.CSSProperties = {
    display: 'inline-flex', alignItems: 'center', gap: 5, height: 28, padding: '0 10px',
    borderRadius: 28, color: W.tertiary, fontSize: 13,
    fontFamily: 'inherit', fontVariantNumeric: 'tabular-nums',
  };

  return (
    <div ref={anchorRef} style={{ display: 'flex', alignItems: 'center', gap: 2, marginTop: 4, marginLeft: -10 }}>
      {(total > 0 || steps > 0 || ms != null) && (
        <button className="ws-pill ws-round" style={pill} onClick={toggle} title="本轮执行用量明细">
          <Database size={13} />
          {total > 0 ? `${formatTokens(total)} tokens` : ms != null ? formatDuration(ms) : `${steps} 步`}
          <ChevronDown size={11} style={{ opacity: 0.6 }} />
        </button>
      )}
      {time && <span style={{ fontSize: 12, color: W.dimmed, marginLeft: 6, fontVariantNumeric: 'tabular-nums' }}>{time}</span>}

      {open && pos && (
        <>
          <div onClick={() => setOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
          <div style={{
            position: 'fixed', left: pos.left, bottom: pos.bottom, zIndex: 50,
            width: USAGE_PANEL_W, maxHeight: '60vh', overflowY: 'auto',
            background: W.overlay, borderRadius: R.md, boxShadow: W.elevProminent, padding: 16,
          }}>
            <div style={{ fontSize: 12, fontWeight: 600, color: W.secondary, marginBottom: 10 }}>本轮用量</div>
            <dl style={{ margin: 0, display: 'grid', gridTemplateColumns: 'auto 1fr', gap: '6px 16px', fontSize: 13 }}>
              {rows.map(([k, v]) => (
                <div key={k} style={{ display: 'contents' }}>
                  <dt style={{ color: W.dimmed }}>{k}</dt>
                  <dd style={{ margin: 0, color: W.text, textAlign: 'right', fontVariantNumeric: 'tabular-nums' }}>{v}</dd>
                </div>
              ))}
            </dl>
          </div>
        </>
      )}
    </div>
  );
}

// ── 回合主体 ──

interface Props {
  nodes?: TrajNode[];
  content: string;
  usage?: UsageRow[];
  trace?: TraceSummary;
  /** 迁移前的历史会话只有整段 thinking 文本，没有结构化节点 */
  legacyThinking?: string;
  running?: boolean;
  time?: string;
}

export default function AgentTurn({ nodes, content, usage, trace, legacyThinking, running = false, time }: Props) {
  const hasNodes = !!nodes && nodes.length > 0;
  // answer 节点的文本已经由调用方收口进 content，这里只投影过程节点，避免正文渲染两遍
  const processNodes = (nodes || []).filter(n => n.kind !== 'answer');

  return (
    <div style={{ display: 'flex', flexDirection: 'column' }}>
      {hasNodes ? (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 2, marginBottom: content ? 14 : 0 }}>
          {processNodes.map(n => n.kind === 'think'
            ? <ThinkRow key={n.id} node={n} running={running && n.status === 'open'} />
            : <ToolRow key={n.id} node={n} />)}
        </div>
      ) : legacyThinking ? (
        <div style={{ marginBottom: content ? 14 : 0 }}>
          <ThinkRow
            node={{ id: 'legacy', kind: 'think', status: 'done', turn: 0, step: 0, text: legacyThinking }}
            running={false}
          />
        </div>
      ) : null}

      {content && <Markdown text={content} />}

      {!running && (hasNodes || usage || trace || time)
        ? <UsageTail usage={usage} trace={trace} time={time} />
        : null}

      {/* 还没有任何节点与正文：给一行最低限度的「在跑」反馈 */}
      {running && !content && !hasNodes && (
        <div className="ws-sweep" style={{
          position: 'relative', overflow: 'hidden', height: 24, display: 'flex', alignItems: 'center',
          gap: 6, fontSize: 13, color: W.tertiary,
        }}>
          <span className="ws-spin ws-round" style={{
            width: 12, height: 12, borderRadius: '50%', flexShrink: 0,
            border: `1.6px solid ${W.ongoing}`, borderTopColor: 'transparent',
          }} />
          正在处理…
        </div>
      )}
    </div>
  );
}
