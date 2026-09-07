'use client';

// TraceSummaryRow — Agent 回复消息的执行摘要行（TraceProjection 产物展示）
//
// 数据：AgentReply.trace / messages.metadata.trace（与后端 trace dict 同构）——
//   {steps: [{type: llm|tool, latency_ms, content|tool, usage?}], usage: 轮级聚合,
//    total_ms, stop_reason}
//
// 形态：折叠 = 单行摘要（N 步 · 耗时 · LLM 次数 · 缓存命中 · 停止原因）；
//       展开 = 每步一行（第 N 步 LLM/Tool + 内容预览 + 耗时）。
// 不做任何猜测：只读后端给的全量字段；tool output 可能巨大 → 一律截断显示。
// 样式走 theme.ts design tokens（与 TrajectoryTimeline 同体系）。

import { useState } from 'react';
import { ChevronDown, ChevronRight, Zap } from 'lucide-react';
import { T, S } from '@/app/theme';
import {
  TraceSummary, formatDuration, formatTokens, stopReasonLabel, traceSummaryLine,
} from '@/lib/trajectory';

const STEP_LABEL: Record<string, string> = { llm: 'LLM', tool: '工具' };

export default function TraceSummaryRow({ trace }: { trace: TraceSummary }) {
  const [expanded, setExpanded] = useState(false);
  const line = traceSummaryLine(trace);
  const steps = trace.steps || [];
  if (!line && steps.length === 0) return null;

  return (
    <div style={{
      marginTop: 6, display: 'flex', alignItems: 'center', gap: S.xs,
      fontSize: 11, color: T.tertiary, flexWrap: 'wrap', cursor: 'pointer', userSelect: 'none',
    }} onClick={(e) => { e.stopPropagation(); setExpanded(v => !v); }}
      title={expanded ? '收起执行明细' : '展开执行明细'}>
      <Zap size={11} style={{ flexShrink: 0, color: T.warning }} />
      <span>{line || '无摘要'}</span>
      {steps.length > 0 && (
        <span style={{ display: 'inline-flex', alignItems: 'center' }}>
          {expanded ? <ChevronDown size={12} /> : <ChevronRight size={12} />}
        </span>
      )}
      {expanded && steps.length > 0 && (
        <div style={{ width: '100%', marginTop: 6, paddingLeft: 2, cursor: 'default' }}
          onClick={(e) => e.stopPropagation()}>
          {steps.map((s, i) => (
            <div key={i} style={{
              display: 'flex', gap: S.sm, alignItems: 'baseline',
              padding: '3px 6px', borderRadius: 4, background: T.bg, marginBottom: 2,
              fontFamily: 'monospace', fontSize: 11, lineHeight: 1.5, color: T.secondary,
            }}>
              <span style={{ flexShrink: 0, color: T.tertiary }}>
                第 {(s.step ?? i) + 1} 步 · {STEP_LABEL[s.type ?? ''] ?? s.type}
              </span>
              <span style={{ flexShrink: 0, color: T.text }}>{formatDuration(s.latency_ms)}</span>
              <span style={{ minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {s.type === 'tool'
                  ? `→ ${s.tool ?? ''}` + (s.args ? ` ${JSON.stringify(s.args).slice(0, 80)}` : '')
                  : (s.content || '').slice(0, 80) || '（纯工具调用步）'}
              </span>
              {s.type === 'llm' && s.usage && (
                <span style={{ flexShrink: 0, color: T.tertiary }}>
                  输入{formatTokens(s.usage.prompt_tokens)}/出{formatTokens(s.usage.completion_tokens)}
                </span>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
