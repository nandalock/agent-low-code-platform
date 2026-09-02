'use client';

import { useState, useRef, useEffect } from 'react';
import { useParams } from 'next/navigation';
import { T, S, inputField, labelField, btnPrimary } from '@/app/theme';
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen, Wrench, Brain, ChevronDown, ChevronRight, X } from 'lucide-react';
import McpToolBinding from '../_components/McpToolBinding';
import CachePolicyEditor, { type CachePolicyData } from '../_components/CachePolicyEditor';

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
  const saved = sessionStorage.getItem(`conv_${agentKey}`);
  return saved ? parseInt(saved) : null;
}

// AgentRuntime 多轮 Session id：done 事件带回 → sessionStorage 持久化 → 下一轮回传（与 conv 同生命周期）
function getSavedSessionId(agentKey: string): string | null {
  if (typeof window === 'undefined') return null;
  return sessionStorage.getItem(`sid_${agentKey}`);
}

interface TraceStep {
  step: number; type: 'llm' | 'tool'; content?: string;
  tool?: string; args?: Record<string,any>; output?: any;
  latency_ms?: number;
}
interface ChatMsg {
  role: 'user' | 'agent'; content: string; time: string;
  thinking?: string;
  trace?: { tier: string; total_ms: number; steps?: TraceStep[] };
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
  const [trace, setTrace] = useState<ChatMsg['trace'] | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(getSavedConversationId(agentKey));
  const [sessionId, setSessionId] = useState<string | null>(getSavedSessionId(agentKey));
  const [loadingHistory, setLoadingHistory] = useState(false);
  // 流式状态（deepseek harness 风格：思考面板 + 工具卡片 + 打字机）
  const [liveThinking, setLiveThinking] = useState('');
  const [liveAnswer, setLiveAnswer] = useState('');
  const [liveTools, setLiveTools] = useState<{tool:string; args:any; status:'running'|'done'; summary?:string; stage?:string; seconds?:number}[]>([]);
  const [liveSteps, setLiveSteps] = useState<{step:number; total:number}>({ step:0, total:0 });

  useEffect(() => {
    if (sessionStorage.getItem(`conv_${agentKey}`)) setLoadingHistory(true);
  }, [agentKey]);

