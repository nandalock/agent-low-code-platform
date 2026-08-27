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

interface TraceStep {
  step: number; type: 'llm' | 'tool'; content?: string;
  tool?: string; args?: Record<string,any>; output?: any;
  latency_ms?: number;
}
interface ChatMsg {
  role: 'user' | 'agent'; content: string; time: string;
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
  const [loadingHistory, setLoadingHistory] = useState(false);

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
            if (m.role === 'agent') msgs.push({ role:'agent', content:m.content, time, trace: meta.trace || undefined });
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

  function handleNewChat() { setMessages([]); setConversationId(null); setTrace(null); sessionStorage.removeItem(`conv_${agentKey}`); }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
    setMessages(prev => [...prev, { role:'user', content:input, time:now }]);
    setInput(''); setSending(true);
    try {
      const body: any = { question: input, visitor_id: VISITOR_ID };
      if (conversationId) body.conversation_id = conversationId;
      const r = await fetch(`${API}/api/agents/${agentKey}/chat`, { method:'POST', headers:{'Content-Type':'application/json','X-Tenant-ID':String(TENANT_ID)}, body:JSON.stringify(body) });
      const data = await r.json();
      if (data.conversation_id && !conversationId) { setConversationId(data.conversation_id); sessionStorage.setItem(`conv_${agentKey}`, String(data.conversation_id)); }
      const tr = data.trace ? { tier: data.tier, total_ms: data.trace.total_ms, steps: data.trace.steps } : undefined;
      setMessages(prev => [...prev, { role:'agent', content:data.answer||'抱歉，暂时无法处理。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), trace: tr }]);
      setTrace(tr || null);
    } catch {
      setMessages(prev => [...prev, { role:'agent', content:'请求失败，请稍后重试。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}) }]);
    } finally { setSending(false); }
  }

  const preSmall: React.CSSProperties = { margin:0, padding:'6px 8px', background:T.bg, borderRadius:4, fontSize:11, fontFamily:'monospace', color:T.text, lineHeight:1.5, overflow:'auto', maxHeight:200, whiteSpace:'pre-wrap', wordBreak:'break-all' };

  const tierLabel = (tier: string) => tier==='llm'?'AI 回答':tier==='fallback'?'兜底回复':tier;
  const tierColor = (tier: string) => tier==='llm'?T.accent:tier==='fallback'?T.warning:T.success;
  const tierBg = (tier: string) => tier==='llm'?T.accentBg:'#FFF7E6';

  return (
    <div style={{ display:'flex', height:'100vh', background:T.bg, color:T.text, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif" }}>
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
      <div style={{ flex:1, display:'flex', flexDirection:'column', background:T.bg }}>
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

        <div style={{ flex:1, padding:S.xl, overflow:'auto' }}>
          {loadingHistory && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>加载历史消息...</div>}
          {!loadingHistory && messages.length===0 && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>输入消息，测试智能体</div>}
          {messages.map((msg, i) => {
            const isUser = msg.role === 'user';
            return (
              <div key={i} style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:isUser?'flex-end':'flex-start' }}>
                <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{isUser?'测试用户':(agentName||agentKey)} · {msg.time}</div>
                <div style={{ maxWidth:'72%' }}>
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
          {sending && <div style={{ color:T.secondary, fontSize:13 }}>Agent 回复中...</div>}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          <div style={{ display:'flex', gap:S.sm }}>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key==='Enter'&&handleSend()}
              placeholder="输入消息测试 Agent..."
              style={{ flex:1, padding:'10px 14px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
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
        <div style={{ padding:`${S.md}px ${S.xl}px`, borderBottom:`1px solid ${T.border}`, minWidth:340 }}>
          <span style={{ fontSize:13, fontWeight:600, color:T.text }}>调用链</span>
          <span style={{ fontSize:11, color:T.tertiary, marginLeft:S.sm }}>Tool Calling Trace</span>
        </div>
        <div style={{ flex:1, overflow:'auto', padding:S.xl, minWidth:340 }}>
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
