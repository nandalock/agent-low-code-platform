'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { T, S, btnPrimary } from '@/app/theme';
import { X } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface AgentInfo { key:string; name:string; desc:string; status:string; agent_type?:string; }
const ICONS: Record<string,string> = { faqagent:'📋', router:'🧠', supervisor:'🧭', default:'🤖' };
const TYPE_BADGE: Record<string,{label:string;bg:string;color:string}> = {
  router: {label:'路由', bg:'#F3E8FF', color:'#9333EA'},
  agent:  {label:'Agent', bg:'#E8F0FF', color:'#3370FF'},
};

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ key:'', name:'', desc:'', system_prompt:'', agent_type:'agent' as 'agent'|'router' });
  const [error, setError] = useState('');

  function loadAgents() {
    fetch(`${API}/api/agents`).then(r=>r.json()).then(d=>setAgents(d)).finally(()=>setLoading(false));
  }
  useEffect(()=>{ loadAgents(); },[]);

  async function handleCreate() {
    setError('');
    if (!form.key.trim() || !form.name.trim()) { setError('标识和名称不能为空'); return; }
    setCreating(true);
    try {
      const body: any = {
        key: form.key.trim().toLowerCase().replace(/\s+/g, '_'),
        name: form.name.trim(),
        desc: form.desc.trim(),
        agent_type: form.agent_type,
      };
      if (form.agent_type === 'agent') {
        body.system_prompt = form.system_prompt.trim();
      }
      const r = await fetch(`${API}/api/agents`, {
        method: 'POST', headers: {'Content-Type':'application/json'},
        body: JSON.stringify(body),
      });
      if (r.ok) {
        const data = await r.json();
        setShowModal(false);
        setForm({ key:'', name:'', desc:'', system_prompt:'', agent_type:'agent' });
        const target = form.agent_type === 'router'
          ? `/agents/router/${data.agent.key}`
          : `/agents/${data.agent.key}`;
        router.push(target);
      } else {
        const err = await r.json();
        setError(err.detail || '创建失败');
      }
    } catch { setError('请求失败'); }
    finally { setCreating(false); }
  }

  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:S.xl }}>
        <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>Agent 管理</h2>
        <button onClick={()=>{setError('');setShowModal(true);}} style={btnPrimary}>+ 新建 Agent</button>
      </div>
      {loading && <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge }}>加载中...</div>}
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(300px, 1fr))', gap:S.base }}>
        {agents.map(agent=>{
          const isRouter = agent.agent_type === 'router';
          const badge = TYPE_BADGE[agent.agent_type || 'agent'] || TYPE_BADGE.agent;
          const route = isRouter ? `/agents/router/${agent.key}` : `/agents/${agent.key}`;
          const icon = ICONS[agent.agent_type || agent.key] || ICONS[agent.key] || ICONS.default;
          return (
          <div key={agent.key} onClick={()=>router.push(route)} style={{
            background:T.surface, borderRadius:10, padding:S.xl, cursor:'pointer', border:`1px solid ${T.border}`,
            transition:'box-shadow .15s',
          }} onMouseEnter={e=>e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'} onMouseLeave={e=>e.currentTarget.style.boxShadow='none'}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:S.md }}>
              <div style={{ display:'flex', alignItems:'center', gap:S.md }}>
                <div style={{ width:36, height:36, borderRadius:8, background:isRouter?'#F3E8FF':T.accentBg, display:'flex', alignItems:'center', justifyContent:'center', fontSize:18 }}>{icon}</div>
                <div>
                  <span style={{ fontSize:15, fontWeight:600, color:T.text }}>{agent.name}</span>
                  <span style={{ fontSize:10, padding:'1px 6px', borderRadius:8, marginLeft:6, background:badge.bg, color:badge.color, fontWeight:500 }}>{badge.label}</span>
                </div>
              </div>
              <span style={{ fontSize:11, padding:'2px 8px', borderRadius:12, fontWeight:500, background:agent.status==='active'?'#E8F8EE':'T.bg', color:agent.status==='active'?T.success:T.secondary }}>
                {agent.status==='active'?'运行中':'草稿'}
              </span>
            </div>
            <p style={{ fontSize:13, color:T.secondary, margin:0, lineHeight:1.5 }}>{agent.desc}</p>
          </div>
        )})}
      </div>

      {/* ── Create Modal ── */}
      {showModal && (
        <div onClick={()=>setShowModal(false)} style={{
          position:'fixed', inset:0, background:'rgba(0,0,0,.45)',
          display:'flex', alignItems:'center', justifyContent:'center', zIndex:100,
        }}>
          <div onClick={e=>e.stopPropagation()} style={{
            width:460, background:T.surface, borderRadius:12, padding:S.xl,
            border:`1px solid ${T.border}`, boxShadow:'0 16px 48px rgba(0,0,0,.25)',
          }}>
            <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:S.lg }}>
              <span style={{ fontSize:16, fontWeight:600, color:T.text }}>新建 Agent</span>
              <button onClick={()=>setShowModal(false)} style={{
                width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.bg,
                display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
              }}><X size={14} /></button>
            </div>

            <div style={{ display:'flex', flexDirection:'column', gap:S.md }}>
              <div>
                <label style={labelStyle}>智能体类型</label>
                <div style={{ display:'flex', gap:S.sm, marginTop:2 }}>
                  {(['agent','router'] as const).map(t => {
                    const isSel = form.agent_type === t;
                    return (
                      <button key={t} onClick={()=>setForm(f=>({...f, agent_type:t}))} style={{
                        flex:1, padding:'8px 12px', borderRadius:6, cursor:'pointer',
                        background:isSel ? T.accentBg : T.bg,
                        border:isSel ? `2px solid ${T.accent}` : `1px solid ${T.border}`,
                        color:isSel ? T.accent : T.secondary,
                        fontSize:13, fontWeight:isSel?600:400, fontFamily:'inherit',
                      }}>
                        {t==='agent'?'🤖 普通 Agent':'🧠 路由 Agent'}
                      </button>
                    );
                  })}
                </div>
                <div style={{ fontSize:11, color:T.tertiary, marginTop:4 }}>
                  {form.agent_type==='router'
                    ? 'Router Agent 用 L1/L2/L3 三级路由做意图分类，返回目标 Agent key'
                    : '普通 Agent 调用 LLM + MCP 工具，返回自然语言回答'}
                </div>
              </div>
              <div>
                <label style={labelStyle}>标识 (英文)</label>
                <input value={form.key} onChange={e=>setForm(f=>({...f, key:e.target.value}))}
                  placeholder="my_agent" style={inputStyle} />
              </div>
              <div>
                <label style={labelStyle}>名称</label>
                <input value={form.name} onChange={e=>setForm(f=>({...f, name:e.target.value}))}
                  placeholder="我的智能体" style={inputStyle} />
              </div>
              <div>
                <label style={labelStyle}>描述</label>
                <input value={form.desc} onChange={e=>setForm(f=>({...f, desc:e.target.value}))}
                  placeholder="用一句话描述这个智能体的用途" style={inputStyle} />
              </div>
              {form.agent_type === 'agent' && (
                <div>
                  <label style={labelStyle}>系统提示词</label>
                  <textarea rows={4} value={form.system_prompt} onChange={e=>setForm(f=>({...f, system_prompt:e.target.value}))}
                    placeholder="你是一个..." style={{...inputStyle, resize:'vertical', minHeight:80}} />
                </div>
              )}

              {error && <div style={{ fontSize:12, color:T.danger }}>{error}</div>}

              <div style={{ display:'flex', gap:S.sm, justifyContent:'flex-end', marginTop:S.sm }}>
                <button onClick={()=>setShowModal(false)} style={{
                  padding:'8px 20px', borderRadius:6, border:`1px solid ${T.border}`, background:T.bg,
                  color:T.text, fontSize:13, cursor:'pointer',
                }}>取消</button>
                <button onClick={handleCreate} disabled={creating} style={{
                  ...btnPrimary, padding:'8px 20px', fontSize:13, opacity:creating?0.5:1,
                }}>{creating?'创建中...':'创建'}</button>
              </div>
            </div>
          </div>
        </div>
      )}

      {/* hidden button style only — for future styling */}
      <style jsx global>{''}</style>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width:'100%', padding:'8px 10px',
  background:T.bg, border:`1px solid ${T.border}`, borderRadius:6,
  fontSize:13, color:T.text, outline:'none', fontFamily:'inherit',
  boxSizing:'border-box',
};

const labelStyle: React.CSSProperties = {
  fontSize:12, fontWeight:500, color:T.text, display:'block', marginBottom:4,
};
