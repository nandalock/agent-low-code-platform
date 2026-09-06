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
