'use client';

// WorkspaceChat —— 中栏对话（对齐 DSH：用户气泡右侧 + Agent 气泡左侧 + 工具时间线摊开在气泡内）
//
// 逻辑自 agents/[id] 页移植：SSE 轨迹投影（useTrajectoryBatch rAF 批处理）、
// 审批卡片（approval.request → POST resolve）、双源历史恢复
// （messages + trajectory 按 turn 对齐）。会话的 conversation_id / session_id
// 直接来自左侧选中项（workspace/sessions 端点），不再需要 localStorage。
//
// 每轮 done → onTurnDone()：页面据此刷新侧栏会话列表（label/时间变了）与
// 右栏文件树（模型这一轮可能写了文件）。

import { useEffect, useRef, useState } from 'react';
import { Brain, ChevronDown, ChevronRight } from 'lucide-react';
import { W, WS } from '../theme';
import TrajectoryTimeline from '@/app/agents/_components/TrajectoryTimeline';
import { useTrajectoryBatch } from '@/app/agents/_components/useTrajectoryBatch';
import {
  type TrajNode, type UsageRow, type TraceSummary, type TrajectorySnapshot,
  answerTextOf, groupSnapshotByTurn,
} from '@/lib/trajectory';
import Composer from './Composer';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;
const H = { 'X-Tenant-ID': String(TENANT_ID) };

interface ChatMsg {
  role: 'user' | 'agent';
  content: string;
  time: string;
  thinking?: string;
  nodes?: TrajNode[];
  usage?: UsageRow[];
  trace?: TraceSummary;
}

interface PendingApproval {
  approvalId: string;
  tool: string;
  reason: string;
  metadata: Record<string, any>;
}

/** 中栏的活动会话：侧栏选中项，或「新建会话」草稿（conversation_id/session_id 为 null，首问后补上）。 */
export interface ActiveSession {
  agent_key: string;
  conversation_id: number | null;
  session_id: string | null;
  label: string;
}

interface Props {
  session: ActiveSession | null;
  onTurnDone: (ids?: { conversation_id: number | null; session_id: string | null }) => void;   // 每轮完成：刷新侧栏 + 文件树（首问带回新会话 id）
  uploading: boolean;
  uploadError: string | null;
  onUpload: (files: File[]) => void;
  sandboxMode: string;
  onModeChange: (mode: string) => void;
  rightOpen: boolean;
  onToggleRight: () => void;
}