  const bottomRef = useRef<HTMLDivElement>(null);
  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [cachePolicy, setCachePolicy] = useState<Record<string, any> | null>(null);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [expandedSteps, setExpandedSteps] = useState<Record<number, boolean>>({});
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
    fetch(`${API}/api/chat/conversations/${conversationId}/messages`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } })
      .then(r => r.json()).then(data => {
        const items = data.items || [];
        if (items.length > 0) {
          const msgs: ChatMsg[] = [];
          for (const m of items) {
            const time = m.created_at ? new Date(m.created_at).toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' }) : '';
            const meta = m.metadata || {};
            if (m.role === 'agent') msgs.push({ role:'agent', content:m.content, time, thinking: meta.thinking || undefined, trace: meta.trace || undefined });
            else if (m.role === 'customer') msgs.push({ role:'user', content:m.content, time });
          }
          setMessages(msgs);
        }
      }).catch(() => {}).finally(() => setLoadingHistory(false));
  }, [conversationId]);

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);

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

  function handleNewChat() { setMessages([]); setConversationId(null); setSessionId(null); setTrace(null); sessionStorage.removeItem(`conv_${agentKey}`); sessionStorage.removeItem(`sid_${agentKey}`); }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
    setMessages(prev => [...prev, { role:'user', content:input, time:now }]);
    setInput(''); setSending(true);
    setLiveThinking(''); setLiveAnswer(''); setLiveTools([]); setLiveSteps({ step:0, total:0 });
    let finalAnswer = '';
    let liveAns = '';  // 局部累积（避免 state 闭包读到旧值）
    let liveThink = '';  // 局部累积思考（done 后用于生成首行摘要）
    let finalTrace: ChatMsg['trace'] | undefined;
    try {
      const body: any = { question: input, visitor_id: VISITOR_ID };
      if (conversationId) body.conversation_id = conversationId;
      if (sessionId) body.session_id = sessionId;  // 多轮：回传 AgentRuntime Session id 续上历史
      const r = await fetch(`${API}/api/agents/${agentKey}/chat/stream`, { method:'POST', headers:{'Content-Type':'application/json','X-Tenant-ID':String(TENANT_ID)}, body:JSON.stringify(body) });
      if (!r.ok || !r.body) throw new Error(`HTTP ${r.status}`);
      // 解析 SSE 事件流（data: JSON 空行分隔）
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
            case 'step':
              setLiveSteps({ step: ev.step, total: ev.total });
              break;
            case 'thinking':
              liveThink += ev.delta;
              setLiveThinking(p => p + ev.delta);
              break;
            case 'text':
              setLiveAnswer(p => p + ev.delta);
              break;
            case 'tool_call':
              setLiveTools(p => [...p, { tool: ev.tool, args: ev.args, status: 'running' }]);
              break;
            case 'tool_progress':
              setLiveTools(p => {
                const a = [...p];
                const idx = a.findIndex(x => x.tool === ev.tool && x.status === 'running');
                if (idx >= 0) a[idx] = { ...a[idx], stage: ev.stage, seconds: ev.seconds };
                return a;
              });
              break;
            case 'tool_result':
              setLiveTools(p => {
                const a = [...p];
                const idx = a.findIndex(x => x.tool === ev.tool && x.status === 'running');
                if (idx >= 0) a[idx] = { ...a[idx], status:'done', summary: ev.summary || '' };
                return a;
              });
              break;
            case 'answer':
              liveAns += ev.delta;
              setLiveAnswer(p => p + ev.delta);
              break;
            case 'done':
              if (ev.answer) finalAnswer = ev.answer;
              if (ev.trace) finalTrace = { tier: ev.tier, total_ms: ev.trace.total_ms, steps: ev.trace.steps };
              if (ev.session_id) { setSessionId(ev.session_id); sessionStorage.setItem(`sid_${agentKey}`, ev.session_id); }  // 首轮新建 → 保存，后续轮次带回
              if (ev.conversation_id) { setConversationId(ev.conversation_id); sessionStorage.setItem(`conv_${agentKey}`, String(ev.conversation_id)); }  // 会话 id 保存：切页回来据此恢复历史消息
              setLiveThinking(liveThink);  // 保留思考（折叠为首行摘要）
              break;
          }
        }
      }
    } catch {
      finalAnswer = '请求失败，请稍后重试。';
    } finally {
      setSending(false);
      setMessages(prev => [...prev, { role:'agent', content: finalAnswer || liveAns || '抱歉，暂时无法处理。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), thinking: liveThink || undefined, trace: finalTrace }]);
      if (finalTrace) setTrace(finalTrace);
      setLiveThinking(''); setLiveAnswer(''); setLiveTools([]);
    }
  }

  const preSmall: React.CSSProperties = { margin:0, padding:'6px 8px', background:T.bg, borderRadius:4, fontSize:11, fontFamily:'monospace', color:T.text, lineHeight:1.5, overflow:'auto', maxHeight:200, whiteSpace:'pre-wrap', wordBreak:'break-all' };

  const tierLabel = (tier: string) => tier==='llm'?'AI 回答':tier==='fallback'?'兜底回复':tier;
  const tierColor = (tier: string) => tier==='llm'?T.accent:tier==='fallback'?T.warning:T.success;
  const tierBg = (tier: string) => tier==='llm'?T.accentBg:'#FFF7E6';

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
            <McpToolBinding agentKey={agentKey} />
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
          {trace && (
            <span style={{ fontSize:11, padding:'3px 10px', borderRadius:12, fontWeight:500, background:tierBg(trace.tier), color:tierColor(trace.tier) }}>
              {tierLabel(trace.tier)} {trace.total_ms>0 ? `· ${trace.total_ms}ms` : ''}
            </span>
          )}
          <button onClick={() => setRightOpen(!rightOpen)} title={rightOpen?'收起调用链':'展开调用链'} style={{
            width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
          }}>{rightOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}</button>
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
                <div style={{ maxWidth:'min(72%, 820px)', minWidth:0 }}>
                  {!isUser && msg.thinking && (
                    <ThinkingPanel thinking={msg.thinking} running={false} />
                  )}
                  <div style={{
                    padding:`${S.md}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.55,
                    whiteSpace:'pre-wrap', wordBreak:'break-word',
                    background:isUser?T.accent:T.surface, color:isUser?'#fff':T.text,
                    border:isUser?'none':`1px solid ${T.border}`, borderBottomRightRadius:isUser?2:8, borderBottomLeftRadius:isUser?8:2,
                  }}>{msg.content}</div>
                </div>
              </div>
            );
          })}
          {sending && (
            <div style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:'flex-start' }}>
              <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{agentName||agentKey} · 回复中</div>
              <div style={{ maxWidth:'min(72%, 820px)', minWidth:0, width:'100%' }}>
                {/* DSH 式思考面板：折叠=最新一行跟读（流式）/ 首行摘要（完成），展开=全文 */}
                {(liveThinking || liveSteps.step>0) && (
                  <ThinkingPanel thinking={liveThinking} running={sending} />
                )}
                {/* 工具区（DSH tool rows：名称 + 状态动画 + 参数摘要） */}
                {liveTools.length>0 && (
                  <div style={{ display:'flex', flexDirection:'column', gap:6, marginBottom:S.sm }}>
                    {liveTools.map((t,i) => (
                      <div key={i} style={{ padding:`${S.sm}px ${S.base}px`, borderRadius:8, background:T.surface, border:`1px solid ${T.border}`, minWidth:0 }}>
                        <div style={{ display:'flex', alignItems:'center', gap:S.sm, fontSize:12 }}>
                          {t.status==='running' ? (
                            <span style={{ width:12, height:12, borderRadius:'50%', border:'2px solid '+T.accent, borderTopColor:'transparent', animation:'spin 0.8s linear infinite', display:'inline-block', flexShrink:0 }} />
                          ) : (
                            <span style={{ color:T.success, flexShrink:0 }}>✓</span>
                          )}
                          <span style={{ fontWeight:600, color:T.text, fontFamily:'monospace' }}>{t.tool}</span>
                          <span style={{ fontSize:11, color:T.secondary }}>{t.status==='running' ? '运行中' : '完成'}</span>
                          {t.status==='running' && t.seconds ? <span style={{ fontSize:11, color:T.tertiary }}>{t.seconds}s</span> : null}
                        </div>
                        {t.args && Object.keys(t.args).length>0 && (
                          <div style={{ marginTop:4, fontSize:11, color:T.secondary, fontFamily:'monospace', whiteSpace:'nowrap', overflow:'hidden', textOverflow:'ellipsis' }}>
                            {JSON.stringify(t.args, null, 0).slice(0, 160)}
                          </div>
                        )}
                        {t.stage && (
                          <div style={{ marginTop:4, fontSize:11, color:T.warning }}>{t.stage}{t.seconds ? `（${t.seconds}s）` : ''}</div>
                        )}
                        {t.status==='done' && t.summary && (
                          <div style={{ marginTop:4, fontSize:11, color:T.success }}>→ {t.summary}</div>
                        )}
                      </div>
                    ))}
                  </div>
                )}
                {/* 流式回答气泡（打字机） */}
                {liveAnswer && (
                  <div data-streaming="true" style={{ padding:`${S.md}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.55,
                    whiteSpace:'pre-wrap', wordBreak:'break-word',
                    background:T.surface, color:T.text, border:`1px solid ${T.border}`, borderBottomLeftRadius:2 }}>
                    {liveAnswer}
                    <span className="stream-cursor" />
                  </div>
                )}
              </div>
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          <div style={{ display:'flex', gap:S.sm }}>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key==='Enter'&&handleSend()}
              placeholder="输入消息测试 Agent..."
              style={{ flex:1, minWidth:0, padding:'10px 14px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
            <button onClick={handleSend} disabled={!input.trim()||sending} style={{...btnPrimary, padding:'10px 22px', fontSize:14, opacity:!input.trim()||sending?0.4:1 }}>发送</button>
          </div>
        </div>
      </div>

      {/* Right: Trace Panel */}
      <div style={{
        width: rightOpen ? 360 : 0, minWidth: rightOpen ? 360 : 0,
        background:T.surface, borderLeft: rightOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease', overflow:'hidden',
      }}>
        <div style={{ padding:`${S.md}px ${S.xl}px`, borderBottom:`1px solid ${T.border}`, minWidth:0 }}>
          <span style={{ fontSize:13, fontWeight:600, color:T.text }}>调用链</span>
          <span style={{ fontSize:11, color:T.tertiary, marginLeft:S.sm }}>Tool Calling Trace</span>
        </div>
        <div style={{ flex:1, minHeight:0, overflow:'auto', padding:S.xl, minWidth:0 }}>
          {!trace || !trace.steps || trace.steps.length===0 ? (
            <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge, fontSize:13 }}>暂无调用记录</div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:S.sm }}>
              {trace.steps.map((step, i) => {
                const expanded = expandedSteps[i] ?? (i === trace.steps!.length - 1);
                return (
                  <div key={i} style={{
                    padding:S.md, borderRadius:8, border:`1px solid ${T.border}`,
                    background: step.type==='tool' ? '#FFF7E6' : T.accentBg,
                  }}>
                    <div style={{ display:'flex', alignItems:'center', gap:S.sm, cursor:'pointer' }}
                      onClick={() => setExpandedSteps(p => ({...p, [i]: !expanded}))}>
                      <span style={{
                        display:'inline-flex', alignItems:'center', gap:3,
                        padding:'1px 7px', borderRadius:4, fontSize:10, fontWeight:600,
                        background: step.type==='tool' ? '#F59E0B18' : `${T.accent}18`,
                        color: step.type==='tool' ? '#F59E0B' : T.accent,
                      }}>
                        {step.type==='tool' ? <Wrench size={10} /> : <Brain size={10} />}
                        {step.type==='tool' ? `调用: ${step.tool}` : 'LLM 决策'}
                      </span>
                      <span style={{ fontSize:10, color:T.tertiary, marginLeft:'auto' }}>Step {step.step+1} · {step.latency_ms}ms</span>
                      {expanded ? <ChevronDown size={12} color={T.tertiary} /> : <ChevronRight size={12} color={T.tertiary} />}
                    </div>
                    {expanded && (
                      <div style={{ marginTop:S.sm, fontSize:12 }}>
                        {step.type==='tool' ? (
                          <>
                            <div style={{ marginBottom:S.xs }}>
                              <span style={{ fontWeight:600, color:T.secondary }}>参数</span>
                              <pre style={preSmall}>{JSON.stringify(step.args || {}, null, 2)}</pre>
                            </div>
                            <div>
                              <span style={{ fontWeight:600, color:T.secondary }}>返回</span>
                              <pre style={preSmall}>{JSON.stringify(step.output, null, 2)}</pre>
                            </div>
                          </>
                        ) : (
                          <div style={{ color:T.text, whiteSpace:'pre-wrap', lineHeight:1.5 }}>
                            {step.content || '(无文本输出 — LLM 决定调用工具)'}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                );
              })}
              <div style={{ fontSize:11, color:T.tertiary, textAlign:'center', padding:S.sm }}>
                总耗时 {trace.total_ms}ms · {trace.steps.length} 步
              </div>
            </div>
          )}
        </div>
      </div>

    </div>
  );
}
