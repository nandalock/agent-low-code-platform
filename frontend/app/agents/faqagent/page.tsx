'use client';

import { useState, useRef, useEffect } from 'react';
import { T, S, inputField, labelField, btnPrimary } from '@/app/theme';
import { Settings, PanelLeftClose, PanelRightClose, PanelLeftOpen, PanelRightOpen, ChevronDown, ChevronRight } from 'lucide-react';
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
  const key = 'faq_visitor_id';
  let id = localStorage.getItem(key);
  if (!id) { id = crypto.randomUUID(); localStorage.setItem(key, id); }
  return id;
}
const VISITOR_ID = getVisitorId();

function getSavedConversationId(): number | null {
  if (typeof window === 'undefined') return null;
  const saved = sessionStorage.getItem('faq_conversation_id');
  return saved ? parseInt(saved) : null;
}

type TraceChunk = { question: string; answer: string; score: number };

interface ChatMsg {
  role: 'user' | 'agent'; content: string; time: string;
  faq?: { question: string; answer: string; tags: string[] };
  trace?: {
    tier: string; pg_trgm_score: number; vector_top_n: number; rerank_top_k: number;
    rerank_enabled: boolean; candidates_count: number; llm_called: boolean;
    llm_model: string; llm_ms: number; total_ms: number;
    rough_chunks: TraceChunk[]; fine_chunks: TraceChunk[];
  };
}

const DEFAULT_LLM = { system_prompt: '', fallback_reply: '', api_key: '', base_url: '', model: '' };
const DEFAULT_FAQ = { direct_threshold: 0.85, vector_top_n: 10, rerank_top_k: 3, rerank_enabled: true, system_prompt: '', fallback_reply: '' };

