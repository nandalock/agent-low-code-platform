'use client';

// TrajectoryTimeline — Agent Trajectory 纵向时间线（DeepSeek Harness Conversation 思想）
//
// Session typed events（后端投影）→ TrajNode[] → 这里按执行序渲染为独立行：
//   ThinkRow（第 N 步思考） / ToolRow（工具名 + 输入 + 运行态 → 结果） / UsageLine
//
// 不做任何内容猜测：think 只来自后端 traj/delta(field=thinking)，
// tool 只来自 traj/open(tool)，绝不按字符串/工具名/正则推断。
// answer node 不进时间线（由消息区按气泡渲染，防重复 —— 场景 E 的 UI 侧）。
//
// 样式全部走 theme.ts design tokens + lucide-react（沿用页面既有体系）。

import { useEffect, useRef, useState } from 'react';
import { T, S } from '@/app/theme';
import {
  Brain, CheckCircle2, XCircle, Clock, ChevronDown, ChevronRight, Loader2,
} from 'lucide-react';
import {
  TrajNode, UsageRow, formatToolArgs, formatUsage, formatSandbox,
} from '@/lib/trajectory';

// ── ThinkRow：每 step 一个思考行（delta 在 node.text 内累积，天然单行）──
// DSH 折叠形态：折叠 = 流式最新一行字幕跟读 / 完成首行摘要；展开 = 全文
function ThinkRow({ node, running }: { node: Extract<TrajNode, { kind: 'think' }>; running: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const open = running && node.status === 'open';
  const thinking = node.text.length > 0;
  const trimmed = node.text.trimEnd();
  const summary = open
    ? trimmed.slice(trimmed.lastIndexOf('\n') + 1)
    : trimmed.slice(0, trimmed.indexOf('\n') === -1 ? trimmed.length : trimmed.indexOf('\n'));

  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollLeft = open
        ? bodyRef.current.scrollWidth - bodyRef.current.clientWidth
        : 0;
    }
  }, [summary, open]);

  return (
    <div style={{
      padding: `${S.sm}px ${S.base}px`, borderRadius: 8, background: T.surface,
      border: `1px solid ${T.border}`, marginBottom: S.sm, minWidth: 0,
      cursor: thinking ? 'pointer' : 'default',
    }} onClick={() => thinking && setExpanded(v => !v)}>
      <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, fontSize: 12, fontWeight: 600, color: T.secondary }}>
        {open ? (
          <Loader2 size={13} style={{ animation: 'spin 0.8s linear infinite' }} />
        ) : (
          <Brain size={13} />
        )}
        <span>深度思考</span>
        {node.step > 0 && <span style={{ fontSize: 11, color: T.tertiary }}>· 第 {node.step} 步</span>}
        {open && <span style={{ fontSize: 11, color: T.tertiary }}>思考中...</span>}
        {thinking && (
          <span style={{ marginLeft: 'auto', color: T.tertiary }}>
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        )}
      </div>
      {thinking && !expanded && (
        <div style={{ position: 'relative', marginTop: S.xs, overflow: 'hidden', minWidth: 0 }}>
          <div ref={bodyRef} style={{ fontSize: 12, color: T.secondary, lineHeight: 1.6, whiteSpace: 'nowrap', overflow: 'hidden' }}>
            {summary}
            {open && <span className="stream-cursor" />}
          </div>
          {open && (
            <div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 40, background: `linear-gradient(to left, ${T.surface}, transparent)` }} />
          )}
        </div>
      )}
      {thinking && expanded && (
        <div style={{ fontSize: 12, color: T.secondary, lineHeight: 1.6, marginTop: S.xs, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
          {node.text}
        </div>
      )}
    </div>
  );
}

