'use client';

// WorkspaceChat —— 中栏对话（日志流布局）
//
// 版面取自参考项目 ui-conversation / ui-chat：
//   内容轴 clamp(680px,64%,920px) 居中，输入卡比消息列宽 32px（→ 同一根轴上的
//   两级内缩，见下方 ROOT 与 transcript 的 padding 差）
//   用户消息 = 右对齐 r22 气泡，时间 hover 才现身
//   助手回合 = 无气泡全宽分组，交给 AgentTurn 投影成日志流
//
// 逻辑自 agents/[id] 页移植，本次改造一行未动：SSE 轨迹投影（useTrajectoryBatch
// rAF 批处理）、审批卡片、双源历史恢复（messages + trajectory 按 turn 对齐）、
// 首问交接。会话的 id 直接来自左侧选中项，不再需要 localStorage。
//
// 每轮 done → onTurnDone()：页面据此刷新侧栏会话列表与右栏文件树。

import { useEffect, useRef, useState } from 'react';
import { PanelRight, ShieldAlert } from 'lucide-react';
import { W, R, WS, CHAT_COLUMN } from '../theme';
import { useTrajectoryBatch } from '@/app/agents/_components/useTrajectoryBatch';
import {
  type TrajNode, type UsageRow, type TraceSummary, type TrajectorySnapshot,
  answerTextOf, groupSnapshotByTurn,
} from '@/lib/trajectory';
import AgentTurn from './AgentTurn';
import Composer from './Composer';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;
const H = { 'X-Tenant-ID': String(TENANT_ID) };

const TURN_GAP = 20;   // 回合之间的呼吸；回合内部各区块的间距由 AgentTurn 自管

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
  onTurnDone: () => void;   // 每轮完成：刷新侧栏 + 文件树（模型这一轮可能落了文件）
  uploading: boolean;
  uploadError: string | null;
  onUpload: (files: File[]) => void;
  sandboxMode: string;
  onModeChange: (mode: string) => void;
  rightOpen: boolean;
  onToggleRight: () => void;
  /** 空态发出首问后由页面交接下来的一条待发消息（见下方「首问交接」）。 */
  pendingQuestion?: { text: string; key: string } | null;
  onPendingQuestionConsumed?: () => void;
}

/** 头部小圆点：运行中/空闲 */
function HeaderDot({ active }: { active: boolean }) {
  return (
    <span className="ws-round" style={{
      width: 6, height: 6, borderRadius: '50%', flexShrink: 0,
      background: active ? W.ongoing : W.tertiary,
      boxShadow: active ? `0 0 0 3px ${W.ongoing}29` : 'none',
    }} />
  );
}

