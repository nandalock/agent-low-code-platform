'use client';

import { LayoutGrid } from 'lucide-react';
import { T, S } from '@/app/theme';

export default function WorkspacePage() {
  return (
    <div style={{ display:'flex', flexDirection:'column', alignItems:'center', justifyContent:'center', minHeight:'60vh', gap:S.lg }}>
      <div style={{ width:64, height:64, borderRadius:16, background:'rgba(51,112,255,0.10)', display:'flex', alignItems:'center', justifyContent:'center', color:T.accent }}>
        <LayoutGrid size={30} />
      </div>
      <div style={{ textAlign:'center' }}>
        <div style={{ fontSize:18, fontWeight:600, color:T.text, marginBottom:S.sm }}>工作区</div>
        <div style={{ fontSize:13, color:T.secondary }}>功能开发中，敬请期待</div>
      </div>
    </div>
  );
}