/** DSH 式思考面板：折叠=首行/末行跟读，点击展开全文（自 agents 页移植） */
function ThinkingPanel({ thinking, running }: { thinking: string; running: boolean }) {
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  const trimmed = thinking.trimEnd();
  const summary = running
    ? trimmed.slice(trimmed.lastIndexOf('\n') + 1)
    : trimmed.slice(0, trimmed.indexOf('\n') === -1 ? trimmed.length : trimmed.indexOf('\n'));
  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollLeft = running
        ? bodyRef.current.scrollWidth - bodyRef.current.clientWidth
        : 0;
    }
  }, [summary, running]);

  return (
    <>
      <style>{`@keyframes wsBlink{50%{opacity:0}} .ws-cursor{display:inline-block;width:2px;height:1em;background:${W.accent};margin-left:2px;vertical-align:-2px;animation:wsBlink 1s steps(1) infinite} @keyframes wsSpin{to{transform:rotate(360deg)}} .ws-spin{animation:wsSpin 0.8s linear infinite}`}</style>
      <div style={{ padding: `${WS.md}px ${WS.base}px`, borderRadius: 8, background: W.surface, border: `1px solid ${W.border}`, marginBottom: WS.sm, cursor: thinking ? 'pointer' : 'default' }}
        onClick={() => thinking && setExpanded(v => !v)}>
        <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, fontSize: 12, fontWeight: 600, color: W.secondary }}>
          {running ? (
            <span className="ws-spin" style={{ width: 14, height: 14, borderRadius: '50%', border: `2px solid ${W.accent}`, borderTopColor: 'transparent', display: 'inline-block' }} />
          ) : (
            <Brain size={14} />
          )}
          <span>深度思考</span>
          {running && <span style={{ fontSize: 11, color: W.tertiary }}>思考中...</span>}
          {thinking && (
            <span style={{ marginLeft: 'auto', color: W.tertiary }}>
              {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            </span>
          )}
        </div>
        {thinking && !expanded && (
          <div style={{ position: 'relative', marginTop: WS.sm, overflow: 'hidden', minWidth: 0 }}>
            <div ref={bodyRef} style={{ fontSize: 12, color: W.secondary, lineHeight: 1.6, whiteSpace: 'nowrap', overflow: 'hidden' }}>
              {summary}
              {running && <span className="ws-cursor" />}
            </div>
            {running && <div style={{ position: 'absolute', right: 0, top: 0, bottom: 0, width: 40, background: `linear-gradient(to left, ${W.surface}, transparent)` }} />}
          </div>
        )}
        {thinking && expanded && (
          <div style={{ fontSize: 12, color: W.secondary, lineHeight: 1.6, marginTop: WS.sm, maxHeight: 220, overflow: 'auto', whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>
            {thinking}
          </div>
        )}
      </div>
    </>
  );
}

export default function WorkspaceChat({ session, onTurnDone, uploading, uploadError, onUpload, sandboxMode, onModeChange, rightOpen, onToggleRight }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const { nodes: trajNodes, nodesRef, enqueue, forceFlush, reset } = useTrajectoryBatch();
  const [liveUsage, setLiveUsage] = useState<UsageRow[]>([]);
  const usageRef = useRef<UsageRow[]>([]);
  const bottomRef = useRef<HTMLDivElement>(null);

  const agentName = session?.agent_key || '智能体';

  // ── 会话切换：双源历史恢复（messages + trajectory 按 turn 对齐） ──
  useEffect(() => {
    reset();
    setApprovals([]);
    setMessages([]);
    setLiveUsage([]);
    usageRef.current = [];
    if (!session) { setLoadingHistory(false); return; }
    const { conversation_id, session_id } = session;
    let cancelled = false;
    (async () => {
      setLoadingHistory(true);
      try {
        const msgsR = await fetch(`${API}/api/chat/conversations/${conversation_id}/messages`, { headers: H });
        const data = await msgsR.json();
        let snap: TrajectorySnapshot | null = null;
        if (session_id) {
          const trR = await fetch(`${API}/api/agents/sessions/${session_id}/trajectory`, { headers: H });
          if (trR.ok) snap = await trR.json();
        }
        const items = data.items || [];
        const msgs: ChatMsg[] = [];
        for (const m of items) {
          const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : '';
          const meta = m.metadata || {};
          if (m.role === 'agent') msgs.push({ role: 'agent', content: m.content, time, thinking: meta.thinking || undefined, trace: meta.trace || undefined });
          else if (m.role === 'customer') msgs.push({ role: 'user', content: m.content, time });
        }
        if (snap && snap.nodes && snap.nodes.length > 0) {
          const groups = groupSnapshotByTurn(snap);
          let i = msgs.length - 1;
          for (let t = groups.length - 1; t >= 0 && i >= 0; t--) {
            while (i >= 0 && msgs[i].role !== 'agent') i--;
            if (i < 0) break;
            msgs[i] = { ...msgs[i], nodes: groups[t], usage: t === groups.length - 1 ? snap.usage : undefined };
            i--;
          }
        }
        if (!cancelled) setMessages(msgs);
      } catch { /* 网络失败：保持空态 */ }
      finally {
        if (!cancelled) setLoadingHistory(false);
      }
    })();
    return () => { cancelled = true; };
  }, [session]);   // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);
  useEffect(() => {
    if (sending) bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [trajNodes, sending]);

  const liveAnsText = answerTextOf(trajNodes);

  async function handleSend(q: string) {
    if (!session || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    setMessages(prev => [...prev, { role: 'user', content: q, time: now }]);
    setSending(true);
    setLiveUsage([]); usageRef.current = [];
    reset();
    setApprovals([]);
    let finalAnswer = '';
    let doneTrace: TraceSummary | undefined;
    let legacyThink = '';
    let legacyAns = '';
    let doneConvId: number | null = null;
    let doneSid: string | null = null;
    try {
      // 草稿会话（新建后首问）不带两个 id：后端新建 conversation + Session，
      // done 事件回传，页面据此把草稿升级为正式会话
      const body: any = { question: q };
      if (session.conversation_id != null) body.conversation_id = session.conversation_id;
      if (session.session_id != null) body.session_id = session.session_id;
      const r = await fetch(`${API}/api/agents/${session.agent_key}/chat/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...H }, body: JSON.stringify(body),
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
              enqueue(ev as any);
              break;
            case 'usage': {
              const row = { ...ev } as UsageRow;
              usageRef.current = [...usageRef.current, row];
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
              if (ev.data?.approval_id) {
                const doneId = ev.data.approval_id;
                setApprovals(prev => prev.filter(p => p.approvalId !== doneId));
              }
              break;
            case 'thinking':
              legacyThink += ev.delta;
              break;
            case 'text':
            case 'answer':
              legacyAns += ev.delta;
              break;
            case 'done':
              if (ev.answer) finalAnswer = ev.answer;
              doneTrace = ev.trace;
              if (ev.conversation_id) doneConvId = Number(ev.conversation_id);
              if (ev.session_id) doneSid = ev.session_id;
              break;
          }
        }
      }
    } catch {
      finalAnswer = '请求失败，请稍后重试。';
    } finally {
      forceFlush();
      setSending(false);
      const nodes = nodesRef.current;
      const ansText = answerTextOf(nodes);
      const content = finalAnswer || ansText || legacyAns || '抱歉，暂时无法处理。';
      setMessages(prev => [...prev, {
        role: 'agent', content,
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        nodes: nodes.length ? nodes : undefined,
        usage: usageRef.current.length ? usageRef.current : undefined,
        thinking: legacyThink || undefined,
        trace: doneTrace,
      }]);
      onTurnDone({ conversation_id: doneConvId, session_id: doneSid });   // 模型这一轮可能落了文件：侧栏 + 文件树一起刷新；草稿会话带回正式 id
    }
  }

  async function resolveApproval(approvalId: string, decision: 'allow-once' | 'reject') {
    setApprovals(prev => prev.filter(p => p.approvalId !== approvalId));
    try {
      await fetch(`${API}/api/agents/approvals/${approvalId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
    } catch { /* 已超时/断连：卡片已收起 */ }
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontFamily: W.font }}>
      {/* 顶栏：会话标题 + 进行中指示 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: `${WS.md}px ${WS.xl}px`, borderBottom: `1px solid ${W.border}`, flexShrink: 0 }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: sending ? W.success : W.accent }} />
        <span style={{ fontSize: 14, fontWeight: 500, color: W.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {session ? (session.label || session.session_id?.slice(0, 8)) : '工作区'}
        </span>
        {sending && <span style={{ fontSize: 12, color: W.tertiary }}>运行中...</span>}
        <div style={{ flex: 1 }} />
        {/* 右上角用户头像：点击收起 / 展开右侧文件面板 */}
        <button onClick={onToggleRight} title={rightOpen ? '收起文件面板' : '展开文件面板'}
          style={{
            width: 30, height: 30, borderRadius: '50%', padding: 0, border: `1px solid ${W.border}`,
            background: W.surface, cursor: 'pointer', overflow: 'hidden', flexShrink: 0,
          }}>
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/workspace-avatar.png" alt="用户" style={{ width: '100%', height: '100%', objectFit: 'cover' }} />
        </button>
      </div>

      {/* 消息区 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `${WS.xl}px`, scrollBehavior: 'auto' }}>
        {loadingHistory && <div style={{ textAlign: 'center', color: W.secondary, marginTop: WS.huge, fontSize: 14 }}>加载历史消息...</div>}

        {/* 空态：DSH 式欢迎语 */}
        {!loadingHistory && messages.length === 0 && (
          <div style={{ maxWidth: 640, margin: '16vh auto 0' }}>
            <div style={{ fontSize: 22, fontWeight: 600, color: W.text, marginBottom: WS.md }}>
              你好，我是工作区智能体！
            </div>
            <div style={{ fontSize: 14, color: W.secondary, lineHeight: 1.7 }}>
              我是运行在你平台的编码智能体。有任何问题，可以直接开始对话，或先浏览、上传文件给我。
              <br />我执行工具时落下的文件会出现在右侧文件树里，可预览、可下载。
            </div>
          </div>
        )}

        {messages.map((msg, i) => {
          const isUser = msg.role === 'user';
          return (
            <div key={i} style={{ marginBottom: WS.lg, display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
              <div style={{ fontSize: 12, color: W.secondary, marginBottom: 6 }}>
                {isUser ? '你' : agentName} · {msg.time}
              </div>
              <div style={{ maxWidth: 'min(78%, 860px)', minWidth: 0, width: '100%' }}>
                {!isUser && msg.nodes && msg.nodes.length > 0 && (
                  <TrajectoryTimeline nodes={msg.nodes} usage={msg.usage} running={false} />
                )}
                {!isUser && (!msg.nodes || msg.nodes.length === 0) && msg.thinking && (
                  <ThinkingPanel thinking={msg.thinking} running={false} />
                )}
                <div style={{
                  padding: `${WS.md}px ${WS.base}px`, borderRadius: 10, fontSize: 14, lineHeight: 1.55,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  background: isUser ? W.accent : W.surface, color: isUser ? '#fff' : W.text,
                  border: isUser ? 'none' : `1px solid ${W.border}`,
                }}>{msg.content}</div>
              </div>
            </div>
          );
        })}

        {/* 实时轮次：轨迹 + 打字机气泡 */}
        {sending && (
          <div style={{ marginBottom: WS.lg, display: 'flex', flexDirection: 'column', alignItems: 'flex-start' }}>
            <div style={{ fontSize: 12, color: W.secondary, marginBottom: 6 }}>{agentName} · 回复中</div>
            <div style={{ maxWidth: 'min(78%, 860px)', minWidth: 0, width: '100%' }}>
              <TrajectoryTimeline nodes={trajNodes} usage={liveUsage} running={sending} />
              {liveAnsText && (
                <div style={{
                  padding: `${WS.md}px ${WS.base}px`, borderRadius: 10, fontSize: 14, lineHeight: 1.55,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  background: W.surface, color: W.text, border: `1px solid ${W.border}`,
                }}>
                  {liveAnsText}
                  <span className="ws-cursor" />
                </div>
              )}
            </div>
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      {/* 审批卡片：后端工具调用挂起中，不裁决等到超时 */}
      {approvals.length > 0 && (
        <div style={{ padding: `0 ${WS.xl}px ${WS.sm}px`, display: 'flex', flexDirection: 'column', gap: WS.sm }}>
          {approvals.map(a => (
            <div key={a.approvalId} style={{ border: `1px solid ${W.warning}`, borderRadius: 10, background: W.surface, padding: `${WS.md}px ${WS.base}px` }}>
              <div style={{ fontSize: 13, fontWeight: 600, color: W.text, marginBottom: 6 }}>
                模型申请提权：{a.metadata.from ?? '?'} → {a.metadata.to ?? '?'}
              </div>
              <div style={{ fontSize: 13, color: W.secondary, lineHeight: 1.55, whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: WS.sm }}>
                {a.metadata.justification || a.reason || '（未给理由）'}
              </div>
              <div style={{ fontSize: 11, color: W.tertiary, marginBottom: WS.sm }}>
                只放行这一次 {a.tool} 调用；不裁决将等待至超时（超时按不可用处理）
              </div>
              <div style={{ display: 'flex', gap: WS.sm }}>
                <button onClick={() => resolveApproval(a.approvalId, 'allow-once')}
                  style={{ padding: '6px 16px', borderRadius: 8, border: 'none', cursor: 'pointer', background: W.accent, color: '#fff', fontSize: 13, fontFamily: 'inherit' }}>
                  允许一次
                </button>
                <button onClick={() => resolveApproval(a.approvalId, 'reject')}
                  style={{ padding: '6px 16px', borderRadius: 8, border: `1px solid ${W.border}`, background: W.surface, color: W.text, fontSize: 13, cursor: 'pointer', fontFamily: 'inherit' }}>
                  拒绝
                </button>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* 底部输入条 */}
      <Composer
        disabled={!session || sending}
        sending={sending}
        uploading={uploading}
        uploadError={uploadError}
        onSend={handleSend}
        onUpload={onUpload}
        sandboxMode={sandboxMode}
        onModeChange={onModeChange}
      />
    </div>
  );
}
