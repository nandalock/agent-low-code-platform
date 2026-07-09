'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';
import { T, S } from '@/app/theme';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault(); setError(''); setLoading(true);
    const res = await fetch('/api/login', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify({username,password}) });
    if (res.ok) router.push('/dashboard'); else { const d = await res.json(); setError(d.error); }
    setLoading(false);
  }

  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:T.bg }}>
      <form onSubmit={handleSubmit} style={{ width:380 }}>
        <div style={{ textAlign:'center', marginBottom:S.xxl }}>
          <div style={{ width:48, height:48, borderRadius:12, background:T.accent, margin:'0 auto', marginBottom:S.base, display:'flex', alignItems:'center', justifyContent:'center', fontSize:20, color:'#fff', fontWeight:700 }}>AI</div>
          <h1 style={{ margin:0, fontSize:22, fontWeight:600, color:T.text }}>低代码智能体平台</h1>
          <p style={{ margin:0, marginTop:S.xs, fontSize:14, color:T.secondary }}>登录到管理后台</p>
        </div>
        <div style={{ display:'flex', flexDirection:'column', gap:S.base }}>
          <input type="text" placeholder="账号" value={username} onChange={e=>setUsername(e.target.value)}
            style={{ width:'100%', padding:'12px 14px', background:T.surface, border:`1px solid ${error?T.danger:T.border}`, borderRadius:8, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
          <input type="password" placeholder="密码" value={password} onChange={e=>setPassword(e.target.value)}
            style={{ width:'100%', padding:'12px 14px', background:T.surface, border:`1px solid ${error?T.danger:T.border}`, borderRadius:8, fontSize:14, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit' }} />
        </div>
        {error && <p style={{ color:T.danger, fontSize:13, marginTop:S.md }}>{error}</p>}
        <button type="submit" disabled={loading} style={{
          width:'100%', padding:'12px 0', marginTop:S.lg, borderRadius:8, fontSize:15, cursor:'pointer', fontWeight:500,
          background:T.accent, color:'#fff', border:'none', opacity:loading?0.6:1,
        }}>{loading?'登录中...':'登录'}</button>
      </form>
    </div>
  );
}
