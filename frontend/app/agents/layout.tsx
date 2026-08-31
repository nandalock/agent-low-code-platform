'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import { MessageCircle, BookOpen, Bot, Settings, ChevronLeft, ChevronRight, Home, Brain } from 'lucide-react';
import { T, S } from '@/app/theme';

interface AgentItem { key:string; name:string; }
const AGENTS: AgentItem[] = [
  { key:'faqagent', name:'FAQ 知识检索' },
  { key:'order_agent', name:'订单处理' },
  { key:'ticket_agent', name:'工单售后' },
  { key:'human_handoff', name:'人工转接' },
  { key:'supervisor', name:'Supervisor' },
];

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const COLLAPSED_W=64, EXPANDED_W=200;

export default function AgentsLayout({ children }:{ children:React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [collapsed, setCollapsed] = useState(false);
  const [routerAgents, setRouterAgents] = useState<AgentItem[]>([]);
  useEffect(() => { setCollapsed(localStorage.getItem('agents_sidebar_collapsed')==='true'); }, []);
  useEffect(() => {
    fetch(`${API}/api/agents`).then(r => r.json()).then(list => {
      const routers = (list as any[]).filter((a:any) => a.agent_type === 'router')
        .map((a:any) => ({ key: a.key, name: a.name }));
      setRouterAgents(routers);
    }).catch(() => {});
  }, []);
  const toggle = useCallback(()=>{ setCollapsed(p=>{ const n=!p; localStorage.setItem('agents_sidebar_collapsed',String(n)); return n; }); },[]);
  const sidebarW = collapsed?COLLAPSED_W:EXPANDED_W;

  const isActive = (key:string) => pathname.includes(key);

  return (
    <div style={{ display:'flex', height:'100vh' }}>
      {/* ── Sidebar ── */}
      <div style={{ width:sidebarW, background:T.sidebar, display:'flex', flexDirection:'column', flexShrink:0, transition:'width .18s ease', overflow:'hidden' }}>
        {/* Brand / section label */}
        <div style={{ padding:collapsed?`${S.base}px 0`:`${S.lg}px ${S.base}px ${S.base}px`, display:'flex', flexDirection:collapsed?'column':'column', alignItems:collapsed?'center':'flex-start', gap:S.xs }}>
          <div style={{ width:32, height:32, borderRadius:8, background:T.accent, display:'flex', alignItems:'center', justifyContent:'center', fontSize:14, fontWeight:700, color:'#fff', flexShrink:0, margin:collapsed?'0 auto':0 }}>A</div>
          {!collapsed && <span style={{ fontSize:14, fontWeight:600, color:'#fff', whiteSpace:'nowrap' }}>Agent 中心</span>}
        </div>

        {/* Back to dashboard */}
        <div style={{ padding:`0 ${S.xs}px ${S.xs}px` }}>
          <Link href="/dashboard" style={{
            display:'flex', alignItems:'center', gap:collapsed?0:S.sm, padding:collapsed?'8px 0':'8px 10px',
            borderRadius:6, justifyContent:collapsed?'center':'flex-start',
            color:'#86909C', textDecoration:'none', fontSize:13, whiteSpace:'nowrap',
          }}><Home size={16} />{!collapsed && '返回首页'}</Link>
        </div>

        {/* Divider */}
        <div style={{ height:1, background:'rgba(255,255,255,0.06)', margin:`0 ${S.sm}px` }} />

        {/* Agent list */}
        <nav style={{ flex:1, padding:`${S.sm}px ${S.xs}px`, display:'flex', flexDirection:'column', gap:1 }}>
          {AGENTS.map(a=>{
            const active = isActive(a.key);
            return (
              <Link key={a.key} href={`/agents/${a.key}`} style={{
                display:'flex', alignItems:'center', gap:collapsed?0:S.sm, padding:collapsed?'10px 0':'10px 10px',
                borderRadius:6, justifyContent:collapsed?'center':'flex-start',
                color:active?'#fff':'#86909C', textDecoration:'none', fontSize:13, fontWeight:active?500:400,
                background:active?'rgba(51,112,255,0.20)':'transparent', whiteSpace:'nowrap',
              }}><Bot size={16} />{!collapsed && a.name}</Link>
            );
          })}
        </nav>

        {/* Router Agents */}
        {routerAgents.length > 0 && !collapsed && (
          <div style={{ fontSize: 10, color: '#86909C', padding: `${S.sm}px ${S.sm}px ${S.xs}px`, fontWeight: 500, textTransform: 'uppercase', letterSpacing: '.5px' }}>
            路由 Agent
          </div>
        )}
        {routerAgents.slice(0, collapsed ? 3 : 99).map(a => {
          const active = isActive(a.key);
          return (
            <Link key={`router-${a.key}`} href={`/agents/router/${a.key}`} style={{
              display: 'flex', alignItems: 'center', gap: collapsed ? 0 : S.sm,
              padding: collapsed ? '10px 0' : '10px 10px',
              borderRadius: 6, justifyContent: collapsed ? 'center' : 'flex-start',
              color: active ? '#fff' : '#86909C', textDecoration: 'none',
              fontSize: 13, fontWeight: active ? 500 : 400,
              background: active ? 'rgba(147,51,234,0.25)' : 'transparent',
              whiteSpace: 'nowrap',
              margin: routerAgents.length > 0 ? 0 : undefined,
            }}><Brain size={16} style={{ color: active ? '#c084fc' : '#86909C' }} />{!collapsed && a.name}</Link>
          );
        })}

        {/* Collapse */}
        <div style={{ padding:`0 ${S.xs}px ${S.xs}px` }}>
          <button onClick={toggle} style={{ width:'100%', padding:'6px 0', borderRadius:6, border:'none', cursor:'pointer', background:'transparent', color:'#86909C', display:'flex', justifyContent:'center' }}>{collapsed?<ChevronRight size={16}/>:<ChevronLeft size={16}/>}</button>
        </div>
      </div>

      {/* ── Content ── */}
      {/* overflow:auto：页面聊天列有 420px 保底宽度，窄视口下宁可整体横向滚动也不裁剪 */}
      <div style={{ flex:1, minWidth:0, overflow:'auto' }}>
        {children}
      </div>
    </div>
  );
}
