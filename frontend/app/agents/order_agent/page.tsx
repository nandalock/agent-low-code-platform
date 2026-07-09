'use client';

import { useState, useRef, useEffect } from 'react';
import { T, S, inputField, labelField, btnPrimary } from '@/app/theme';
import { PanelLeftClose, PanelLeftOpen } from 'lucide-react';
import McpToolBinding from '../_components/McpToolBinding';

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

function getVisitorId(): string {
  if (typeof window === 'undefined') return '';
  const key = 'order_visitor_id';
  let id = localStorage.getItem(key);
  if (!id) { id = crypto.randomUUID(); localStorage.setItem(key, id); }
  return id;
}
const VISITOR_ID = getVisitorId();

function getSavedConversationId(): number | null {
  if (typeof window === 'undefined') return null;
  const saved = sessionStorage.getItem('order_conversation_id');
  return saved ? parseInt(saved) : null;
}

interface ChatMsg {
  role: 'user' | 'agent'; content: string; time: string;
  trace?: { tier: string; llm_called: boolean; llm_model: string; llm_ms: number; total_ms: number };
}

const DEFAULT_CONFIG = { system_prompt: '', fallback_reply: '', api_key: '', base_url: '', model: '' };

export default function OrderAgentPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [trace, setTrace] = useState<ChatMsg['trace'] | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(getSavedConversationId);
  const [loadingHistory, setLoadingHistory] = useState(false);

  useEffect(() => {
    if (sessionStorage.getItem('order_conversation_id')) setLoadingHistory(true);
  }, []);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [leftOpen, setLeftOpen] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/agents/order_agent/config`).then(r => r.json()).then(d => {
      if (d.config) {
        setConfig({
          system_prompt: d.config.system_prompt ?? '',
          fallback_reply: d.config.fallback_reply ?? '',
          api_key: d.config.api_key ?? '',
          base_url: d.config.base_url || 'https://api.deepseek.com',
          model: d.config.model || 'deepseek-chat',
        });
      }
    }).catch(() => {});
  }, []);

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
            if (m.role === 'agent') {
              msgs.push({ role:'agent', content:m.content, time, trace: meta.trace || undefined });
            } else if (m.role === 'customer') {
              msgs.push({ role:'user', content:m.content, time });
            }
          }
          setMessages(msgs);
        }
      }).catch(()=>{}).finally(() => setLoadingHistory(false));
  }, []); // eslint-disable-line

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);

  async function handleSaveConfig() {
    setSaving(true); setSaveMsg('');
    try {
      const r = await fetch(`${API}/api/agents/order_agent/config`, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config) });
      setSaveMsg(r.ok?'保存成功':'保存失败');
    } catch { setSaveMsg('保存失败'); }
    finally { setSaving(false); }
  }

  function handleNewChat() { setMessages([]); setConversationId(null); setTrace(null); sessionStorage.removeItem('order_conversation_id'); }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
    setMessages(prev => [...prev, { role:'user', content:input, time:now }]);
    setInput(''); setSending(true);
    try {
      const body: any = { question: input, visitor_id: VISITOR_ID };
      if (conversationId) body.conversation_id = conversationId;
      const r = await fetch(`${API}/api/agents/order_agent/chat`, { method:'POST', headers:{'Content-Type':'application/json','X-Tenant-ID':String(TENANT_ID)}, body:JSON.stringify(body) });
      const data = await r.json();
      if (data.conversation_id && !conversationId) { setConversationId(data.conversation_id); sessionStorage.setItem('order_conversation_id', String(data.conversation_id)); }
      const tr = data.trace ? { ...data.trace, tier: data.tier } : undefined;
      setMessages(prev => [...prev, { role:'agent', content:data.answer||'抱歉，暂时无法处理。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), trace: tr }]);
      setTrace(tr || null);
    } catch {
      setMessages(prev => [...prev, { role:'agent', content:'请求失败，请稍后重试。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}) }]);
    } finally { setSending(false); }
  }

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
          <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>订单智能体</h3>
          <p style={{ margin:0, marginTop:S.xs, fontSize:13, color:T.secondary }}>订单查询、物流跟踪、催单处理</p>
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
            <McpToolBinding agentKey="order_agent" />
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
          <button onClick={handleNewChat} style={{ padding:'5px 14px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, fontSize:12, cursor:'pointer' }}>新建会话</button>
        </div>

        <div style={{ flex:1, padding:S.xl, overflow:'auto' }}>
          {loadingHistory && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>加载历史消息...</div>}
          {!loadingHistory && messages.length===0 && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>输入客户订单问题，测试订单智能体</div>}
          {messages.map((msg, i) => {
            const isUser = msg.role === 'user';
            return (
              <div key={i} style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:isUser?'flex-end':'flex-start' }}>
                <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{isUser?'测试用户':'OrderAgent'} · {msg.time}</div>
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
              placeholder="输入客户订单问题..."
              style={{ flex:1, padding:'10px 14px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
            <button onClick={handleSend} disabled={!input.trim()||sending} style={{...btnPrimary, padding:'10px 22px', fontSize:14, opacity:!input.trim()||sending?0.4:1 }}>发送</button>
          </div>
        </div>
      </div>
    </div>
  );
}
