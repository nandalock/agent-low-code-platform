// Agent Trajectory — Conversation Projection 前端纯数据层
//
// 与后端 TrajectoryProjector 的 UI 事件 schema 一一对应（typed events，无字符串猜测）：
//   Session Event → (后端) TrajectoryProjector → traj/* 事件 → SSE
//   → (本层) applyTrajEvent reducer → TrajNode[]
//
// reducer 是纯函数：只按「全量字段值」patch/拼接 delta，UI 渲染零拼装逻辑。
// 不 import React —— 可独立单测 / 服务端复用。

// ── Node 类型（traj/open 的 node body 与回放快照同构） ──

export interface TrajThinkNode {
  id: string;
  kind: 'think';
  status: 'open' | 'done';
  turn: number;
  step: number;
  text: string;
}

/** 沙箱事实（后端 tool/result 的结构化字段；非沙箱工具为 null） */
export interface SandboxFacts {
  mode: string;         // read-only | workspace-write | danger-full-access
  enforcement: string;  // full | partial
  outcome: string;      // normal | denied | runner_failed
}

export interface TrajToolNode {
  id: string;
  kind: 'tool';
  status: 'running' | 'success' | 'error' | 'cancelled';
  turn: number;
  step: number;
  tool: string;
  args: string; // JSON 字符串（后端已截断），展示用
  call_id: string;
  stage: string | null;
  seconds: number | null;
  summary: string | null;
  error: string | null;
  result: string | null; // JSON 字符串（后端截断 ≤4000）
  sandbox: SandboxFacts | null;
}

export interface TrajAnswerNode {
  id: string;
  kind: 'answer';
  status: 'open' | 'done';
  turn: number;
  step: number;
  text: string;
}

export type TrajNode = TrajThinkNode | TrajToolNode | TrajAnswerNode;

export interface UsageRow {
  step?: number | null;
  prompt_tokens?: number | null;
  completion_tokens?: number | null;
  prompt_cache_hit_tokens?: number | null;
  prompt_cache_miss_tokens?: number | null;
}

// ── SSE wire 事件（data: JSON）──

export type TrajWireEvent =
  | { type: 'step'; step: number; total?: number }
  | { type: 'traj/open'; node: TrajNode }
  | { type: 'traj/delta'; id: string; field: 'thinking' | 'text'; delta: string }
  | { type: 'traj/update'; id: string; patch: Record<string, unknown> }
  | { type: 'traj/close'; id: string }
  | { type: 'usage' } & UsageRow
  | { type: 'done'; answer: string; session_id?: string | null; conversation_id?: number }
  | { type: 'answer-final'; text: string }; // 本地合成：done.answer 收口 answer node（权威文本）

// 回放快照（GET /api/agents/sessions/{sid}/trajectory）
export interface TrajectorySnapshot {
  session_id: string;
  nodes: TrajNode[];
  usage: UsageRow[];
}

// ── 执行摘要（TraceProjection 产物：AgentReply.trace / messages.metadata.trace） ──
// 与后端 trace dict 一一对应（后端为条件性出现 key，这里全部可选）。

export interface TraceStepEntry {
  step?: number;                    // 0-based
  type?: 'llm' | 'tool';
  content?: string | null;          // llm 步正文
  tool?: string;                    // tool 步工具名
  args?: Record<string, unknown> | null;
  output?: unknown;                 // tool 步原始结果（可能很大，展示时截断）
  latency_ms?: number;
  usage?: UsageRow | null;          // llm 步的 4-key token 统计
}

export interface TraceSummary {
  steps?: TraceStepEntry[];
  limits?: { max_steps?: number; max_tool_calls?: number; max_wall_time?: number };
  usage?: {
    llm_calls?: number;
    cache_hit_tokens?: number;
    cache_miss_tokens?: number;
    cache_hit_ratio?: number | null;
  };
  stop_reason?: string | null;
  total_ms?: number;
}

// ── 纯 reducer ──

function upsert(list: TrajNode[], node: TrajNode): TrajNode[] {
  const idx = list.findIndex(n => n.id === node.id);
  if (idx < 0) return [...list, node];
  const next = [...list];
  next[idx] = node;
  return next;
}

function mutate(list: TrajNode[], id: string, fn: (n: TrajNode) => TrajNode): TrajNode[] {
  const idx = list.findIndex(n => n.id === id);
  if (idx < 0) return list; // 未知 id（旧日志/异常流）→ 忽略，不猜测
  const next = [...list];
  next[idx] = fn(next[idx]);
  return next;
}

/** 单事件应用：think/tool/answer 按 node id 增量更新；顺序由插入序保持 */
export function applyTrajEvent(nodes: TrajNode[], ev: TrajWireEvent): TrajNode[] {
  switch (ev.type) {
    case 'traj/open':
      return ev.node ? upsert(nodes, ev.node) : nodes;
    case 'traj/delta':
      return mutate(nodes, ev.id, n => {
        if (n.kind === 'think' && ev.field === 'thinking') return { ...n, text: n.text + ev.delta };
        if (n.kind === 'answer' && ev.field === 'text') return { ...n, text: n.text + ev.delta };
        return n; // field 与 node kind 不匹配：忽略（不猜测）
      });
    case 'traj/update':
      return mutate(nodes, ev.id, n => ({ ...n, ...ev.patch }));
    case 'traj/close':
      // 后端只对 think/answer close（tool 终态走 cancelled/success update）
      return mutate(nodes, ev.id, n => {
        if (n.kind === 'tool') return n;
        return { ...n, status: 'done' as const };
      });
    case 'answer-final': {
      // done.answer 是最终权威文本：替换现有 answer node；不存在则兜底创建（guard 合成回答）
      const ans = nodes.find(n => n.kind === 'answer');
      if (ans) {
        return mutate(nodes, ans.id, n => {
          if (n.kind !== 'answer') return n; // 防御（id 冲突不应发生）
          return { ...n, text: ev.text, status: 'done' as const };
        });
      }
      return [
        ...nodes,
        { id: 'answer', kind: 'answer', status: 'done', turn: 1, step: 1, text: ev.text },
      ];
    }
    default:
      return nodes; // step / usage / done 元事件不进 node 列表
  }
}