export default function WorkspaceChat({ session, onTurnDone, uploading, uploadError, onUpload, sandboxMode, onModeChange, rightOpen, onToggleRight, pendingQuestion, onPendingQuestionConsumed }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [sending, setSending] = useState(false);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);
  const { nodes: trajNodes, nodesRef, enqueue, forceFlush, reset } = useTrajectoryBatch();
  const [liveUsage, setLiveUsage] = useState<UsageRow[]>([]);
  const usageRef = useRef<UsageRow[]>([]);
  // 停止生成：当前在飞的 SSE 请求（abort 它 = 服务端取消本轮）；null = 没有在跑的轮次
  const stopRef = useRef<AbortController | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  // 视口是否贴着底：决定新内容到达时跟不跟随（见下方 handleScroll）
  const atBottomRef = useRef(true);
  // 首问交接：consumedKey 记「哪条已认领」，historySettled 是「历史加载完了」的闸
  const consumedKeyRef = useRef<string | null>(null);
  const [historySettled, setHistorySettled] = useState(false);

  // ── 会话切换：双源历史恢复（messages + trajectory 按 turn 对齐） ──
  useEffect(() => {
    reset();
    setApprovals([]);
    setMessages([]);
    setLiveUsage([]);
    usageRef.current = [];
    atBottomRef.current = true;   // 换会话回到贴底：新会话应该从头看到尾，带着上一个会话的滚动位置没有意义
    setHistorySettled(false);   // 首问要等本轮历史加载落定（见下方首问 effect）
    if (!session) { setLoadingHistory(false); setHistorySettled(true); return; }
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
        if (!cancelled) { setLoadingHistory(false); setHistorySettled(true); }
      }
    })();
    return () => { cancelled = true; };
  }, [session]);   // eslint-disable-line react-hooks/exhaustive-deps

  // ── 首问交接：空态发来的第一条消息，等历史落定后恰好发一次 ──
  //
  // 两道闸缺一不可：
  //  - historySettled —— 历史加载的 setMessages(空数组) 会冲掉 handleSend 刚追加的
  //    用户气泡。新会话的历史一定是空的，所以必须等它落定再发。顺带解决 StrictMode：
  //    挂载时的 effect 双执行都发生在 historySettled 尚为 false 时，两次都提前返回；
  //    真正的发送发生在 setHistorySettled(true) 触发的**更新** effect 上（dev 不双执行）。
  //  - consumedKeyRef —— 认领是同步语句，先于 handleSend 的任何 await。即便某天
  //    effect 被执行两次，第二次看到 key 已认领直接跳过。key 用新 session_id，天然唯一。
  useEffect(() => {
    if (!pendingQuestion || !historySettled) return;
    if (consumedKeyRef.current === pendingQuestion.key) return;
    consumedKeyRef.current = pendingQuestion.key;
    onPendingQuestionConsumed?.();   // 父级同步清空 → 本 effect 再跑时幂等
    handleSend(pendingQuestion.text);
  }, [pendingQuestion, historySettled]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 只在「本来就贴着底」时才跟随。无脑 scrollIntoView 会在用户往回翻历史时
  // 把视口反复拽回底部——流式期间内容每帧都在变高，这个毛病会被放大到没法读。
  function handleScroll() {
    const el = scrollRef.current;
    if (!el) return;
    atBottomRef.current = el.scrollHeight - el.scrollTop - el.clientHeight < 40;
  }

  useEffect(() => {
    if (atBottomRef.current) bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);
  useEffect(() => {
    if (sending && atBottomRef.current) bottomRef.current?.scrollIntoView({ behavior: 'auto' });
  }, [trajNodes, sending]);

  const liveAnsText = answerTextOf(trajNodes);

  // 停止生成：断开 SSE 连接本身就是取消信号（服务端生成器关闭 → 取消本轮），
  // 收尾（收正文、落气泡、清 sending）全在 handleSend 的 finally 里统一走
  function handleStop() {
    stopRef.current?.abort();
  }

  async function handleSend(q: string) {
    if (!session || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    setMessages(prev => [...prev, { role: 'user', content: q, time: now }]);
    atBottomRef.current = true;   // 自己发的消息：无论刚才翻到哪儿都跟到底部
    setSending(true);
    setLiveUsage([]); usageRef.current = [];
    reset();
    setApprovals([]);
    let finalAnswer = '';
    let doneTrace: TraceSummary | undefined;
    let legacyThink = '';
    let legacyAns = '';
    // 停止生成：abort 这次 fetch → 服务端连接断开 → AgentLoop 取消本轮（stop_reason=cancelled）。
    // 只认自己发起的 abort（signal.aborted），避免把网络错误当成「用户停止」。
    const controller = new AbortController();
    stopRef.current = controller;
    let stopped = false;
    try {
      // 两个 id 恒存在：会话一律先在空态经 POST /workspace/sessions 建好再进聊天
      const body: any = { question: q };
      if (session.conversation_id != null) body.conversation_id = session.conversation_id;
      if (session.session_id != null) body.session_id = session.session_id;
      const r = await fetch(`${API}/api/agents/${session.agent_key}/chat/stream`, {
        method: 'POST', headers: { 'Content-Type': 'application/json', ...H },
        body: JSON.stringify(body), signal: controller.signal,
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
              break;
          }
        }
      }
    } catch {
      stopped = controller.signal.aborted;
      // 主动停止不是失败：不说「请求失败」，已收到的正文照常留在气泡里
      if (!stopped) finalAnswer = '请求失败，请稍后重试。';
    } finally {
      if (stopRef.current === controller) stopRef.current = null;
      forceFlush();
      setSending(false);
      const nodes = nodesRef.current;
      const ansText = answerTextOf(nodes);
      const content = finalAnswer || ansText || legacyAns
        || (stopped ? '已停止生成。' : '抱歉，暂时无法处理。');
      setMessages(prev => [...prev, {
        role: 'agent', content,
        time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
        nodes: nodes.length ? nodes : undefined,
        usage: usageRef.current.length ? usageRef.current : undefined,
        thinking: legacyThink || undefined,
        trace: doneTrace,
      }]);
      onTurnDone();   // 模型这一轮可能落了文件：侧栏 + 文件树一起刷新
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

  const showEmpty = !loadingHistory && messages.length === 0 && !sending;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontFamily: W.font, background: W.bg }}>

      {/* 顶栏：会话标题 + 运行指示 + 右栏开合 */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: WS.sm, height: 48, padding: `0 ${WS.base}px`,
        flexShrink: 0, borderBottom: `0.5px solid ${W.borderSoft}`,
      }}>
        <HeaderDot active={sending} />
        <span style={{
          fontSize: 14, fontWeight: 500, color: W.text,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
        }}>
          {session ? (session.label || session.session_id?.slice(0, 8)) : '工作区'}
        </span>
        {sending && <span style={{ fontSize: 13, color: W.tertiary, flexShrink: 0 }}>运行中</span>}
        <div style={{ flex: 1 }} />
        <button onClick={onToggleRight} title={rightOpen ? '收起文件面板' : '展开文件面板'}
          className="ws-btn ws-round"
          style={iconBtn}>
          <PanelRight size={16} />
        </button>
      </header>

      {/* 内容轴：消息列与输入卡共用同一根居中轴，输入卡靠 seat 的 padding 差外扩 32px */}
      <div style={{
        flex: 1, minHeight: 0, width: '100%', maxWidth: CHAT_COLUMN, margin: '0 auto',
        display: 'flex', flexDirection: 'column',
      }}>

        {/* 消息区 */}
        <div ref={scrollRef} onScroll={handleScroll}
          style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `${WS.base}px ${WS.base}px ${WS.sm}px` }}>
          {loadingHistory && (
            <div style={{ textAlign: 'center', color: W.tertiary, marginTop: WS.huge, fontSize: 13 }}>加载历史消息…</div>
          )}

          {/* 会话内空态：开场白（会话已建好，只是还没说过话） */}
          {showEmpty && (
            <div style={{
              minHeight: '100%', display: 'flex', flexDirection: 'column',
              justifyContent: 'center', alignItems: 'flex-start', paddingBottom: 48,
            }}>
              <div style={{ fontSize: 26, lineHeight: '32px', fontWeight: 500, color: W.text, marginBottom: WS.md }}>
                你好，我是工作区智能体
              </div>
              <div style={{ fontSize: 14, lineHeight: '24px', color: W.tertiary, maxWidth: 520 }}>
                我是运行在你平台的编码智能体。直接开始对话，或先在右侧浏览、上传文件给我。
                我执行工具时落下的文件会出现在文件树里，可预览、可下载。
              </div>
            </div>
          )}

          {messages.map((msg, i) => msg.role === 'user' ? (
            <div key={i} className="ws-hover-host"
              style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-end', marginBottom: TURN_GAP }}>
              <div className="ws-round" style={{
                maxWidth: '78%', background: W.surfaceHi, color: W.text,
                borderRadius: R.xl, padding: '10px 16px',
                fontSize: 14, lineHeight: '22px', whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              }}>{msg.content}</div>
              <span className="ws-on-hover" style={{ fontSize: 12, color: W.dimmed, marginTop: 6, paddingRight: 4 }}>{msg.time}</span>
            </div>
          ) : (
            <div key={i} style={{ marginBottom: TURN_GAP }}>
              <AgentTurn
                nodes={msg.nodes}
                content={msg.content}
                usage={msg.usage}
                trace={msg.trace}
                legacyThinking={msg.thinking}
                time={msg.time}
              />
            </div>
          ))}

          {/* 实时回合 */}
          {sending && (
            <div style={{ marginBottom: TURN_GAP }}>
              <AgentTurn nodes={trajNodes} content={liveAnsText} usage={liveUsage} running />
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* 输入卡：与消息列同轴，向外多占 32px（harness 的 composer-card 关系） */}
        <div className="ws-composer-seat" style={{ paddingBottom: WS.base }}>
          {/* 审批卡片：后端工具调用挂起中，不裁决会等到超时 */}
          {approvals.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: WS.sm, marginBottom: WS.sm }}>
              {approvals.map(a => (
                <div key={a.approvalId} style={{
                  borderRadius: R.md, background: W.surface,
                  boxShadow: `0 0 0 0.5px ${W.warning}66, 0 4px 16px rgba(0,0,0,0.35)`,
                  padding: `${WS.md}px ${WS.base}px`,
                }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 6 }}>
                    <ShieldAlert size={14} color={W.warning} />
                    <span style={{ fontSize: 13, fontWeight: 500, color: W.text }}>
                      模型申请提权：{a.metadata.from ?? '?'} → {a.metadata.to ?? '?'}
                    </span>
                  </div>
                  <div style={{ fontSize: 13, lineHeight: '22px', color: W.secondary, whiteSpace: 'pre-wrap', wordBreak: 'break-word', marginBottom: WS.sm }}>
                    {a.metadata.justification || a.reason || '（未给理由）'}
                  </div>
                  <div style={{ fontSize: 12, lineHeight: '20px', color: W.dimmed, marginBottom: WS.md }}>
                    只放行这一次 {a.tool} 调用；不裁决将等待至超时（超时按不可用处理）
                  </div>
                  <div style={{ display: 'flex', gap: WS.sm }}>
                    <button onClick={() => resolveApproval(a.approvalId, 'allow-once')}
                      className="ws-round" style={primaryBtn}>允许一次</button>
                    <button onClick={() => resolveApproval(a.approvalId, 'reject')}
                      className="ws-btn ws-round" style={outlineBtn}>拒绝</button>
                  </div>
                </div>
              ))}
            </div>
          )}

          <Composer
            disabled={!session || sending}
            sending={sending}
            onStop={handleStop}
            uploading={uploading}
            uploadError={uploadError}
            onSend={handleSend}
            onUpload={onUpload}
            sandboxMode={sandboxMode}
            onModeChange={onModeChange}
          />
        </div>
      </div>
    </div>
  );
}

// ── 局部按钮样式（几何内联、颜色走 token）──

// 背景一律交给 .ws-btn* 持有：行内 background 会压死 :hover 蒙层
const iconBtn: React.CSSProperties = {
  width: 28, height: 28, borderRadius: 999, flexShrink: 0,
  color: W.secondary,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};

// 主按钮：黑白反转，不用品牌蓝 —— 参考实现里蓝只留给链接与信息态
const primaryBtn: React.CSSProperties = {
  height: 32, padding: '0 16px', borderRadius: 999, border: 'none',
  background: W.primary, color: W.primaryText, fontSize: 13, fontWeight: 500,
  cursor: 'pointer', fontFamily: 'inherit',
};

const outlineBtn: React.CSSProperties = {
  height: 32, padding: '0 16px', borderRadius: 999,
  border: `0.5px solid ${W.borderStrong}`,
  color: W.text, fontSize: 13, fontFamily: 'inherit',
};
