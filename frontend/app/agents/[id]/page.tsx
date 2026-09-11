'use client';

import { useState, useRef, useEffect } from 'react';
import { useParams } from 'next/navigation';
import { T, S, inputField, labelField, btnPrimary } from '@/app/theme';
import { PanelLeftClose, PanelLeftOpen, Brain, ChevronDown, ChevronRight } from 'lucide-react';
import ToolBinding from '../_components/ToolBinding';
import CachePolicyEditor, { type CachePolicyData } from '../_components/CachePolicyEditor';
import TrajectoryTimeline from '../_components/TrajectoryTimeline';
import TraceSummaryRow from '../_components/TraceSummaryRow';
import { useTrajectoryBatch } from '../_components/useTrajectoryBatch';
import {
  type TrajNode, type UsageRow, type TraceSummary, type TrajectorySnapshot,
  answerTextOf, groupSnapshotByTurn,
} from '@/lib/trajectory';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

const MODEL_BASE_URLS: Record<string, string> = {
  'deepseek-chat': 'https://api.deepseek.com',
  'deepseek-reasoner': 'https://api.deepseek.com',
  'deepseek-v4-pro': 'https://api.deepseek.com',
  'gpt-5.5': 'https://api.openai.com',
  'gpt-5.4': 'https://api.openai.com',
  'gpt-5.4-mini': 'https://api.openai.com',
  'gpt-4o': 'https://api.openai.com',
};

const AGENT_DEFAULTS: Record<string, { model: string; base_url: string }> = {
  faqagent:     { model: 'deepseek-chat', base_url: 'https://api.deepseek.com' },
  order_agent:  { model: 'deepseek-chat', base_url: 'https://api.deepseek.com' },
  ticket_agent: { model: 'deepseek-chat', base_url: 'https://api.deepseek.com' },
  supervisor:   { model: 'deepseek-chat', base_url: 'https://api.deepseek.com' },
};

