'use client';

/**
 * WorkspaceChat —— 工作区右栏的对话面板
 *
 * 和 Agent 详情页那个聊天是同一套数据流（SSE → Session typed events →
 * TrajectoryProjection），但目标不同：这里**续的是当前选中的那个 Session**，
 * 所以模型跑工具落下的文件会直接出现在左边这棵树上。
 *
 * 复用 `useTrajectoryBatch` + `TrajectoryTimeline` + `lib/trajectory` —— 高频
 * delta 的 rAF 批处理、think/tool 行的状态机都在那里，本组件不重造。
 *
 * 与 Agent 详情页的两处刻意差异：
 *   - 不走 localStorage 存 session/conversation —— 这里的会话由左侧列表决定，
 *     存了反而会和「我到底在看哪个工作区」打架
 *   - 每轮结束后回调 onTurnDone，让外层刷新文件树（模型刚写的文件要立刻可见）
 */

import { useCallback, useEffect, useRef, useState } from 'react';
import { AlertTriangle, Loader2, Send, ShieldAlert } from 'lucide-react';
import { T, S } from '@/app/theme';
import {
  answerTextOf, groupSnapshotByTurn, TrajNode, TrajectorySnapshot, UsageRow,
} from '@/lib/trajectory';
import TrajectoryTimeline from '@/app/agents/_components/TrajectoryTimeline';
import { useTrajectoryBatch } from '@/app/agents/_components/useTrajectoryBatch';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;
const HEADERS = { 'X-Tenant-ID': String(TENANT_ID) };

interface PendingApproval {
  approvalId: string;
  tool: string;
  reason: string;
  metadata: Record<string, unknown>;
}

interface Turn {
  role: 'user' | 'agent';
  content: string;
  time: string;
  /** 该轮的 think/tool 轨迹（answer node 不存：气泡已按 content 渲染，防重复） */
  nodes?: TrajNode[];
  usage?: UsageRow[];
}

