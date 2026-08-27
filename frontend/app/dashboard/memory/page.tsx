'use client';

import { useRouter } from 'next/navigation';
import { Brain, Users, Bug, ArrowRight } from 'lucide-react';
import { T, S } from '@/app/theme';

export default function MemoryPage() {
  const router = useRouter();

  return (
    <div>
      <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>智能客服记忆系统</h2>
      <p style={{ fontSize:13, color:T.secondary, marginTop:S.sm, marginBottom:S.xl }}>
        从对话中自动提取用户画像，为每次服务提供上下文
      </p>

      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:S.base }}>
        {/* 用户记忆卡片 */}
        <div onClick={() => router.push('/dashboard/memory/users')} style={{
          background:T.surface, borderRadius:10, padding:S.xl, cursor:'pointer',
          border:`1px solid ${T.border}`, transition:'box-shadow .15s',
        }} onMouseEnter={e => e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'}
           onMouseLeave={e => e.currentTarget.style.boxShadow='none'}>
          <div style={{ width:40, height:40, borderRadius:10, background:'#EEF2FF', display:'flex', alignItems:'center', justifyContent:'center', marginBottom:S.base }}>
            <Users size={20} color={T.accent} />
          </div>
          <div style={{ fontSize:15, fontWeight:600, color:T.text }}>用户记忆总览</div>
          <div style={{ fontSize:12, color:T.secondary, marginTop:S.xs, lineHeight:1.5 }}>
            查看所有用户的画像，包括偏好、历史、习惯等自动提取的持久事实
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:S.xs, marginTop:S.base, fontSize:12, color:T.accent }}>
            查看详情 <ArrowRight size={12} />
          </div>
        </div>

        {/* 调试工具卡片 */}
        <div onClick={() => router.push('/dashboard/memory/debug')} style={{
          background:T.surface, borderRadius:10, padding:S.xl, cursor:'pointer',
          border:`1px solid ${T.border}`, transition:'box-shadow .15s',
        }} onMouseEnter={e => e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'}
           onMouseLeave={e => e.currentTarget.style.boxShadow='none'}>
          <div style={{ width:40, height:40, borderRadius:10, background:'#FFF7E8', display:'flex', alignItems:'center', justifyContent:'center', marginBottom:S.base }}>
            <Bug size={20} color={T.warning} />
          </div>
          <div style={{ fontSize:15, fontWeight:600, color:T.text }}>Pipeline 调试</div>
          <div style={{ fontSize:12, color:T.secondary, marginTop:S.xs, lineHeight:1.5 }}>
            输入对话消息，运行 MemoryContext 管道，查看压缩摘要、画像提取、上下文窗口等中间状态
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:S.xs, marginTop:S.base, fontSize:12, color:T.accent }}>
            打开调试 <ArrowRight size={12} />
          </div>
        </div>

        {/* 配置卡片 */}
        <div style={{
          background:T.surface, borderRadius:10, padding:S.xl,
          border:`1px solid ${T.border}`, opacity:0.6,
        }}>
          <div style={{ width:40, height:40, borderRadius:10, background:T.bg, display:'flex', alignItems:'center', justifyContent:'center', marginBottom:S.base }}>
            <Brain size={20} color={T.secondary} />
          </div>
          <div style={{ fontSize:15, fontWeight:600, color:T.text }}>记忆配置</div>
          <div style={{ fontSize:12, color:T.secondary, marginTop:S.xs, lineHeight:1.5 }}>
            管理记忆提取参数：上下文窗口大小、压缩比例、触发阈值
          </div>
          <div style={{ fontSize:12, color:T.tertiary, marginTop:S.base }}>即将上线</div>
        </div>
      </div>
    </div>
  );
}