/** DSH ReasoningRow 移植：折叠=最新行跟读（流式）/ 首行摘要（完成），点击展开全文 */
function ThinkingPanel({ thinking, running }: {
  thinking: string; running: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const bodyRef = useRef<HTMLDivElement>(null);
  // 折叠态摘要：流式中取最新一行（字幕跟读），完成取第一行
  const trimmed = thinking.trimEnd();
  const summary = running
    ? trimmed.slice(trimmed.lastIndexOf('\n') + 1)
    : trimmed.slice(0, trimmed.indexOf('\n') === -1 ? trimmed.length : trimmed.indexOf('\n'));
  // DSH 折叠态：流式中水平滚动到最新文本末尾（字幕跟读），完成回到开头
  useEffect(() => {
    if (bodyRef.current) {
      bodyRef.current.scrollLeft = running
        ? bodyRef.current.scrollWidth - bodyRef.current.clientWidth
        : 0;
    }
  }, [summary, running]);

  return (
    <>
    <style>{`@keyframes spin{to{transform:rotate(360deg)}} .stream-cursor{display:inline-block;width:2px;height:1em;background:${T.accent};margin-left:2px;vertical-align:-2px;animation:blink 1s steps(1) infinite} @keyframes blink{50%{opacity:0}}`}</style>
    <div style={{ padding:`${S.md}px ${S.base}px`, borderRadius:8, background:T.surface, border:`1px solid ${T.border}`, marginBottom:S.sm, cursor: thinking ? 'pointer' : 'default' }}
      onClick={() => thinking && setExpanded(v => !v)}>
      <div style={{ display:'flex', alignItems:'center', gap:S.sm, fontSize:12, fontWeight:600, color:T.secondary }}>
        {running ? (
          <span style={{ width:14, height:14, borderRadius:'50%', border:'2px solid '+T.accent, borderTopColor:'transparent', animation:'spin 0.8s linear infinite', display:'inline-block' }} />
        ) : (
          <Brain size={14} />
        )}
        <span>深度思考</span>
        {running && <span style={{ fontSize:11, color:T.tertiary }}>思考中...</span>}
        {thinking && (
          <span style={{ marginLeft:'auto', color:T.tertiary }}>
            {expanded ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          </span>
        )}
      </div>
      {thinking && !expanded && (
        <div style={{ position:'relative', marginTop:S.sm, overflow:'hidden', minWidth:0 }}>
          <div ref={bodyRef} style={{ fontSize:12, color:T.secondary, lineHeight:1.6, whiteSpace:'nowrap', overflow:'hidden' }}>
            {summary}
            {running && <span style={{ display:'inline-block', width:2, height:12, background:T.accent, verticalAlign:-1, marginLeft:2, animation:'blink 1s steps(1) infinite' }} />}
          </div>
          {running && <div style={{ position:'absolute', right:0, top:0, bottom:0, width:40, background:`linear-gradient(to left, ${T.surface}, transparent)` }} />}
        </div>
      )}
      {thinking && expanded && (
        <div style={{ fontSize:12, color:T.secondary, lineHeight:1.6, marginTop:S.sm, maxHeight:220, overflow:'auto', whiteSpace:'pre-wrap', wordBreak:'break-word' }}>
          {thinking}
        </div>
      )}
    </div>
    </>
  );
}

function getVisitorId(agentKey: string): string {
  if (typeof window === 'undefined') return '';
  const key = `visitor_${agentKey}`;
  let id = localStorage.getItem(key);
  if (!id) { id = crypto.randomUUID(); localStorage.setItem(key, id); }
  return id;
}

function getSavedConversationId(agentKey: string): number | null {
  if (typeof window === 'undefined') return null;
  const saved = localStorage.getItem(`conv_${agentKey}`);
  return saved ? parseInt(saved) : null;
}

// AgentRuntime 多轮 Session id：done 事件带回 → localStorage 持久化 → 下一轮回传。
// 与 conv 同生命周期（localStorage：重启浏览器仍续最近一场对话，后端冷恢复续 Agent 上下文）
function getSavedSessionId(agentKey: string): string | null {
  if (typeof window === 'undefined') return null;
  return localStorage.getItem(`sid_${agentKey}`);
}

interface ChatMsg {
  role: 'user' | 'agent'; content: string; time: string;
  thinking?: string;                 // 遗留兜底（无轨迹的历史数据）
  nodes?: TrajNode[];                // Agent Trajectory（think/tool 行；answer 不存这里）
  usage?: UsageRow[];
  trace?: TraceSummary;              // 执行摘要（TraceProjection 产物；history 来自 metadata.trace，实时来自 done.trace）
}

// 待人工裁决的审批申请（approval.request 事件 → 卡片 → POST /approvals/{id}）。
// 后端调用此刻**正挂起等待**，不裁决就一直等到超时（超时按不可用处理）。
//
// 审批是通用的（interaction.approval），字段刻意领域中立：reason 是给人看的
// 一句话，metadata 放领域细节（沙箱的 from/to 模式就在里面）—— 前端只做透传，
// 不解释 metadata。
interface PendingApproval {
  approvalId: string;
  tool: string;
  reason: string;       // 给人看的一句话（模型自己写的理由在里面）
  metadata: Record<string, any>;
}

// 历史会话项（服务端 conversations 列表，channel=agent:{key}，与 DeepSeek 会话列表同源）
interface HistConv {
  id: number;
  last_msg: string;
  last_time: string;
  count: number;
  session_id?: string | null;  // 最近一次 chat 的 Agent Session（轨迹回放定位）
}

const DEFAULT_CONFIG = { system_prompt: '', fallback_reply: '', api_key: '', base_url: '', model: '', max_steps: 5 };

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const agentKey = id as string;

  const [agentName, setAgentName] = useState('');
  const [agentDesc, setAgentDesc] = useState('');
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  // 初值恒为 null：localStorage 读取放在 mount effect 里（SSR 首帧无本地存储，
  // 直接读会 hydation mismatch —— select 的 value 与 option 集合必须服务端/客户端一致）
  const [conversationId, setConversationId] = useState<number | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [hist, setHist] = useState<HistConv[]>([]);
  const histRef = useRef<HistConv[]>([]);  // hist 镜像：SSE 循环内判断「是否新会话」用
  // 实时 Trajectory：rAF 批处理 reducer（think/tool node 流）+ usage 低频 state
  const { nodes: trajNodes, nodesRef, enqueue, forceFlush, reset } = useTrajectoryBatch();
  const [liveUsage, setLiveUsage] = useState<UsageRow[]>([]);
  const usageRef = useRef<UsageRow[]>([]);
  // 待裁决的升权申请：后端工具调用挂起中，不裁决 = 一直等到超时（拒绝）
  const [approvals, setApprovals] = useState<PendingApproval[]>([]);

  useEffect(() => {
    // mount：从 localStorage 恢复最近一场对话（重启浏览器 → 自动续聊最近会话）。
    // 放 effect 而非 state 初值，保证 SSR/客户端首帧渲染一致。
    const cid = getSavedConversationId(agentKey);
    const sid = getSavedSessionId(agentKey);
    if (cid) setConversationId(cid);
    if (sid) setSessionId(sid);
  }, [agentKey]);

  // 历史会话列表（服务端拉取，channel=agent:{key}）：与 DeepSeek 会话列表同源 ——
  // 数据在服务端 conversations/messages 表，重启浏览器/换设备都能看到
  useEffect(() => {
    fetch(`${API}/api/chat/conversations?channel=agent:${agentKey}`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } })
      .then(r => r.json()).then(d => {
        const items = (d.items || []).map((c: any) => ({
          id: c.id, last_msg: c.last_msg || '', last_time: c.last_time || '', count: c.msg_count || 0,
          session_id: c.session_id || null,  // SELECT c.* 透传；老数据无 → null
        }));
        histRef.current = items;
        setHist(items);
      }).catch(() => {});
  }, [agentKey]);

  // 重新拉历史列表：新建会话首次回复后（conversation_id 刚产生，不在 hist 里）
  // 下拉框会卡在「当前会话（恢复中…）」—— 拉到新会话即可消除
  const refreshHist = () => {
    fetch(`${API}/api/chat/conversations?channel=agent:${agentKey}`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } })
      .then(r => r.json()).then(d => {
        const items = (d.items || []).map((c: any) => ({
          id: c.id, last_msg: c.last_msg || '', last_time: c.last_time || '', count: c.msg_count || 0,
          session_id: c.session_id || null,
        }));
        histRef.current = items;
        setHist(items);
      }).catch(() => {});
  };

  const bottomRef = useRef<HTMLDivElement>(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [cachePolicy, setCachePolicy] = useState<Record<string, any> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [leftOpen, setLeftOpen] = useState(true);
  const VISITOR_ID = getVisitorId(agentKey);

  useEffect(() => {
    fetch(`${API}/api/agents/${agentKey}/config`).then(r => r.json()).then(d => {
      if (d.config) {
        const def = AGENT_DEFAULTS[agentKey];
        setConfig({
          system_prompt: d.config.system_prompt ?? '',
          fallback_reply: d.config.fallback_reply ?? '',
          api_key: d.config.api_key ?? '',
          base_url: d.config.base_url || def?.base_url || '',
          model: d.config.model || def?.model || '',
          max_steps: d.config.max_steps ?? 5,
        });
      }
      setCachePolicy(d.cache_policy ?? null);
    }).catch(() => {});
  }, [agentKey]);

  useEffect(() => {
    // Load agent info from list
    fetch(`${API}/api/agents`).then(r => r.json()).then(list => {
      const a = (list as any[]).find((x: any) => x.key === agentKey);
      if (a) { setAgentName(a.name); setAgentDesc(a.desc); }
    }).catch(() => {});
  }, [agentKey]);

  useEffect(() => {
    if (!conversationId) { setLoadingHistory(false); return; }
    let cancelled = false;
    (async () => {
      setLoadingHistory(true);  // 会话切换 / 自动恢复时显示加载态
      try {
        // 消息 + Session 轨迹并行拉取：conversations.session_id（服务端映射）→ 回放端点
        // → think/tool 轨迹按 turn 对齐到末尾 agent 消息；无 session_id（老数据）降级
        // metadata.thinking（原行为），不做字符串猜测。
        const convR = await fetch(`${API}/api/chat/conversations/${conversationId}`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } });
        const conv = convR.ok ? await convR.json() : null;
        const sid: string | null = conv?.session_id || null;
        const msgsR = await fetch(`${API}/api/chat/conversations/${conversationId}/messages`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } });
        const data = await msgsR.json();
        let snap: TrajectorySnapshot | null = null;
        if (sid) {
          const trR = await fetch(`${API}/api/agents/sessions/${sid}/trajectory`);
          if (trR.ok) snap = await trR.json();
        }
        const items = data.items || [];
        const msgs: ChatMsg[] = [];
        for (const m of items) {
          const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' }) : '';
          const meta = m.metadata || {};
          if (m.role === 'agent') msgs.push({ role:'agent', content:m.content, time, thinking: meta.thinking || undefined, trace: meta.trace || undefined });
          else if (m.role === 'customer') msgs.push({ role:'user', content:m.content, time });
        }
        // 轨迹对齐：session 覆盖该会话最近 N 个 agent turn（节点按 turn id 分组；
        // 老数据无 session_id / 会话跨 session 时，超出的轮保持 thinking 降级）
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
      } catch {
        // 网络失败：保持现状
      } finally {
        if (!cancelled) setLoadingHistory(false);
      }
    })();
    return () => { cancelled = true; };
  }, [conversationId]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);
  // 实时轨迹滚动跟随：rAF batch 后滚到底（auto 不排队）；轮末 smooth 滚动由上方 effect 承担
  useEffect(() => {
    if (sending) bottomRef.current?.scrollIntoView({ behavior:'auto' });
  }, [trajNodes, sending]);
  // 当前 turn 的实时回答文本（answer node 增量；渲染用）
  const liveAnsText = answerTextOf(trajNodes);

  async function handleSaveConfig() {
    setSaving(true); setSaveMsg('');
    try {
      const body: any = { ...config };
      if (cachePolicy !== null) body.cache_policy = cachePolicy;
      const r = await fetch(`${API}/api/agents/${agentKey}/config`, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(body) });
      setSaveMsg(r.ok ? '保存成功' : '保存失败');
    } catch { setSaveMsg('保存失败'); }
    finally { setSaving(false); }
  }

  function handleNewChat() {
    setMessages([]); setConversationId(null); setSessionId(null);
    localStorage.removeItem(`conv_${agentKey}`); localStorage.removeItem(`sid_${agentKey}`);
  }

  // 切换到历史会话：加载该场对话消息（conversationId state 变化 → 下方 effect 拉消息）并继续在该场聊。
  // 只有「最近一场」的 Agent Session id 存在本地；切到更早会话不带 session_id →
  // 后端新建 Runtime Session（消息仍追加进同一场对话，记录连续；Agent 上下文从头 ——
  // 完整会话级 Agent 记忆恢复属于 conversation↔session 服务端映射（后续阶段）。
  function switchConversation(id: number) {
    if (conversationId === id) return;
    setMessages([]);
    setConversationId(id);
    const savedSid = getSavedSessionId(agentKey);
    setSessionId(savedSid && getSavedConversationId(agentKey) === id ? savedSid : null);
  }

  function fmtConvTime(t: string): string {
    if (!t) return '';
    const d = new Date(t);
    const p = (n: number) => String(n).padStart(2, '0');
    return `${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
  }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const q = input;
    const now = new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
    setMessages(prev => [...prev, { role:'user', content:q, time:now }]);
    setInput(''); setSending(true);
    setLiveUsage([]); usageRef.current = [];
    reset();  // 清空上个 turn 的 trajectory nodes（flush 尾部滞留 + reducer reset）
    setApprovals([]);  // 上个 turn 若留下卡片（后端已被取消）不清会一直挂着
    let finalAnswer = '';
    let doneTrace: TraceSummary | undefined;  // done.trace：本轮执行摘要（TraceProjection 产物）
    let legacyThink = '';   // session=None 遗留路径：thinking 降级累积（无轨迹时兜底显示）
    let legacyAns = '';     // 遗留路径 answer/text 降级累积
    try {
      const body: any = { question: q, visitor_id: VISITOR_ID };
      if (conversationId) body.conversation_id = conversationId;
      if (sessionId) body.session_id = sessionId;  // 多轮：回传 AgentRuntime Session id 续上历史
      const r = await fetch(`${API}/api/agents/${agentKey}/chat/stream`, { method:'POST', headers:{'Content-Type':'application/json','X-Tenant-ID':String(TENANT_ID)}, body:JSON.stringify(body) });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      // 解析 SSE 事件流（data: JSON 空行分隔）：
      //   traj/* → rAF 批处理（高频 delta 不进 React 同步路径）；usage → 低频 state；
      //   done.answer 为最终权威文本（guard 轮合成回答也以它为准）。
      const reader = r.body.getReader();
      const decoder = new TextDecoder();
      let buf = '';
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream:true });
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
              enqueue(ev as any);  // 投影事件 → rAF buffer（≤60fps 批量渲染）
              break;
            case 'usage': {
              const row = { ...ev } as UsageRow;
              usageRef.current = [...usageRef.current, row];
              setLiveUsage(usageRef.current);
              break;
            }
            case 'approval.request': {
              // 待确认申请：后端此刻已挂起，等这里的裁决（或超时 → 不可用）
              const d = ev.data || {};
              if (!d.approval_id) break;
              setApprovals(prev => [
                ...prev.filter(p => p.approvalId !== d.approval_id),
                { approvalId: d.approval_id, tool: ev.tool || '',
                  reason: d.reason || '', metadata: d.metadata || {} },
              ]);
              break;
            }
            case 'sandbox.escalation':
              // 裁决落地（批准 / 拒绝 / 超时）→ 收起对应卡片，approval_id 精确配对
              if (ev.data?.approval_id) {
                const done = ev.data.approval_id;
                setApprovals(prev => prev.filter(p => p.approvalId !== done));
              }
              break;
            case 'thinking':  // 遗留路径（session=None）：不做实时展示，仅 done 后折叠兜底
              legacyThink += ev.delta;
              break;
            case 'text':
            case 'answer':
              legacyAns += ev.delta;
              break;
            case 'done':
              if (ev.answer) finalAnswer = ev.answer;
              doneTrace = ev.trace;  // 执行摘要（后端 TraceProjection 产物，随 done 回传）
              // session/conversation id 持久到 localStorage：重启浏览器自动续最近一场
              if (ev.session_id) { setSessionId(ev.session_id); localStorage.setItem(`sid_${agentKey}`, ev.session_id); }
              if (ev.conversation_id) {
                const cid = Number(ev.conversation_id);
                setConversationId(cid); localStorage.setItem(`conv_${agentKey}`, String(cid));
                // 首次回复产生的新会话：刷新历史列表，否则下拉框停在「当前会话（恢复中…）」
                if (!histRef.current.some(h => h.id === cid)) refreshHist();
              }
              break;
          }
        }
      }
    } catch {
      finalAnswer = '请求失败，请稍后重试。';
    } finally {
      forceFlush();  // 尾部滞留事件立即应用（done 后不再有 delta，收口一致性）
      setSending(false);
      const nodes = nodesRef.current;  // 同步镜像：含本轮全部已应用 node
      const ansText = answerTextOf(nodes);
      const content = finalAnswer || ansText || legacyAns || '抱歉，暂时无法处理。';
      setMessages(prev => [...prev, {
        role:'agent', content, time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}),
        // 轨迹存档（answer node 不存档：timeline 忽略它，气泡以 content 为准防重复）；
        // 无节点（legacy/退化）→ 保留 thinking 供原 ThinkingPanel 兜底
        nodes: nodes.length ? nodes : undefined,
        usage: usageRef.current.length ? usageRef.current : undefined,
        thinking: legacyThink || undefined,
        trace: doneTrace,
      }]);
    }
  }

  // 裁决一次升权申请：唤醒后端挂起的工具调用。
  // 先乐观收起卡片（后端也会推 sandbox.escalation 精确收口）；404 = 已超时 / 已处理。
  async function resolveApproval(approvalId: string, decision: 'allow-once' | 'reject') {
    setApprovals(prev => prev.filter(p => p.approvalId !== approvalId));
    try {
      await fetch(`${API}/api/agents/approvals/${approvalId}`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ decision }),
      });
    } catch { /* 后端已超时 / 断连：卡片已收起，不再打扰 */ }
  }

  return (
    <div style={{ display:'flex', height:'100vh', background:T.bg, color:T.text, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif", overflow:'auto' }}>
      {/* Left config panel */}
      <div style={{
        width: leftOpen ? 300 : 0, minWidth: leftOpen ? 300 : 0,
        background:T.surface, borderRight: leftOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease',
        overflow:'hidden',
      }}>
        <div style={{ padding:`${S.lg}px ${S.xl}px ${S.base}px` }}>
          <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>{agentName || agentKey}</h3>
          <p style={{ margin:0, marginTop:S.xs, fontSize:13, color:T.secondary }}>{agentDesc || '自定义智能体'}</p>
        </div>
        <div style={{ flex:1, padding:`0 ${S.xl}px`, display:'flex', flexDirection:'column', gap:S.md, overflow:'auto', paddingBottom:S.xl }}>
          <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase' }}>LLM 配置</div>
          <label style={labelField}>API Key
            <input type="password" value={config.api_key} onChange={e => setConfig(p=>({...p, api_key:e.target.value.trim()}))}
              placeholder="sk-xxx" style={{...inputField, fontFamily:'monospace'}} />
          </label>
          <label style={labelField}>Base URL
            <input type="text" value={config.base_url} onChange={e => setConfig(p=>({...p, base_url:e.target.value.trim()}))}
              placeholder="https://api.deepseek.com" style={{...inputField, fontFamily:'monospace'}} />
          </label>
          <label style={labelField}>Model
            <select value={config.model} onChange={e => { const m = e.target.value; setConfig(p => ({ ...p, model: m, base_url: (!p.base_url || Object.values(MODEL_BASE_URLS).includes(p.base_url)) ? (MODEL_BASE_URLS[m] || '') : p.base_url })); }}
              style={{...inputField, padding:'8px 10px'}}>
              <option value="">未选择</option>
              <optgroup label="DeepSeek">
                <option value="deepseek-chat">DeepSeek V4 Flash (chat)</option>
                <option value="deepseek-reasoner">DeepSeek V4 Flash (reasoner)</option>
                <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
              </optgroup>
              <optgroup label="OpenAI">
                <option value="gpt-5.5">GPT-5.5</option>
                <option value="gpt-5.4">GPT-5.4</option>
                <option value="gpt-5.4-mini">GPT-5.4 Mini</option>
                <option value="gpt-4o">GPT-4o</option>
              </optgroup>
              <optgroup label="Anthropic">
                <option value="claude-opus-4-8">Claude Opus 4.8</option>
                <option value="claude-opus-4-7">Claude Opus 4.7</option>
                <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                <option value="claude-haiku-4-5">Claude Haiku 4.5</option>
              </optgroup>
              <optgroup label="Google">
                <option value="gemini-3.1-pro">Gemini 3.1 Pro</option>
                <option value="gemini-3.5-flash">Gemini 3.5 Flash</option>
              </optgroup>
            </select>
          </label>

          <div style={{ paddingTop:S.sm }}>
            <ToolBinding agentKey={agentKey} />
          </div>

          <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginTop:S.sm }}>回复设置</div>
          <label style={labelField}>系统提示词
            <textarea rows={6} value={config.system_prompt} onChange={e => setConfig(p=>({...p, system_prompt:e.target.value}))}
              style={{ ...inputField, resize:'vertical', minHeight:80 }} />
          </label>
          <label style={labelField}>兜底回复
            <textarea rows={3} value={config.fallback_reply} onChange={e => setConfig(p=>({...p, fallback_reply:e.target.value}))}
              style={{ ...inputField, resize:'vertical', minHeight:50 }} />
          </label>

          {/* 缓存策略 */}
          <div style={{
            marginTop: S.md, borderTop: `1px solid ${T.border}`, paddingTop: S.md,
          }}>
            <div style={{ marginBottom: S.sm }}>
              <span style={{ fontSize: 11, fontWeight: 600, color: T.secondary, letterSpacing: '0.04em', textTransform: 'uppercase' }}>
                缓存策略
              </span>
              {cachePolicy && <span style={{
                fontSize: 10, color: T.success, background: '#E8FFEA',
                padding: '1px 6px', borderRadius: 8, fontWeight: 500, marginLeft: S.sm,
              }}>已配置</span>}
            </div>

            <CachePolicyEditor
              value={{
                base_score: cachePolicy?.base_score ?? 0.50,
                cacheable_intents: cachePolicy?.cacheable_intents ?? [],
                block_entities: cachePolicy?.block_entities ?? [],
                content_hint: cachePolicy?.content_hint ?? '',
                scorer_weights: cachePolicy?.scorer_weights ?? undefined,
              }}
              onChange={(p: CachePolicyData) => setCachePolicy((prev: any) => ({ ...prev, ...p }))}
            />
          </div>
        </div>

        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          {saveMsg && <div style={{ fontSize:12, marginBottom:S.sm, color:saveMsg==='保存成功'?T.success:T.danger }}>{saveMsg}</div>}
          <button onClick={handleSaveConfig} disabled={saving} style={{...btnPrimary, width:'100%', padding:'10px 0', fontSize:14, opacity:saving?0.5:1 }}>{saving?'保存中...':'保存配置'}</button>
        </div>
      </div>

      {/* Center: chat */}
      {/* minWidth:420 保底聊天列可用宽度（窄视口下宁可横向滚动也不把聊天区压扁）；minWidth 同时避免内部长内容撑爆 */}
      <div style={{ flex:1, minWidth:420, minHeight:0, display:'flex', flexDirection:'column', background:T.bg }}>
        <div style={{ padding:`${S.md}px ${S.xl}px`, display:'flex', alignItems:'center', gap:S.sm, borderBottom:`1px solid ${T.border}` }}>
          <button onClick={() => setLeftOpen(!leftOpen)} title={leftOpen?'收起配置':'展开配置'} style={{
            width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
          }}>
            {leftOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
          </button>
          <span style={{ width:7, height:7, borderRadius:'50%', background:T.accent }} />
          <span style={{ fontSize:14, fontWeight:500, color:T.text }}>测试对话</span>
          <div style={{ flex:1 }} />
          {/* 历史会话（服务端列表）：重启浏览器/换设备都在 —— DeepSeek 式历史会话恢复 */}
          <select title="历史会话" value={conversationId ?? '__new'}
            onChange={e => { const v = e.target.value; if (v === '__new') handleNewChat(); else switchConversation(Number(v)); }}
            style={{ maxWidth:260, padding:'4px 10px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, fontSize:12, cursor:'pointer', outline:'none' }}>
            <option value="__new">＋ 新对话</option>
            {conversationId !== null && !hist.some(h => h.id === conversationId) && (
              <option value={conversationId}>当前会话（恢复中…）</option>
            )}
            {hist.map(h => (
              <option key={h.id} value={h.id}>
                {fmtConvTime(h.last_time)}{h.count > 1 ? `（${h.count} 条）` : ''}{h.last_msg ? ` · ${h.last_msg.replace(/\s+/g, ' ').slice(0, 18)}` : ''}
              </option>
            ))}
          </select>
          <button onClick={handleNewChat} style={{ padding:'5px 14px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, fontSize:12, cursor:'pointer' }}>新建会话</button>
        </div>

        <div style={{ flex:1, minHeight:0, padding:S.xl, overflow:'auto' }}>
          {loadingHistory && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>加载历史消息...</div>}
          {!loadingHistory && messages.length===0 && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>输入消息，测试智能体</div>}
          {messages.map((msg, i) => {
            const isUser = msg.role === 'user';
            return (
              <div key={i} style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:isUser?'flex-end':'flex-start' }}>
                <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{isUser?'测试用户':(agentName||agentKey)} · {msg.time}</div>
                <div style={{ maxWidth:'min(72%, 820px)', minWidth:0, width:'100%' }}>
                  {/* 历史轨迹（think/tool 行，来自 trajectory 回放端点）；无轨迹老数据 → thinking 降级 */}
                  {!isUser && msg.nodes && msg.nodes.length > 0 && (
                    <TrajectoryTimeline nodes={msg.nodes} usage={msg.usage} running={false} />
                  )}
                  {!isUser && (!msg.nodes || msg.nodes.length === 0) && msg.thinking && (
                    <ThinkingPanel thinking={msg.thinking} running={false} />
                  )}
                  <div style={{
                    padding:`${S.md}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.55,
                    whiteSpace:'pre-wrap', wordBreak:'break-word',
                    background:isUser?T.accent:T.surface, color:isUser?'#fff':T.text,
                    border:isUser?'none':`1px solid ${T.border}`, borderBottomRightRadius:isUser?2:8, borderBottomLeftRadius:isUser?8:2,
                  }}>{msg.content}</div>
                  {/* 执行摘要（TraceProjection）：N 步 · 耗时 · token/命中 · 停止原因；点击展开每步明细 */}
                  {!isUser && msg.trace && <TraceSummaryRow trace={msg.trace} />}
                </div>
              </div>
            );
          })}
          {sending && (
            <div style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:'flex-start' }}>
              <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{agentName||agentKey} · 回复中</div>
              <div style={{ maxWidth:'min(72%, 820px)', minWidth:0, width:'100%' }}>
                {/* Agent Trajectory：think/tool 行按执行序实时投影（typed events → 后端投影 →
                    rAF 批处理 ≤60fps；非字符串猜测）。answer node 由下方气泡渲染 */}
                <TrajectoryTimeline nodes={trajNodes} usage={liveUsage} running={sending} />
                {/* 实时回答气泡（打字机）：answer node 增量文本；done 后以权威 message content 收口 */}
                {liveAnsText && (
                  <div data-streaming="true" style={{ padding:`${S.md}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.55,
                    whiteSpace:'pre-wrap', wordBreak:'break-word',
                    background:T.surface, color:T.text, border:`1px solid ${T.border}`, borderBottomLeftRadius:2 }}>
                    {liveAnsText}
                    <span className="stream-cursor" />
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        {/* 升权待裁决卡片：后端工具调用正挂起等这个按钮，不裁决就一直等到超时 */}
        {approvals.length > 0 && (
          <div style={{ padding:`0 ${S.xl}px ${S.base}px`, display:'flex', flexDirection:'column', gap:S.sm }}>
            {approvals.map(a => (
              <div key={a.approvalId} style={{ border:`1px solid ${T.warning}`, borderRadius:8, background:T.surface, padding:`${S.md}px ${S.base}px` }}>
                <div style={{ fontSize:13, fontWeight:600, color:T.text, marginBottom:6 }}>
                  模型申请提权：{a.metadata.from ?? '?'} → {a.metadata.to ?? '?'}
                </div>
                {/* 理由原文照显：它是模型为自己的请求举证的全部内容 */}
                <div style={{ fontSize:13, color:T.secondary, lineHeight:1.55, whiteSpace:'pre-wrap', wordBreak:'break-word', marginBottom:S.sm }}>
                  {a.metadata.justification || a.reason || '（未给理由）'}
                </div>
                <div style={{ fontSize:11, color:T.tertiary, marginBottom:S.sm }}>
                  只放行这一次 {a.tool} 调用；不裁决将等待至超时（超时按不可用处理）
                </div>
                <div style={{ display:'flex', gap:S.sm }}>
                  <button onClick={() => resolveApproval(a.approvalId, 'allow-once')} style={{...btnPrimary, padding:'6px 16px', fontSize:13 }}>允许一次</button>
                  <button onClick={() => resolveApproval(a.approvalId, 'reject')} style={{ padding:'6px 16px', fontSize:13, background:T.surface, color:T.text, border:`1px solid ${T.border}`, borderRadius:6, cursor:'pointer', fontFamily:'inherit' }}>拒绝</button>
                </div>
              </div>
            ))}
          </div>
        )}

        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          <div style={{ display:'flex', gap:S.sm }}>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key==='Enter'&&handleSend()}
              placeholder="输入消息测试 Agent..."
              style={{ flex:1, minWidth:0, padding:'10px 14px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
            <button onClick={handleSend} disabled={!input.trim()||sending} style={{...btnPrimary, padding:'10px 22px', fontSize:14, opacity:!input.trim()||sending?0.4:1 }}>发送</button>
          </div>
        </div>
      </div>

    </div>
  );
}