/** 批量应用（rAF flush 一次一帧） */
export function applyTrajBatch(nodes: TrajNode[], evs: TrajWireEvent[]): TrajNode[] {
  let s = nodes;
  for (const ev of evs) s = applyTrajEvent(s, ev);
  return s;
}

/** 当前 turn 的实时 answer 文本（无 answer node 时返回空串） */
export function answerTextOf(nodes: TrajNode[]): string {
  const ans = [...nodes].reverse().find(n => n.kind === 'answer');
  return ans?.text ?? '';
}

// ── 展示 helpers ──

/** tool args 展示：剥掉系统注入的 tenant_id，JSON 单行化截断 */
export function formatToolArgs(args: string, maxLen = 240): string {
  if (!args) return '';
  try {
    const obj = JSON.parse(args);
    if (obj && typeof obj === 'object') delete obj.tenant_id; // 系统字段，对用户无意义
    const text = JSON.stringify(obj);
    return text.length > maxLen ? text.slice(0, maxLen) + '…' : text;
  } catch {
    return args.length > maxLen ? args.slice(0, maxLen) + '…' : args;
  }
}

/** usage 单行文案；无 cache 字段时只报 tokens */
export function formatUsage(row: UsageRow): string {
  const pt = row.prompt_tokens ?? 0;
  const ct = row.completion_tokens ?? 0;
  const hit = row.prompt_cache_hit_tokens ?? 0;
  const miss = row.prompt_cache_miss_tokens ?? 0;
  if (hit || miss) {
    const ratio = hit + miss > 0 ? Math.round((hit / (hit + miss)) * 100) : 0;
    return `第${row.step ?? '?'}步 · 输入 ${pt} · 输出 ${ct} · 缓存命中 ${ratio}%（${hit}/${hit + miss}）`;
  }
  return `第${row.step ?? '?'}步 · 输入 ${pt} · 输出 ${ct}`;
}

/** 快照 → 按 turn 分组（历史消息对齐用）；answer 节点带在组内（渲染层忽略） */
export function groupSnapshotByTurn(snap: TrajectorySnapshot): TrajNode[][] {
  const turns: TrajNode[][] = [];
  for (const node of snap.nodes) {
    const turn = node.turn - 1;
    while (turns.length <= turn) turns.push([]);
    turns[turn].push(node);
  }
  return turns;
}

// ── 执行摘要展示 helpers（TraceProjection dict 的纯文本化，无渲染状态） ──

export const STOP_REASON_LABELS: Record<string, string> = {
  completed: '正常完成',
  error: '异常结束',
  max_steps: '轮数超限停止',
  max_tool_calls: '工具调用超限停止',
  max_wall_time: '超时停止',
  repeat_tool: '重复调用停止',
  tool_timeout: '工具超时停止',
};

export function stopReasonLabel(reason?: string | null): string {
  if (!reason) return '';
  return STOP_REASON_LABELS[reason] ?? reason;
}

export const SANDBOX_OUTCOME_LABELS: Record<string, string> = {
  normal: '沙箱内执行',
  denied: '被沙箱拒绝',
  runner_failed: '沙箱故障',
};

/** 沙箱事实单行文案（无事实返回空串）；enforcement=partial 时如实标注 */
export function formatSandbox(f?: SandboxFacts | null): string {
  if (!f) return '';
  const parts = [SANDBOX_OUTCOME_LABELS[f.outcome] ?? f.outcome, f.mode];
  if (f.enforcement === 'partial') parts.push('部分强制');
  return parts.join(' · ');
}

/** 耗时：<1s 显示 ms，否则显示秒 */
export function formatDuration(ms?: number): string {
  if (ms == null) return '';
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`;
}

/** token 数：≥1 万显示 "x.x万"，否则原样 */
export function formatTokens(n?: number | null): string {
  if (n == null) return '0';
  return n >= 10000 ? `${(n / 10000).toFixed(1)}万` : String(n);
}

/** 一行摘要文案：steps 数 · 总耗时 · 输入/输出/命中 · 停止原因（缺哪段就不显示哪段） */
export function traceSummaryLine(t: TraceSummary): string {
  const parts: string[] = [];
  if (t.steps?.length) parts.push(`${t.steps.length} 步`);
  if (t.total_ms != null) parts.push(`耗时 ${formatDuration(t.total_ms)}`);
  if (t.usage?.llm_calls) {
    const { llm_calls: calls, cache_hit_tokens: hit, cache_miss_tokens: miss, cache_hit_ratio: ratio } = t.usage;
    parts.push(`LLM ×${calls}`);
    if (hit != null || miss != null) {
      if (ratio != null) parts.push(`缓存命中 ${Math.round(ratio * 100)}%`);
      else parts.push('无缓存统计');
    }
  }
  if (t.stop_reason) parts.push(stopReasonLabel(t.stop_reason));
  return parts.join(' · ');
}