export default function WorkspaceChat({
  agentKey, sessionId, conversationId, onTurnDone,
}: {
  agentKey: string | null;
  sessionId: string | null;
  conversationId: number | null;
  /** 一轮结束（无论成败）后触发：外层据此刷新文件树 */
  onTurnDone: () => void;
}) {
  const [turns, setTurns] = useState<Turn[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [liveUsage, setLiveUsage] = useState<UsageRow[]>([]);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);

  const { nodes, nodesRef, enqueue, forceFlush, reset } = useTrajectoryBatch();
  const usageRef = useRef<UsageRow[]>([]);
  const scrollRef = useRef<HTMLDivElement>(null);

  // 切会话：清掉上一场的瞬时状态（气泡由下面的历史加载重建，轨迹缓冲由 reset 清）
  useEffect(() => {
    setTurns([]);
    setApprovals([]);
    setError(null);
    setLiveUsage([]);
    usageRef.current = [];
    reset();
  }, [sessionId, reset]);

  // ── 历史回放（刷新 / 切会话后把这场对话恢复出来）──
  //
  // 与 agents/[id] 同一套做法，数据来源有两个且都要：
  //   - messages（对话消息）→ 用户 / Agent 气泡
  //   - trajectory（Session Event Log 的投影）→ 每个 agent 轮次的 think/tool 轨迹
  // 少了前者只剩轨迹（没气泡），少了后者只剩气泡（工具调用全丢）。
  useEffect(() => {
    if (!sessionId) { setHistoryLoading(false); return; }
    let cancelled = false;
    (async () => {
      setHistoryLoading(true);
      try {
        const [snap, msgData] = await Promise.all([
          fetch(`${API}/api/agents/sessions/${encodeURIComponent(sessionId)}/trajectory`)
            .then(r => (r.ok ? r.json() as Promise<TrajectorySnapshot> : null))
            .catch(() => null),
          conversationId
            ? fetch(`${API}/api/chat/conversations/${conversationId}/messages`, { headers: HEADERS })
                .then(r => (r.ok ? r.json() : null))
                .catch(() => null)
            : Promise.resolve(null),
        ]);
        if (cancelled) return;

        const msgs: Turn[] = [];
        for (const m of msgData?.items ?? []) {
          const time = m.created_at
            ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' })
            : '';
          if (m.role === 'agent') msgs.push({ role: 'agent', content: m.content, time });
          else if (m.role === 'customer') msgs.push({ role: 'user', content: m.content, time });
        }

        // 轨迹对齐：节点按 turn 分组，从末尾往回贴到 agent 气泡上。
        // 超过轨迹覆盖范围的老轮次保持无轨迹（不猜、不乱配）。
        if (snap?.nodes?.length) {
          const groups = groupSnapshotByTurn(snap);
          let i = msgs.length - 1;
          for (let t = groups.length - 1; t >= 0 && i >= 0; t--) {
            while (i >= 0 && msgs[i].role !== 'agent') i--;
            if (i < 0) break;
            msgs[i] = {
              ...msgs[i],
              nodes: groups[t],
              usage: t === groups.length - 1 ? snap.usage : undefined,
            };
            i--;
          }
        }
        setTurns(msgs);
      } catch {
        // 网络失败：保持现状（空白聊天也不该挡住发新消息）
      } finally {
        if (!cancelled) setHistoryLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId, conversationId]);

  useEffect(() => {
    const el = scrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [turns, nodes]);

  const now = () => new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });

  const send = useCallback(async () => {
    const q = input.trim();
    if (!q || sending || !agentKey || !sessionId) return;
    setInput('');
    setError(null);
    setTurns(prev => [...prev, { role: 'user', content: q, time: now() }]);
    setSending(true);
    setLiveUsage([]);
    usageRef.current = [];
    reset();
    setApprovals([]);

    let finalAnswer = '';
    let legacyAns = '';
    try {
      const body: Record<string, unknown> = { question: q };
      // 两个 id 都回传：conversation_id 让后端续用同一个 conversation（否则每轮
      // 新建一个），session_id 让工作区落在同一目录
      if (conversationId) body.conversation_id = conversationId;
      body.session_id = sessionId;

      const r = await fetch(`${API}/api/agents/${encodeURIComponent(agentKey)}/chat/stream`, {
        method: 'POST',
        headers: { ...HEADERS, 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
      });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);

      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const parts = buf.split('\n\n');
        buf = parts.pop() ?? '';
        for (const part of parts) {
          const line = part.split('\n').find(l => l.startsWith('data:'));
          if (!line) continue;
          let ev: any;
          try { ev = JSON.parse(line.slice(5).trim()); } catch { continue; }
          switch (ev.type) {
            case 'traj/open':
            case 'traj/delta':
            case 'traj/update':
            case 'traj/close':
              enqueue(ev);
              break;
            case 'usage': {
              usageRef.current = [...usageRef.current, ev as UsageRow];
              setLiveUsage(usageRef.current);
              break;
            }
            case 'approval.request': {
              const d = ev.data || {};
              if (!d.approval_id) break;
              setApprovals(prev => [
                ...prev.filter(p => p.approvalId !== d.approval_id),
                { approvalId: d.approval_id, tool: ev.tool || '', reason: d.reason || '', metadata: d.metadata || {} },
              ]);
              break;
            }
            case 'sandbox.escalation':
              // 裁决落地（批准/拒绝/超时）→ 精确配对地收起卡片
              if (ev.data?.approval_id) {
                const id = ev.data.approval_id;
                setApprovals(prev => prev.filter(p => p.approvalId !== id));
              }
              break;
            case 'text':
            case 'answer':
              legacyAns += ev.delta ?? '';
              break;
            case 'done':
              if (ev.answer) finalAnswer = ev.answer;
              break;
          }
        }
      }
    } catch (e) {
      setError(e instanceof Error ? `请求失败：${e.message}` : '请求失败');
    } finally {
      forceFlush();  // 尾部滞留事件立即应用，收口一致
      setSending(false);
      const applied = nodesRef.current;
      setTurns(prev => [...prev, {
        role: 'agent',
        content: finalAnswer || answerTextOf(applied) || legacyAns || '（无回复）',
        time: now(),
        nodes: applied.length ? applied : undefined,
        usage: usageRef.current.length ? usageRef.current : undefined,
      }]);
      onTurnDone();
    }
  }, [input, sending, agentKey, sessionId, conversationId, enqueue, forceFlush, reset, nodesRef, onTurnDone]);

  const resolveApproval = async (approvalId: string, decision: 'allow-once' | 'reject') => {
    setApprovals(prev => prev.filter(p => p.approvalId !== approvalId));
    try {
      await fetch(`${API}/api/agents/approvals/${approvalId}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
    } catch { /* 已超时 / 断连：卡片已收起，不再打扰 */ }
  };

  if (!agentKey || !sessionId) {
    return (
      <div style={centerStyle}>
        <div style={{ fontSize: 13, color: T.secondary }}>
          {sessionId ? '这个会话不属于任何 Agent，无法对话' : '选一个会话后就能和 Agent 对话'}
        </div>
        <div style={{ fontSize: 11, color: T.tertiary, marginTop: S.xs, lineHeight: 1.6 }}>
          在左栏「会话」里选一个由 Agent 创建的会话
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minWidth: 0 }}>
      {/* 消息区 */}
      <div ref={scrollRef} style={{ flex: 1, overflow: 'auto', padding: S.base, background: T.bg }}>
        {historyLoading && turns.length === 0 && (
          <div style={{ ...centerStyle, paddingTop: S.xxxl, color: T.tertiary, fontSize: 12 }}>
            正在恢复对话记录…
          </div>
        )}

        {!historyLoading && turns.length === 0 && !sending && (
          <div style={{ ...centerStyle, paddingTop: S.xxxl }}>
            <div style={{ fontSize: 13, color: T.secondary }}>和 {agentKey} 对话</div>
            <div style={{ fontSize: 11, color: T.tertiary, marginTop: S.xs, lineHeight: 1.6 }}>
              模型跑出来的文件会出现在右侧的文件树里
            </div>
          </div>
        )}

        {turns.map((t, i) => (
          <div key={i} style={{ marginBottom: S.base }}>
            {t.nodes && t.nodes.length > 0 && (
              <div style={{ marginBottom: S.sm }}>
                <TrajectoryTimeline nodes={t.nodes} usage={t.usage} running={false} />
              </div>
            )}
            <div style={{ display: 'flex', flexDirection: 'column', alignItems: t.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <div style={{ fontSize: 11, color: T.tertiary, marginBottom: 3 }}>
                {t.role === 'user' ? '你' : agentKey} · {t.time}
              </div>
              <div style={{
                maxWidth: '88%', padding: `${S.sm}px ${S.md}px`, borderRadius: 8, fontSize: 13, lineHeight: 1.65,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                background: t.role === 'user' ? T.accent : T.surface,
                color: t.role === 'user' ? '#fff' : T.text,
                border: t.role === 'user' ? 'none' : `1px solid ${T.border}`,
              }}>{t.content}</div>
            </div>
          </div>
        ))}

        {/* 本轮实时轨迹 */}
        {sending && (
          <div style={{ marginBottom: S.sm }}>
            <TrajectoryTimeline nodes={nodes} usage={liveUsage} running />
            {nodes.length === 0 && (
              <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, fontSize: 12, color: T.tertiary }}>
                <Loader2 size={13} style={{ animation: 'spin .8s linear infinite' }} /> 思考中…
              </div>
            )}
          </div>
        )}

        {/* 待裁决的升权申请：后端此刻已挂起，等这里的裁决（或超时 → 不放行） */}
        {approvals.map(a => (
          <div key={a.approvalId} style={{
            border: `1px solid ${T.warning}`, borderRadius: 8, background: T.surface,
            padding: S.md, marginBottom: S.sm,
          }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, fontSize: 12, fontWeight: 600, color: T.warning, marginBottom: S.xs }}>
              <ShieldAlert size={14} /> 需要你确认
            </div>
            <div style={{ fontSize: 12, color: T.text, lineHeight: 1.6 }}>
              {a.reason || `${a.tool} 请求提升权限`}
            </div>
            <div style={{ display: 'flex', gap: S.sm, marginTop: S.md }}>
              <button onClick={() => resolveApproval(a.approvalId, 'allow-once')} style={primaryBtn}>允许一次</button>
              <button onClick={() => resolveApproval(a.approvalId, 'reject')} style={ghostBtn}>拒绝</button>
            </div>
          </div>
        ))}

        {error && (
          <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, fontSize: 12, color: T.danger, padding: S.sm }}>
            <AlertTriangle size={14} /> {error}
          </div>
        )}
      </div>

      {/* 输入区 */}
      <div style={{ borderTop: `1px solid ${T.border}`, padding: S.md, background: T.surface, flexShrink: 0 }}>
        <div style={{ display: 'flex', gap: S.sm, alignItems: 'flex-end' }}>
          <textarea
            value={input}
            onChange={e => setInput(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
            }}
            rows={2}
            placeholder={`对 ${agentKey} 说点什么…（Enter 发送，Shift+Enter 换行）`}
            style={{
              flex: 1, minWidth: 0, resize: 'none', padding: '8px 10px', borderRadius: 6,
              border: `1px solid ${T.border}`, fontSize: 13, color: T.text, outline: 'none',
              fontFamily: 'inherit', lineHeight: 1.55, background: T.surface,
            }}
          />
          <button
            onClick={send}
            disabled={sending || !input.trim()}
            style={{
              ...primaryBtn, display: 'flex', alignItems: 'center', gap: 6,
              opacity: sending || !input.trim() ? .5 : 1,
              cursor: sending || !input.trim() ? 'not-allowed' : 'pointer',
            }}
          >
            {sending ? <Loader2 size={13} style={{ animation: 'spin .8s linear infinite' }} /> : <Send size={13} />}
            发送
          </button>
        </div>
      </div>
    </div>
  );
}

const centerStyle: React.CSSProperties = {
  display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center',
  textAlign: 'center', padding: S.xl,
};

const primaryBtn: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
  background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit', fontWeight: 500,
};

const ghostBtn: React.CSSProperties = {
  padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
  background: T.surface, color: T.text, border: `1px solid ${T.border}`, fontFamily: 'inherit',
};
