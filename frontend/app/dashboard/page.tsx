import { T, S } from '@/app/theme';

export default function DashboardPage() {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100%' }}>
      <div style={{ textAlign:'center' }}>
        <div style={{ width:64, height:64, borderRadius:16, background:T.accentBg, margin:'0 auto', marginBottom:S.xl, display:'flex', alignItems:'center', justifyContent:'center', border:`1px solid rgba(51,112,255,0.15)` }}>
          <span style={{ fontSize:24 }}>👋</span>
        </div>
        <p style={{ fontSize:18, fontWeight:600, color:T.text, margin:0 }}>欢迎使用智能客服</p>
        <p style={{ fontSize:14, color:T.secondary, margin:0, marginTop:S.sm }}>选择左侧功能开始使用</p>
        <div style={{ display:'flex', gap:S.base, marginTop:S.xxxl, justifyContent:'center' }}>
          {[{ label:'会话', icon:'💬' },{ label:'知识库', icon:'📚' },{ label:'Agent', icon:'🤖' }].map(s=>(
            <div key={s.label} style={{ background:T.surface, borderRadius:10, padding:`${S.lg}px ${S.xl}px`, minWidth:120, border:`1px solid ${T.border}`, textAlign:'center' }}>
              <div style={{ fontSize:22, marginBottom:S.sm }}>{s.icon}</div>
              <div style={{ fontSize:12, color:T.secondary }}>{s.label}</div>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
