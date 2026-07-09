'use client';

import { useEffect, useState, useRef, useCallback } from 'react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Conversation { id:number; cid:string; customer_name:string; channel:string; last_msg:string; last_time:string; count:number; status:string; }
interface Message { id:number; conversation_id:number; role:string; sender_name:string; content:string; created_at:string; }

export default function ChatPageInner() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedId, setSelectedId] = useState<number|null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const [channel, setChannel] = useState('');
  const [inputText, setInputText] = useState('');
  const selectedIdRef = useRef<number|null>(null);
  useEffect(()=>{ selectedIdRef.current=selectedId; },[selectedId]);

  const loadConversations = useCallback(async ()=>{
    try{ const p=new URLSearchParams(); if(channel) p.set('channel',channel); const r=await fetch(`${API}/api/chat/conversations?${p}`,{headers:{'X-Tenant-ID':'1'}}); if(r.ok){ const d=await r.json(); setConversations((d.items||[]).map((c:any)=>({id:c.id,cid:c.channel_conversation_id||'',customer_name:c.customer_name||'',channel:c.channel||'',last_msg:c.last_msg||'',last_time:c.last_time||'',count:c.msg_count||0,status:c.status||'active'}))); } }catch{}
  },[channel]);
  const loadMessages = useCallback(async (convId:number)=>{
    setLoading(true); try{ const r=await fetch(`${API}/api/chat/conversations/${convId}/messages`,{headers:{'X-Tenant-ID':'1'}}); const d=await r.json(); setMessages(d.items||[]); }catch{} finally{setLoading(false);}
  },[]);
  useEffect(()=>{ loadConversations(); },[loadConversations]);
  useEffect(()=>{ if(!selectedId)return; loadMessages(selectedId); },[selectedId,loadMessages]);

  useEffect(()=>{
    const wsUrl=API.replace('https://','wss://').replace('http://','ws://')+'/api/ws/chat';
    const ws=new WebSocket(wsUrl);
    ws.onmessage=(event)=>{ const p=JSON.parse(event.data); if(p.type!=='new_message')return; loadConversations(); if(p.conversation_id&&p.conversation_id===selectedIdRef.current&&p.data){ setMessages(prev=>[...prev,{id:0,conversation_id:p.conversation_id,role:p.data.role||'customer',sender_name:p.data.sender_name||'',content:p.data.content||'',created_at:p.data.time||''}]); } };
    return ()=>{ ws.close(); };
  },[loadConversations]);

  const handleSend = async ()=>{ if(!inputText.trim()||!selectedId)return;
    try{ const r=await fetch(`${API}/api/chat/conversations/${selectedId}/messages`,{method:'POST',headers:{'Content-Type':'application/json','X-Tenant-ID':'1'},body:JSON.stringify({role:'agent',sender_name:'客服',content:inputText})}); if(r.ok){ const m=await r.json(); setMessages(prev=>[...prev,m]); setInputText(''); loadConversations(); } }catch{}
  };
  const handleCreateTest = async ()=>{ try{ const r=await fetch(`${API}/api/chat/conversations`,{method:'POST',headers:{'Content-Type':'application/json','X-Tenant-ID':'1'},body:JSON.stringify({channel:'test',customer_name:'测试客户'})}); if(r.ok) await loadConversations(); }catch{} };

  return (
    <div style={{ display:'flex', height:'100%', gap:S.base, padding:S.base, background:T.bg }}>
      {/* Left: conversation list */}
      <div style={{ width:280, background:T.surface, borderRadius:10, border:`1px solid ${T.border}`, display:'flex', flexDirection:'column', flexShrink:0 }}>
        <div style={{ padding:`${S.base}px`, borderBottom:`1px solid ${T.border}`, fontWeight:600, fontSize:14, color:T.text }}>会话 ({conversations.length})</div>
        <div style={{ display:'flex', gap:S.xs, padding:`${S.sm}px ${S.base}px`, borderBottom:`1px solid ${T.border}` }}>
          {['','xianyu','test'].map(ch=><button key={ch} onClick={()=>setChannel(ch)} style={{ padding:'3px 12px', borderRadius:14, border:`1px solid ${T.border}`, fontSize:12, cursor:'pointer', background:channel===ch?T.accentBg:'transparent', color:channel===ch?T.accent:T.secondary }}>{ch||'全部'}</button>)}
        </div>
        <div style={{ flex:1, overflow:'auto' }}>
          {conversations.length===0&&<p style={{ textAlign:'center', color:T.secondary, fontSize:13, marginTop:S.xxxl }}>暂无会话</p>}
          {conversations.map(c=>(
            <div key={c.id} onClick={()=>setSelectedId(c.id)} style={{ padding:`${S.md}px ${S.base}px`, cursor:'pointer', borderBottom:`1px solid ${T.border}`, background:selectedId===c.id?T.accentBg:'transparent' }}>
              <div style={{ display:'flex', justifyContent:'space-between', marginBottom:S.xs }}>
                <span style={{ fontSize:13, fontWeight:600, color:T.text }}>
                  <span style={{ width:6, height:6, borderRadius:'50%', display:'inline-block', marginRight:6, background:c.status==='active'?T.success:T.tertiary, verticalAlign:'middle' }} />
                  {c.customer_name||'未知'}
                </span>
                <span style={{ fontSize:11, color:T.secondary }}>{c.last_time?new Date(c.last_time).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):''}</span>
              </div>
              <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                <span style={{ fontSize:12, color:T.secondary, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', flex:1 }}>{c.last_msg}</span>
                <span style={{ fontSize:10, background:T.accentBg, color:T.accent, padding:'1px 6px', borderRadius:8, marginLeft:6, flexShrink:0 }}>{c.channel}</span>
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding:S.sm, borderTop:`1px solid ${T.border}` }}>
          <button onClick={handleCreateTest} style={{ width:'100%', padding:'6px 0', borderRadius:6, border:`1px dashed ${T.border}`, background:'transparent', color:T.secondary, fontSize:13, cursor:'pointer' }}>+ 新建测试会话</button>
        </div>
      </div>

      {/* Right: messages */}
      <div style={{ flex:1, background:T.surface, borderRadius:10, border:`1px solid ${T.border}`, display:'flex', flexDirection:'column' }}>
        {!selectedId?<div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:T.secondary, fontSize:14 }}>选择一个会话</div>
        :loading?<div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:T.secondary }}>加载中...</div>
        :<>
          <div style={{ flex:1, padding:S.base, overflow:'auto' }}>
            {messages.length===0&&<p style={{ textAlign:'center', color:T.secondary, marginTop:S.huge }}>暂无消息</p>}
            {messages.map((msg,i)=>{ const isAgent=msg.role==='agent';
              return (<div key={msg.id||i} style={{ marginBottom:S.base, display:'flex', flexDirection:'column', alignItems:isAgent?'flex-end':'flex-start' }}>
                <div style={{ fontSize:12, color:T.secondary, marginBottom:S.xs }}>{msg.sender_name||msg.role} · {msg.created_at?new Date(msg.created_at).toLocaleTimeString('zh-CN',{hour:'2-digit',minute:'2-digit'}):''}</div>
                <div style={{ display:'inline-block', maxWidth:'80%', padding:`${S.sm}px ${S.base}px`, borderRadius:8, fontSize:14, lineHeight:1.5, whiteSpace:'pre-wrap', wordBreak:'break-word',
                  background:isAgent?T.accent:T.bg, color:isAgent?'#fff':T.text, borderBottomRightRadius:isAgent?2:8, borderBottomLeftRadius:isAgent?8:2 }}>{msg.content}</div>
              </div>);
            })}
          </div>
          <div style={{ padding:`${S.md}px ${S.base}px`, borderTop:`1px solid ${T.border}`, display:'flex', gap:S.sm }}>
            <input type="text" value={inputText} onChange={e=>setInputText(e.target.value)} onKeyDown={e=>{if(e.key==='Enter')handleSend();}} placeholder="输入消息..."
              style={{ flex:1, padding:'10px 14px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, fontSize:14, color:T.text, outline:'none', fontFamily:'inherit' }} />
            <button onClick={handleSend} style={{ padding:'10px 22px', borderRadius:6, border:'none', background:T.accent, color:'#fff', fontSize:14, cursor:'pointer', fontWeight:500 }}>发送</button>
          </div>
        </>}
      </div>
    </div>
  );
}
