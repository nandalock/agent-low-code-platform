'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import dynamic from 'next/dynamic';
import { MessageCircle, BookOpen, Bot, Plug, ChevronLeft, ChevronRight, Brain, Wrench, GitBranch } from 'lucide-react';
import { T, S } from '@/app/theme';

const ChatPageInner = dynamic(() => import('./_components/ChatPageInner'), { ssr:false });

interface MenuItem { label:string; path:string; icon:React.ReactNode; }
const MENU: MenuItem[] = [
  { label:'聊天', path:'/dashboard/chat', icon:<MessageCircle size={18} /> },
  { label:'知识库', path:'/dashboard/knowledge', icon:<BookOpen size={18} /> },
  { label:'Agent', path:'/dashboard/agents', icon:<Bot size={18} /> },
  { label:'MCP 工具', path:'/dashboard/mcp', icon:<Wrench size={18} /> },
  { label:'集成管理', path:'/dashboard/integrations', icon:<Plug size={18} /> },
  { label:'记忆系统', path:'/dashboard/memory', icon:<Brain size={18} /> },
  { label:'工作流', path:'/dashboard/workflow', icon:<GitBranch size={18} /> },
];
const COLLAPSED_W=64, EXPANDED_W=220;

export default function DashboardLayout({ children }:{ children:React.ReactNode }) {
  const router = useRouter(); const pathname = usePathname();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const [collapsed, setCollapsed] = useState(false);
  useEffect(() => { setCollapsed(localStorage.getItem('sidebar_collapsed')==='true'); }, []);
  const isChat = pathname==='/dashboard/chat';
  const isMcp = pathname === '/dashboard/mcp';
  const isWorkflowEditor = pathname.startsWith('/dashboard/workflow/') && pathname !== '/dashboard/workflow';
  const [chatMounted, setChatMounted] = useState(false);
  useEffect(()=>{ if(isChat&&!chatMounted) setChatMounted(true); },[isChat,chatMounted]);

  const toggle = useCallback(()=>{ setCollapsed(p=>{ const n=!p; localStorage.setItem('sidebar_collapsed',String(n)); return n; }); },[]);
  async function handleLogout(){ await fetch('/api/logout',{method:'POST'}); router.push('/login'); }

  const sidebarW = collapsed?COLLAPSED_W:EXPANDED_W;
  const isActive = (item:MenuItem) => item.path==='/dashboard'?pathname==='/dashboard':pathname.startsWith(item.path);

  return (
    <div style={{ display:'flex', height:'100vh' }}>
      {/* ── Coze-style sidebar: dark navy ── */}
      <div style={{ width:sidebarW, background:T.sidebar, display:'flex', flexDirection:'column', flexShrink:0, transition:'width .18s ease', overflow:'hidden' }}>
        {/* Brand */}
        <div style={{ padding:collapsed?`${S.base}px ${S.sm}px`:`${S.lg}px ${S.xl}px ${S.base}px`, display:'flex', alignItems:'center', gap:S.sm }}>
          <div style={{ width:32, height:32, borderRadius:8, background:T.accent, flexShrink:0, display:'flex', alignItems:'center', justifyContent:'center', fontSize:14, fontWeight:700, color:'#fff' }}>CS</div>
          {!collapsed && <span style={{ fontSize:16, fontWeight:600, color:'#fff', whiteSpace:'nowrap' }}>低代码平台</span>}
        </div>
        {/* Nav */}
        <nav style={{ flex:1, padding:`${S.sm}px ${S.xs}px`, display:'flex', flexDirection:'column', gap:1 }}>
          {MENU.map(item=>{
            const active = isActive(item);
            return (
              <Link key={item.path} href={item.path} title={collapsed?item.label:undefined} style={{
                display:'flex', alignItems:'center', gap:10, padding:collapsed?'10px 0':'10px 12px',
                borderRadius:6, justifyContent:collapsed?'center':'flex-start',
                color:active?'#fff':'#86909C', textDecoration:'none', fontSize:14, fontWeight:active?500:400,
                background:active?'rgba(51,112,255,0.20)':'transparent', whiteSpace:'nowrap',
              }}>{item.icon}{!collapsed && item.label}</Link>
            );
          })}
        </nav>
        {/* Collapse button */}
        <div style={{ padding:`0 ${S.xs}px ${S.xs}px` }}>
          <button onClick={toggle} style={{ width:'100%', padding:'6px 0', borderRadius:6, border:'none', cursor:'pointer', background:'transparent', color:'#86909C', display:'flex', justifyContent:'center' }}>{collapsed?<ChevronRight size={16} />:<ChevronLeft size={16} />}</button>
        </div>
        {/* User */}
        <div style={{ borderTop:'1px solid rgba(255,255,255,0.08)', position:'relative' }}>
          <div onClick={()=>setShowUserMenu(!showUserMenu)} style={{
            padding:collapsed?`${S.md}px 0`:`${S.md}px ${S.base}px`, fontSize:13, color:'#B0B8C8', cursor:'pointer',
            display:'flex', alignItems:'center', gap:S.sm, justifyContent:collapsed?'center':'flex-start',
          }}>
            <div style={{ width:28, height:28, borderRadius:'50%', background:'rgba(51,112,255,0.20)', flexShrink:0, display:'flex', alignItems:'center', justifyContent:'center', fontSize:12, fontWeight:600, color:T.accent }}>JK</div>
            {!collapsed && <span>jk</span>}
          </div>
          {showUserMenu && (
            <div style={{ position:'absolute', bottom:48, left:S.sm, background:'#FFFFFF', borderRadius:8, width:160, zIndex:100, boxShadow:'0 4px 16px rgba(0,0,0,0.10)', overflow:'hidden' }}>
              <div style={{ padding:`10px ${S.base}px`, fontSize:12, color:T.secondary, borderBottom:`1px solid ${T.border}` }}>管理员</div>
              <div onClick={handleLogout} style={{ padding:`10px ${S.base}px`, fontSize:13, cursor:'pointer', color:T.danger }}>退出登录</div>
            </div>
          )}
        </div>
      </div>

      {/* ── Content ── */}
      {!isChat && !isMcp && !isWorkflowEditor && <div style={{ flex:1, background:T.bg, overflow:'auto', padding:S.xl }}>{children}</div>}
      {isMcp && <div style={{ flex:1, overflow:'hidden' }}>{children}</div>}
      {isWorkflowEditor && <div style={{ flex:1, overflow:'hidden' }}>{children}</div>}
      {chatMounted && <div style={{ flex:1, display:isChat?undefined:'none' }}><ChatPageInner /></div>}
    </div>
  );
}
