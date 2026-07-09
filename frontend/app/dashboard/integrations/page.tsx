'use client';

import { useEffect, useState } from 'react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface PlatformCard { key:string; name:string; logo:string; connected:boolean; }
const PLATFORMS: PlatformCard[] = [{ key:'xianyu', name:'闲鱼', logo:'/xianyu-logo.ico', connected:false }];

export default function IntegrationsPage() {
  const [platforms, setPlatforms] = useState(PLATFORMS);
  const [activeManage, setActiveManage] = useState<string|null>(null);
  const [cookie, setCookie] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  useEffect(()=>{
    fetch(`${API}/api/xianyu/status`).then(r=>r.json()).then(d=>{ setPlatforms(p=>p.map(x=>x.key==='xianyu'?{...x,connected:d.is_online}:x)); }).catch(()=>{});
    fetch(`${API}/api/xianyu/cookie`).then(r=>r.json()).then(d=>{ if(d.cookie) setCookie(d.cookie); }).catch(()=>{});
  },[]);

  function openManage(key:string){ setActiveManage(key); setError(''); }
  async function handleSaveCookie(){
    setSaving(true); setError('');
    try {
      const res = await fetch(`${API}/api/xianyu/connect`,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({cookie})});
      const data = await res.json();
      if(res.ok){ setPlatforms(p=>p.map(x=>x.key===activeManage?{...x,connected:true}:x)); setActiveManage(null); }
      else setError(data.detail||'连接失败');
    } catch { setError('网络错误'); } finally { setSaving(false); }
  }
  async function handleDisconnect(){
    await fetch(`${API}/api/xianyu/disconnect`,{method:'POST'});
    setPlatforms(p=>p.map(x=>x.key===activeManage?{...x,connected:false}:x));
    setActiveManage(null);
  }

  return (
    <div>
      <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text, marginBottom:S.xl }}>集成管理</h2>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(4, 1fr)', gap:S.base }}>
        {platforms.map(p=>(
          <div key={p.key} style={{ background:T.surface, borderRadius:10, padding:S.xl, display:'flex', flexDirection:'column', alignItems:'center', border:`1px solid ${T.border}` }}>
            <div style={{ width:64, height:64, borderRadius:14, overflow:'hidden', marginBottom:S.md, display:'flex', alignItems:'center', justifyContent:'center', background:T.bg }}>
              <img src={p.logo} alt={p.name} style={{ width:'100%',height:'100%',objectFit:'contain' }} onError={e=>{(e.target as HTMLImageElement).style.display='none'}} />
            </div>
            <span style={{ fontSize:15, fontWeight:600, color:T.text, marginBottom:S.sm }}>{p.name}</span>
            <div style={{ display:'flex', alignItems:'center', gap:6, marginBottom:S.base }}>
              <span style={{ width:7, height:7, borderRadius:'50%', background:p.connected?T.success:T.tertiary }} />
              <span style={{ fontSize:13, color:p.connected?T.success:T.secondary }}>{p.connected?'已连接':'未连接'}</span>
            </div>
            <button onClick={()=>openManage(p.key)} style={{ padding:`${S.xs}px ${S.base}px`, borderRadius:6, fontSize:13, cursor:'pointer', background:T.surface, border:`1px solid ${T.border}`, color:T.text }}>管理</button>
          </div>
        ))}
      </div>
      {/* Modal */}
      {activeManage && (
        <div onClick={()=>setActiveManage(null)} style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.4)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}>
          <div onClick={e=>e.stopPropagation()} style={{ background:T.surface, borderRadius:12, padding:S.xl, width:480, boxShadow:'0 8px 32px rgba(0,0,0,0.10)' }}>
            <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text, marginBottom:S.lg }}>
              {platforms.find(p=>p.key===activeManage)?.name} — 连接方式
            </h3>
            <div style={{ display:'flex', gap:S.sm, marginBottom:S.lg }}>
              {[{key:'cookie',label:'Cookie',active:true},{key:'qrcode',label:'二维码',disabled:true},{key:'oauth',label:'OAuth',disabled:true}].map(tab=>(
                <button key={tab.key} disabled={(tab as any).disabled} style={{
                  padding:`${S.xs}px ${S.md}px`, borderRadius:6, fontSize:13,
                  cursor:(tab as any).disabled?'not-allowed':'pointer',
                  border:tab.active?`1px solid ${T.accent}`:`1px solid ${T.border}`,
                  background:tab.active?T.accentBg:T.surface,
                  color:tab.active?T.accent:(tab as any).disabled?T.tertiary:T.secondary,
                }}>{tab.label}</button>
              ))}
            </div>
            {error && <p style={{ color:T.danger, fontSize:13, marginBottom:S.md }}>{error}</p>}
            <textarea value={cookie} onChange={e=>{setCookie(e.target.value);setError('');}} placeholder="粘贴闲鱼 Cookie..."
              rows={5} style={{ width:'100%', padding:'10px 14px', background:T.surface, border:`1px solid ${error?T.danger:T.border}`, borderRadius:8, fontSize:13, resize:'vertical', fontFamily:'monospace', color:T.text, outline:'none', boxSizing:'border-box' }} />
            <div style={{ display:'flex', gap:S.md, justifyContent:'flex-end', marginTop:S.base }}>
              {platforms.find(p=>p.key===activeManage)?.connected && (
                <button onClick={handleDisconnect} style={{ padding:`${S.sm}px ${S.lg}px`, borderRadius:6, fontSize:13, cursor:'pointer', background:T.surface, border:`1px solid ${T.danger}`, color:T.danger }}>断开</button>
              )}
              <button onClick={()=>setActiveManage(null)} style={{ padding:`${S.sm}px ${S.lg}px`, borderRadius:6, fontSize:13, cursor:'pointer', background:T.surface, border:`1px solid ${T.border}`, color:T.text }}>取消</button>
              <button onClick={handleSaveCookie} disabled={saving||!cookie.trim()} style={{
                padding:`${S.sm}px ${S.lg}px`, borderRadius:6, fontSize:13, cursor:'pointer', fontWeight:500,
                background:cookie.trim()?T.accent:'#C9CDD4', color:'#fff', border:'none',
              }}>{saving?'保存中...':'保存'}</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