// ── ToolRow：tool_call_id 已由后端绑定为唯一 node → 状态机 running/success/error/cancelled ──
function ToolRow({ node }: { node: Extract<TrajNode, { kind: 'tool' }> }) {
  const [showResult, setShowResult] = useState(false);
  const { status, tool } = node;
  const running = status === 'running';

  const StatusIcon = running ? Loader2 : status === 'success' ? CheckCircle2 : status === 'error' ? XCircle : Clock;
  const statusColor = running ? T.accent : status === 'success' ? T.success : status === 'error' ? T.danger : T.warning;
  const statusLabel = running ? '运行中' : status === 'success' ? '完成' : status === 'error' ? '出错' : '已取消';

  return (
    <div style={{
      padding: `${S.sm}px ${S.base}px`, borderRadius: 8, background: T.surface,
      border: `1px solid ${T.border}`, marginBottom: S.sm, minWidth: 0,
    }}>
      {/* 状态行：图标 + 工具名（动态，不硬编码 pwsh/bash...）+ 第几步 + 状态 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, fontSize: 12 }}>
        <StatusIcon size={13} style={{ color: statusColor, flexShrink: 0, animation: running ? 'spin 0.8s linear infinite' : undefined }} />
        <span style={{ fontWeight: 600, color: T.text, fontFamily: 'monospace' }}>{tool}</span>
        {node.step > 0 && <span style={{ fontSize: 11, color: T.tertiary }}>· 第 {node.step} 步</span>}
        <span style={{ fontSize: 11, color: statusColor }}>{statusLabel}</span>
        {/* 沙箱事实：仅在工具确实经过沙箱时出现（非沙箱工具 node.sandbox 为 null） */}
        {node.sandbox && (
          <span style={{
            fontSize: 11, padding: '0 6px', borderRadius: 4, background: T.bg,
            color: node.sandbox.outcome === 'normal' ? T.tertiary : T.warning,
            border: `1px solid ${T.border}`, whiteSpace: 'nowrap',
          }}>
            {formatSandbox(node.sandbox)}
          </span>
        )}
        <span style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: S.sm }}>
          {running && node.seconds != null && <span style={{ fontSize: 11, color: T.tertiary }}>{node.seconds}s</span>}
          {(node.result || node.summary) && (
            <span style={{ color: T.tertiary, cursor: 'pointer', display: 'flex', alignItems: 'center' }}
              onClick={(e) => { e.stopPropagation(); setShowResult(v => !v); }}>
              {showResult ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
              <span style={{ fontSize: 11, marginLeft: 2 }}>详情</span>
            </span>
          )}
        </span>
      </div>
      {/* 工具输入（args 为后端 JSON 字符串；tenant_id 系统字段已剥） */}
      {node.args && node.args !== '{}' && (
        <div style={{
          marginTop: 4, fontSize: 11, color: T.secondary, fontFamily: 'monospace',
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis', minWidth: 0,
        }} title={formatToolArgs(node.args, 2000)}>
          {formatToolArgs(node.args)}
        </div>
      )}
      {/* 阶段进度（tool/progress） */}
      {running && node.stage && (
        <div style={{ marginTop: 4, fontSize: 11, color: T.warning }}>
          {node.stage}{node.seconds != null ? `（${node.seconds}s）` : ''}
        </div>
      )}
      {/* 结果：成功 → summary（错误 → error 红字）；result 可展开原文 */}
      {!running && node.status === 'error' && node.error && (
        <div style={{ marginTop: 4, fontSize: 11, color: T.danger }}>→ {node.error}</div>
      )}
      {!running && node.status === 'success' && node.summary && (
        <div style={{ marginTop: 4, fontSize: 11, color: T.success }}>→ {node.summary}</div>
      )}
      {showResult && node.result && (
        <div style={{
          marginTop: 6, fontSize: 11, color: T.secondary, fontFamily: 'monospace',
          whiteSpace: 'pre-wrap', wordBreak: 'break-word', maxHeight: 160, overflow: 'auto',
          background: T.bg, borderRadius: 6, padding: S.sm,
        }}>
          {node.result}
        </div>
      )}
    </div>
  );
}

// ── UsageLine：LLM usage 观测（低调单行，不抢轨迹视觉） ──
function UsageLine({ rows }: { rows: UsageRow[] }) {
  if (!rows.length) return null;
  return (
    <div style={{
      display: 'flex', flexDirection: 'column', gap: 2, marginBottom: S.sm,
      fontSize: 11, color: T.tertiary, fontFamily: 'monospace',
    }}>
      {rows.map((u, i) => (
        <span key={i}>{formatUsage(u)}</span>
      ))}
    </div>
  );
}

// ── Timeline 容器：按 node 插入序渲染（think/tool 独立成行；answer 由消息区渲染）──
// keyframes 内嵌自持（原定义在页面 ThinkingPanel 内，ThinkRow 独立渲染时也需生效；
// 同名 keyframes 重复声明无害）
export default function TrajectoryTimeline({
  nodes, usage, running,
}: {
  nodes: TrajNode[]; usage?: UsageRow[]; running: boolean;
}) {
  const rows = nodes.filter(n => n.kind !== 'answer');
  if (!rows.length && !(usage && usage.length)) return null;
  return (
    <>
      <style>{`@keyframes spin{to{transform:rotate(360deg)}} .stream-cursor{display:inline-block;width:2px;height:1em;background:${T.accent};margin-left:2px;vertical-align:-2px;animation:blink 1s steps(1) infinite} @keyframes blink{50%{opacity:0}}`}</style>
      <div style={{ display: 'flex', flexDirection: 'column', marginBottom: S.sm, minWidth: 0, width: '100%' }}>
        {rows.map(n => n.kind === 'think'
          ? <ThinkRow key={n.id} node={n} running={running} />
          : <ToolRow key={n.id} node={n} />)}
        {usage && usage.length > 0 && <UsageLine rows={usage} />}
      </div>
    </>
  );
}