export default function FaqAgentPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [selectedTrace, setSelectedTrace] = useState<ChatMsg['trace'] | null>(null);
  const [conversationId, setConversationId] = useState<number | null>(getSavedConversationId);
  const [loadingHistory, setLoadingHistory] = useState(false);
  useEffect(() => {
    if (sessionStorage.getItem('faq_conversation_id')) setLoadingHistory(true);
  }, []);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [faqConfig, setFaqConfig] = useState(DEFAULT_FAQ);
  const [llmConfig, setLlmConfig] = useState(DEFAULT_LLM);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');
  const [llmOpen, setLlmOpen] = useState(false);

  // Panel visibility
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  // Collapsible sections in trace
  const [roughOpen, setRoughOpen] = useState(true);
  const [fineOpen, setFineOpen] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/agents/faqagent/config`).then(r => r.json()).then(d => {
      if (d.config) {
        setFaqConfig({ direct_threshold: d.config.direct_threshold ?? DEFAULT_FAQ.direct_threshold, vector_top_n: d.config.vector_top_n ?? DEFAULT_FAQ.vector_top_n, rerank_top_k: d.config.rerank_top_k ?? DEFAULT_FAQ.rerank_top_k, rerank_enabled: d.config.rerank_enabled ?? DEFAULT_FAQ.rerank_enabled, system_prompt: d.config.system_prompt ?? '', fallback_reply: d.config.fallback_reply ?? '' });
        setLlmConfig({ system_prompt: d.config.system_prompt ?? '', fallback_reply: d.config.fallback_reply ?? '', api_key: d.config.api_key ?? '', base_url: d.config.base_url || 'https://api.deepseek.com', model: d.config.model || 'deepseek-chat' });
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
              const trace = meta.trace ? { ...meta.trace, tier: meta.tier } : undefined;
              msgs.push({ role:'agent', content:m.content, time, faq:meta.sources?.[0]||undefined, trace: trace || undefined });
            } else if (m.role === 'customer') {
              msgs.push({ role:'user', content:m.content, time });
            }
          }
          setMessages(msgs);
          const lastAgent = [...msgs].reverse().find(m => m.role==='agent');
          if (lastAgent?.trace) setSelectedTrace(lastAgent.trace);
        }
      }).catch(()=>{}).finally(() => setLoadingHistory(false));
  }, []); // eslint-disable-line

  useEffect(() => { bottomRef.current?.scrollIntoView({ behavior:'smooth' }); }, [messages]);

  async function handleSaveConfig() {
    setSaving(true); setSaveMsg('');
    const config = { ...faqConfig, ...llmConfig };
    try {
      const r = await fetch(`${API}/api/agents/faqagent/config`, { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(config) });
      setSaveMsg(r.ok?'保存成功':'保存失败');
    } catch { setSaveMsg('保存失败'); }
    finally { setSaving(false); }
  }

  function handleNewChat() { setMessages([]); setConversationId(null); setSelectedTrace(null); sessionStorage.removeItem('faq_conversation_id'); }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour:'2-digit', minute:'2-digit' });
    setMessages(prev => [...prev, { role:'user', content:input, time:now }]);
    setInput(''); setSending(true);
    try {
      const body: any = { question: input, visitor_id: VISITOR_ID };
      if (conversationId) body.conversation_id = conversationId;
      const r = await fetch(`${API}/api/agents/faqagent/chat`, { method:'POST', headers:{'Content-Type':'application/json','X-Tenant-ID':String(TENANT_ID)}, body:JSON.stringify(body) });
      const data = await r.json();
      if (data.conversation_id && !conversationId) { setConversationId(data.conversation_id); sessionStorage.setItem('faq_conversation_id', String(data.conversation_id)); }
      const trace = data.trace ? { ...data.trace, tier: data.tier } : undefined;
      setMessages(prev => [...prev, { role:'agent', content:data.answer||'抱歉，暂时无法回答这个问题。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}), faq:data.sources?.[0]||undefined, trace }]);
      setSelectedTrace(trace || null);
    } catch {
      setMessages(prev => [...prev, { role:'agent', content:'请求失败，请稍后重试。', time:new Date().toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}) }]);
    } finally { setSending(false); }
  }

  const tierLabel = (tier: string) => tier==='rag'?'AI 回答':tier==='direct'?'直接匹配':'兜底回复';
  const tierColor = (tier: string) => tier==='rag'?T.accent:tier==='direct'?T.success:T.warning;
  const tierBg = (tier: string) => tier==='rag'?T.accentBg:tier==='direct'?'#E8F8EE':'#FFF7E6';

  const renderChunks = (chunks: TraceChunk[], prefix: string) => (
    <div style={{ display:'flex', flexDirection:'column', gap:S.xs }}>
      {chunks.map((chunk, i) => (
        <div key={i} style={{ padding:S.md, background:T.bg, borderRadius:6, fontSize:12, border:`1px solid ${T.border}` }}>
          <div style={{ display:'flex', justifyContent:'space-between', marginBottom:6 }}>
            <span style={{ fontWeight:600, color:T.text }}>{prefix}{i+1}</span>
            <span style={{ fontWeight:600, color:T.accent }}>{(chunk.score*100).toFixed(1)}%</span>
          </div>
          <div style={{ color:T.text, marginBottom:4, lineHeight:1.4 }}>{chunk.question}</div>
          <div style={{ color:T.secondary, fontSize:11, lineHeight:1.4 }}>{chunk.answer}</div>
        </div>
      ))}
    </div>
  );

  const CollapseHeader = ({ open, setOpen, label, count }: { open:boolean; setOpen:(v:boolean)=>void; label:string; count:number }) => (
    <div onClick={() => setOpen(!open)} style={{ display:'flex', alignItems:'center', gap:S.xs, cursor:'pointer', padding:`${S.xs}px 0`, userSelect:'none' }}>
      {open ? <ChevronDown size={14} color={T.secondary} /> : <ChevronRight size={14} color={T.secondary} />}
      <span style={{ fontSize:12, fontWeight:500, color:T.text }}>{label}</span>
      <span style={{ fontSize:11, color:T.tertiary }}>({count})</span>
    </div>
  );

  return (
    <div style={{ display:'flex', height:'100vh', background:T.bg, color:T.text, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif" }}>
      {/* ═══ Left config panel ═══ */}
      <div style={{
        width: leftOpen ? 300 : 0, minWidth: leftOpen ? 300 : 0,
        background:T.surface, borderRight: leftOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease',
        overflow:'hidden',
      }}>
        <div style={{ padding:`${S.lg}px ${S.xl}px ${S.base}px`, display:'flex', alignItems:'flex-start', justifyContent:'space-between' }}>
          <div>
            <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>FAQ 智能体</h3>
            <p style={{ margin:0, marginTop:S.xs, fontSize:13, color:T.secondary }}>检索策略配置</p>
          </div>
          <button onClick={() => setLlmOpen(true)} title="LLM 配置" style={{
            width:32, height:32, borderRadius:6, border:`1px solid ${T.border}`, background:T.bg,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
          }}><Settings size={16} /></button>
        </div>
        <div style={{ flex:1, padding:`0 ${S.xl}px`, display:'flex', flexDirection:'column', gap:S.md, overflow:'auto', paddingBottom:S.xl }}>
          {/* Pipeline */}
          <div style={{ background:T.accentBg, borderRadius:8, padding:`${S.base}px`, border:`1px solid rgba(51,112,255,0.12)` }}>
            <div style={{ fontWeight:600, color:T.accent, marginBottom:S.sm, fontSize:12, letterSpacing:'0.02em' }}>匹配流程</div>
            {[
              { step:'1', label:'pg_trgm 模糊匹配', sub:'命中 → 直接返回' },
              { step:'2', label:'pgvector 语义搜索', sub:'向量相似度 + reranker 精排' },
              { step:'3', label:'LLM RAG 润色', sub:'知识库喂给大模型生成回答' },
              { step:'4', label:'兜底回复', sub:'全不中 → 返回预设文案' },
            ].map((s,i) => (
              <div key={i} style={{ display:'flex', gap:S.sm, padding:`${S.xs}px 0`, borderBottom:i<3?`1px solid rgba(51,112,255,0.08)`:'none' }}>
                <span style={{ width:20, height:20, borderRadius:'50%', background:T.accent, color:'#fff', fontSize:11, fontWeight:700, display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0 }}>{s.step}</span>
                <div>
                  <div style={{ fontSize:13, color:T.text, fontWeight:500 }}>{s.label}</div>
                  <div style={{ fontSize:11, color:T.secondary }}>{s.sub}</div>
                </div>
              </div>
            ))}
          </div>

          <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase' }}>搜索参数</div>
          <label style={labelField}>直接回答阈值
            <input type="number" step="0.01" min="0" max="1" value={faqConfig.direct_threshold}
              onChange={e => setFaqConfig(p=>({...p, direct_threshold:parseFloat(e.target.value)||0}))} style={inputField} />
          </label>
          <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:S.sm }}>
            <label style={labelField}>top-N
              <input type="number" min="1" max="50" value={faqConfig.vector_top_n}
                onChange={e => setFaqConfig(p=>({...p, vector_top_n:parseInt(e.target.value)||10}))} style={inputField} />
            </label>
            <label style={labelField}>top-K
              <input type="number" min="1" max="10" value={faqConfig.rerank_top_k}
                onChange={e => setFaqConfig(p=>({...p, rerank_top_k:parseInt(e.target.value)||3}))} style={inputField} />
            </label>
          </div>
          <label style={{ ...labelField, flexDirection:'row', alignItems:'center', gap:S.sm, cursor:'pointer' }}>
            <input type="checkbox" checked={faqConfig.rerank_enabled} onChange={e => setFaqConfig(p=>({...p, rerank_enabled:e.target.checked}))}
              style={{ width:16, height:16, cursor:'pointer', accentColor:T.accent }} />
            启用 Reranker 精排
          </label>

          <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginTop:S.sm }}>回复设置</div>
          <label style={labelField}>系统提示词
            <textarea rows={4} value={faqConfig.system_prompt} onChange={e => { setFaqConfig(p=>({...p, system_prompt:e.target.value})); setLlmConfig(p=>({...p, system_prompt:e.target.value})); }}
              style={{ ...inputField, resize:'vertical', minHeight:60 }} />
          </label>
          <label style={labelField}>兜底回复
            <textarea rows={4} value={faqConfig.fallback_reply} onChange={e => { setFaqConfig(p=>({...p, fallback_reply:e.target.value})); setLlmConfig(p=>({...p, fallback_reply:e.target.value})); }}
              style={{ ...inputField, resize:'vertical', minHeight:50 }} />
          </label>
        </div>

        <div style={{ padding:`${S.xs}px ${S.xl}px` }}>
          <McpToolBinding agentKey="faqagent" />
        </div>

        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          {saveMsg && <div style={{ fontSize:12, marginBottom:S.sm, color:saveMsg==='保存成功'?T.success:T.danger }}>{saveMsg}</div>}
          <button onClick={handleSaveConfig} disabled={saving} style={{...btnPrimary, width:'100%', padding:'10px 0', fontSize:14, opacity:saving?0.5:1 }}>{saving?'保存中...':'保存配置'}</button>
        </div>
      </div>

      {/* ═══ Center: chat + toggle buttons ═══ */}
      <div style={{ flex:1, display:'flex', flexDirection:'column', background:T.bg }}>
        {/* Header */}
        <div style={{ padding:`${S.md}px ${S.xl}px`, display:'flex', alignItems:'center', gap:S.sm, borderBottom:`1px solid ${T.border}` }}>
          {/* Two toggle buttons — stacked vertically on the left edge */}
          <div style={{ display:'flex', gap:4, marginRight:S.sm }}>
            <button onClick={() => setLeftOpen(!leftOpen)} title={leftOpen?'收起配置':'展开配置'} style={{
              width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
              display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
            }}>
              {leftOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}
            </button>
            <button onClick={() => setRightOpen(!rightOpen)} title={rightOpen?'收起检索':'展开检索'} style={{
              width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
              display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
            }}>
              {rightOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}
            </button>
          </div>
          <span style={{ width:7, height:7, borderRadius:'50%', background:T.accent }} />
          <span style={{ fontSize:14, fontWeight:500, color:T.text }}>测试对话</span>
          <div style={{ flex:1 }} />
          <button onClick={handleNewChat} style={{ padding:'5px 14px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, fontSize:12, cursor:'pointer' }}>新建会话</button>
        </div>

        {/* Messages */}
        <div style={{ flex:1, padding:S.xl, overflow:'auto' }}>
          {loadingHistory && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>加载历史消息...</div>}
          {!loadingHistory && messages.length===0 && <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>输入客户问题，测试 FAQ 智能体的匹配效果</div>}
          {messages.map((msg, i) => {
            const isUser = msg.role === 'user';
            return (
              <div key={i} style={{ marginBottom:S.lg, display:'flex', flexDirection:'column', alignItems:isUser?'flex-end':'flex-start' }}>
                <div style={{ fontSize:12, color:T.secondary, marginBottom:6 }}>{isUser?'测试用户':'FAQ Agent'} · {msg.time}</div>
                <div style={{ maxWidth:'72%' }}>
                  <div style={{
                    padding:`${S.md}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.55,
                    whiteSpace:'pre-wrap', wordBreak:'break-word',
                    background:isUser?T.accent:T.surface, color:isUser?'#fff':T.text,
                    border:isUser?'none':`1px solid ${T.border}`, borderBottomRightRadius:isUser?2:8, borderBottomLeftRadius:isUser?8:2,
                  }}>{msg.content}</div>
                  {msg.faq && (
                    <div style={{ marginTop:S.sm, display:'flex', gap:6, flexWrap:'wrap', padding:'0 2px' }}>
                      {msg.faq.tags?.map(t => (
                        <span key={t} style={{ padding:'2px 8px', borderRadius:4, background:T.accentBg, fontSize:11, color:T.accent }}>{t}</span>
                      ))}
                      <span style={{ fontSize:11, color:T.secondary }}>来源: {msg.faq.question}</span>
                    </div>
                  )}
                </div>
              </div>
            );
          })}
          {sending && <div style={{ color:T.secondary, fontSize:13 }}>Agent 回复中...</div>}
          <div ref={bottomRef} />
        </div>

        {/* Input */}
        <div style={{ padding:`${S.base}px ${S.xl}px ${S.lg}px` }}>
          <div style={{ display:'flex', gap:S.sm }}>
            <input value={input} onChange={e => setInput(e.target.value)} onKeyDown={e => e.key==='Enter'&&handleSend()}
              placeholder="输入客户问题..."
              style={{ flex:1, padding:'10px 14px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
            <button onClick={handleSend} disabled={!input.trim()||sending} style={{...btnPrimary, padding:'10px 22px', fontSize:14, opacity:!input.trim()||sending?0.4:1 }}>发送</button>
          </div>
        </div>
      </div>

      {/* ═══ Right: trace panel ═══ */}
      <div style={{
        width: rightOpen ? 320 : 0, minWidth: rightOpen ? 320 : 0,
        background:T.surface, borderLeft: rightOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease',
        overflow:'hidden',
      }}>
        {selectedTrace && (
          <>
            <div style={{ padding:`${S.base}px`, borderBottom:`1px solid ${T.border}`, display:'flex', alignItems:'center', justifyContent:'space-between', minWidth:320 }}>
              <span style={{ fontSize:13, fontWeight:500, color:T.text }}>检索过程</span>
              <span style={{ fontSize:11, padding:'3px 10px', borderRadius:12, fontWeight:500, background:tierBg(selectedTrace.tier), color:tierColor(selectedTrace.tier) }}>
                {tierLabel(selectedTrace.tier)}
              </span>
            </div>
            <div style={{ flex:1, padding:S.base, overflow:'auto', display:'flex', flexDirection:'column', gap:S.base, minWidth:320 }}>
              {/* Stats grid */}
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:S.sm }}>
                <div style={{ background:T.bg, borderRadius:6, padding:`${S.sm}px ${S.md}px`, fontSize:12 }}>
                  <div style={{ color:T.secondary }}>总耗时</div>
                  <div style={{ color:T.text, fontWeight:600, fontSize:14 }}>{selectedTrace.total_ms}ms</div>
                </div>
                <div style={{ background:T.bg, borderRadius:6, padding:`${S.sm}px ${S.md}px`, fontSize:12 }}>
                  <div style={{ color:T.secondary }}>候选数</div>
                  <div style={{ color:T.text, fontWeight:600, fontSize:14 }}>{selectedTrace.candidates_count}</div>
                </div>
                <div style={{ background:T.bg, borderRadius:6, padding:`${S.sm}px ${S.md}px`, fontSize:12 }}>
                  <div style={{ color:T.secondary }}>pg_trgm</div>
                  <div style={{ color:T.text, fontWeight:600, fontSize:14 }}>{selectedTrace.pg_trgm_score.toFixed(4)}</div>
                </div>
                <div style={{ background:T.bg, borderRadius:6, padding:`${S.sm}px ${S.md}px`, fontSize:12 }}>
                  <div style={{ color:T.secondary }}>Reranker</div>
                  <div style={{ color:selectedTrace.rerank_enabled?T.success:T.secondary, fontWeight:600, fontSize:14 }}>{selectedTrace.rerank_enabled?'top-'+selectedTrace.rerank_top_k:'未启用'}</div>
                </div>
              </div>

              {/* LLM status */}
              <div style={{ background:T.bg, borderRadius:6, padding:`${S.md}px`, fontSize:12, border:`1px solid ${T.border}` }}>
                <div style={{ fontWeight:500, color:T.text, marginBottom:S.sm }}>LLM</div>
                <div style={{ display:'flex', justifyContent:'space-between', lineHeight:2 }}>
                  <span style={{ color:T.secondary }}>调用</span>
                  <span style={{ color:selectedTrace.llm_called?T.success:T.secondary, fontWeight:500 }}>{selectedTrace.llm_called?'是':'否'}</span>
                </div>
                <div style={{ display:'flex', justifyContent:'space-between', lineHeight:2 }}>
                  <span style={{ color:T.secondary }}>模型 / 耗时</span>
                  <span style={{ color:T.text, fontWeight:500 }}>{selectedTrace.llm_model||'-'} {selectedTrace.llm_ms>0?`· ${selectedTrace.llm_ms}ms`:''}</span>
                </div>
              </div>

              {/* Rough-ranking chunks (collapsible) */}
              {selectedTrace.rough_chunks?.length > 0 && (
                <div>
                  <CollapseHeader open={roughOpen} setOpen={setRoughOpen} label="粗排" count={selectedTrace.rough_chunks.length} />
                  {roughOpen && renderChunks(selectedTrace.rough_chunks, 'R')}
                </div>
              )}

              {/* Fine-ranking chunks (collapsible) — only when reranker enabled */}
              {selectedTrace.rerank_enabled && selectedTrace.fine_chunks?.length > 0 && (
                <div>
                  <CollapseHeader open={fineOpen} setOpen={setFineOpen} label="精排" count={selectedTrace.fine_chunks.length} />
                  {fineOpen && renderChunks(selectedTrace.fine_chunks, 'F')}
                </div>
              )}

              {/* When reranker disabled, fine_chunks holds the display results */}
              {!selectedTrace.rerank_enabled && selectedTrace.fine_chunks?.length > 0 && !selectedTrace.rough_chunks?.length && (
                <div>
                  <CollapseHeader open={fineOpen} setOpen={setFineOpen} label="检索结果" count={selectedTrace.fine_chunks.length} />
                  {fineOpen && renderChunks(selectedTrace.fine_chunks, '')}
                </div>
              )}
            </div>
          </>
        )}
        {!selectedTrace && (
          <div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:T.tertiary, fontSize:13, minWidth:320 }}>暂无检索数据</div>
        )}
      </div>

      {/* ═══ LLM Config Modal ═══ */}
      {llmOpen && (
        <div onClick={() => setLlmOpen(false)} style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.3)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:100 }}>
          <div onClick={e => e.stopPropagation()} style={{ background:T.surface, borderRadius:12, padding:S.xl, width:440, boxShadow:'0 8px 32px rgba(0,0,0,0.12)' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:S.lg }}>
              <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>LLM 配置</h3>
              <button onClick={() => setLlmOpen(false)} style={{ background:'none', border:'none', cursor:'pointer', color:T.secondary, fontSize:20, padding:0, lineHeight:1 }}>×</button>
            </div>
            <div style={{ display:'flex', flexDirection:'column', gap:S.base }}>
              <label style={labelField}>API Key
                <input type="password" value={llmConfig.api_key} onChange={e => setLlmConfig(p=>({...p, api_key:e.target.value.trim()}))}
                  placeholder="sk-xxx" style={{...inputField, marginTop:S.xs, fontFamily:'monospace'}} />
              </label>
              <label style={labelField}>Base URL
                <input type="text" value={llmConfig.base_url} onChange={e => setLlmConfig(p=>({...p, base_url:e.target.value.trim()}))}
                  placeholder="https://api.deepseek.com" style={{...inputField, marginTop:S.xs, fontFamily:'monospace'}} />
              </label>
              <label style={labelField}>Model
                <select value={llmConfig.model} onChange={e => { const m = e.target.value; setLlmConfig(p => ({ ...p, model: m, base_url: (!p.base_url || Object.values(MODEL_BASE_URLS).includes(p.base_url)) ? (MODEL_BASE_URLS[m] || '') : p.base_url })); }}
                  style={{...inputField, marginTop:S.xs, padding:'8px 10px'}}>
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
            </div>
            <div style={{ display:'flex', gap:S.md, justifyContent:'flex-end', marginTop:S.lg }}>
              <button onClick={() => setLlmOpen(false)} style={{ padding:`${S.sm}px ${S.lg}px`, borderRadius:6, fontSize:13, cursor:'pointer', background:T.surface, border:`1px solid ${T.border}`, color:T.text }}>取消</button>
              <button onClick={() => { handleSaveConfig(); setLlmOpen(false); }} disabled={saving} style={{...btnPrimary, opacity:saving?0.5:1 }}>
                {saving?'保存中...':'保存并关闭'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
