'use client';

import { useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { ArrowLeft } from 'lucide-react';
import { T, S, btnPrimary } from '@/app/theme';

interface FAQ { id:number; question:string; answer:string; tags:string[]; is_active:boolean; created_at:string; embedding:number[]|null; }
const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

async function fetchFaqs(page:number){ const r=await fetch(`${API}/api/faqs?page=${page}`,{headers:{'X-Tenant-ID':'1'}}); return r.json() as Promise<{items:FAQ[];total:number}>; }

const KB_CATEGORIES = [
  { key:'faq', name:'FAQ问答对', desc:'客户常见问题与答案库', icon:'📋', count:0 },
  { key:'docs', name:'文档知识库', desc:'产品手册、帮助文档等', icon:'📄', count:0, disabled:true },
  { key:'conversations', name:'历史对话库', desc:'客服历史会话存档', icon:'💬', count:0, disabled:true },
];

function KBList({ faqCount, onEnter }: { faqCount:number; onEnter:(key:string)=>void }) {
  return (
    <div>
      <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text, marginBottom:S.xl }}>知识库</h2>
      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:S.base }}>
        {KB_CATEGORIES.map(kb => (
          <div key={kb.key} onClick={() => !kb.disabled && onEnter(kb.key)}
            style={{
              background:T.surface, borderRadius:10, padding:S.xl, cursor: kb.disabled?'default':'pointer',
              border:`1px solid ${T.border}`, transition:'box-shadow .15s', opacity:kb.disabled?0.5:1,
            }}
            onMouseEnter={e => { if(!kb.disabled) e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'; }}
            onMouseLeave={e => { e.currentTarget.style.boxShadow='none'; }}
          >
            <div style={{ display:'flex', alignItems:'center', gap:S.md, marginBottom:S.md }}>
              <div style={{ width:40, height:40, borderRadius:10, background:T.accentBg, display:'flex', alignItems:'center', justifyContent:'center', fontSize:20 }}>{kb.icon}</div>
              <div>
                <div style={{ fontSize:15, fontWeight:600, color:T.text }}>{kb.name}</div>
                <div style={{ fontSize:12, color:T.secondary, marginTop:2 }}>{kb.desc}</div>
              </div>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
              <span style={{ fontSize:12, color:T.secondary }}>{kb.key==='faq'?`${faqCount} 条记录`:kb.disabled?'即将上线':'—'}</span>
              {!kb.disabled && <span style={{ fontSize:12, color:T.accent }}>进入 →</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export default function KnowledgePage() {
  const [selectedKB, setSelectedKB] = useState<string|null>(null);
  const [page, setPage] = useState(1);
  const qc = useQueryClient();
  const { data, isFetching } = useQuery({ queryKey:['faqs',page], queryFn:()=>fetchFaqs(page) });
  const faqs = data?.items??[], total = data?.total??0;

  const [showForm, setShowForm] = useState(false); const [editId, setEditId] = useState<number|null>(null);
  const [question, setQuestion] = useState(''); const [answer, setAnswer] = useState('');
  const [tags, setTags] = useState(''); const [vectorize, setVectorize] = useState(true);
  const [backfilling, setBackfilling] = useState(false);
  const [file, setFile] = useState<File|null>(null); const [importing, setImporting] = useState(false);

  function openCreate(){ setEditId(null); setQuestion(''); setAnswer(''); setTags(''); setVectorize(true); setShowForm(true); }
  function openEdit(f:FAQ){ setEditId(f.id); setQuestion(f.question); setAnswer(f.answer); setTags(f.tags.join(', ')); setShowForm(true); }
  async function handleSave(){
    const tl=tags.split(',').map(t=>t.trim()).filter(Boolean);
    const body=JSON.stringify({question,answer,tags:tl,vectorize});
    if(editId) await fetch(`${API}/api/faqs/${editId}`,{method:'PUT',headers:{'Content-Type':'application/json','X-Tenant-ID':'1'},body});
    else await fetch(`${API}/api/faqs`,{method:'POST',headers:{'Content-Type':'application/json','X-Tenant-ID':'1'},body});
    setShowForm(false); qc.invalidateQueries({queryKey:['faqs']});
  }
  async function handleBackfill(){ if(!confirm('将为所有向量为空的 FAQ 生成语义向量，确认？'))return; setBackfilling(true);
    try{ const r=await fetch(`${API}/api/faqs/backfill`,{method:'POST',headers:{'X-Tenant-ID':'1'}}); const j=await r.json(); alert(`向量化完成：${j.success}/${j.total} 成功`); qc.invalidateQueries({queryKey:['faqs']}); } catch{ alert('请求失败'); } finally{setBackfilling(false);} }
  async function handleVectorize(id:number){ await fetch(`${API}/api/faqs/${id}/vectorize`,{method:'POST',headers:{'X-Tenant-ID':'1'}}); qc.invalidateQueries({queryKey:['faqs']}); }
  async function handleDelete(id:number){ if(!confirm('确认删除？'))return; await fetch(`${API}/api/faqs/${id}`,{method:'DELETE',headers:{'X-Tenant-ID':'1'}}); qc.invalidateQueries({queryKey:['faqs']}); }
  async function handleImport(){ if(!file)return; setImporting(true); const fd=new FormData(); fd.append('file',file);
    const r=await fetch(`${API}/api/faqs/import`,{method:'POST',headers:{'X-Tenant-ID':'1'},body:fd}); const j=await r.json(); alert(`导入完成：${j.success}/${j.total} 条成功`); setFile(null); setImporting(false); qc.invalidateQueries({queryKey:['faqs']}); }
  const totalPages = Math.ceil(total/20);

  const th: React.CSSProperties = { textAlign:'left', padding:`${S.md}px ${S.base}px`, fontWeight:500, color:T.secondary, fontSize:12, borderBottom:`1px solid ${T.border}` };
  const td: React.CSSProperties = { padding:`${S.md}px ${S.base}px`, fontSize:13, color:T.text, borderBottom:`1px solid ${T.border}`, verticalAlign:'middle' };
  const linkBtn: React.CSSProperties = { background:'none', border:'none', cursor:'pointer', fontSize:12, padding:0 };

  // ── List view ──
  if (!selectedKB) return <KBList faqCount={total} onEnter={setSelectedKB} />;

  // ── FAQ detail view ──
  return (
    <div>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:S.xl }}>
        <div style={{ display:'flex', alignItems:'center', gap:S.md }}>
          <button onClick={()=>setSelectedKB(null)} style={{ padding:4, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, cursor:'pointer', display:'flex', color:T.secondary }}>
            <ArrowLeft size={16} />
          </button>
          <div>
            <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>
              FAQ问答对 {isFetching && <span style={{ marginLeft:S.sm, fontSize:12, color:T.secondary, fontWeight:400 }}>刷新中...</span>}
            </h2>
            <p style={{ margin:0, marginTop:2, fontSize:12, color:T.secondary }}>共 {total} 条</p>
          </div>
        </div>
        <div style={{ display:'flex', gap:S.sm, alignItems:'center' }}>
          <button onClick={handleBackfill} disabled={backfilling} style={{ padding:`${S.xs}px ${S.md}px`, borderRadius:6, fontSize:12, background:T.surface, border:`1px solid ${T.border}`, color:T.text, cursor:'pointer' }}>{backfilling?'向量化中...':'一键向量化'}</button>
          <label style={{ cursor:'pointer', padding:`${S.xs}px ${S.md}px`, borderRadius:6, fontSize:12, background:T.surface, border:`1px solid ${T.border}`, color:T.text }}>
            {file?file.name:'导入 CSV'}<input type="file" accept=".csv" hidden onChange={e=>setFile(e.target.files?.[0]||null)} />
          </label>
          {file && <button onClick={handleImport} disabled={importing} style={btnPrimary}>{importing?'导入中...':'确认导入'}</button>}
          <button onClick={openCreate} style={btnPrimary}>+ 新增</button>
        </div>
      </div>

      <div style={{ background:T.surface, borderRadius:10, border:`1px solid ${T.border}`, overflow:'hidden' }}>
        <table style={{ width:'100%', borderCollapse:'collapse' }}>
          <thead><tr><th style={th}>问题</th><th style={th}>回答</th><th style={th}>标签</th><th style={{...th,width:80}}>向量化</th><th style={{...th,width:140}}>操作</th></tr></thead>
          <tbody>
            {faqs.map(f=>(
              <tr key={f.id}>
                <td style={{...td, fontWeight:500}}>{f.question}</td>
                <td style={{...td, maxWidth:280, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', color:T.secondary}}>{f.answer}</td>
                <td style={td}>{f.tags.map(t=><span key={t} style={{ display:'inline-block', padding:'2px 6px', margin:'2px 4px 2px 0', background:T.accentBg, borderRadius:4, fontSize:11, color:T.accent }}>{t}</span>)}</td>
                <td style={td}>{f.embedding?<span style={{ color:T.success, fontSize:12 }}>已向量化</span>:<button onClick={()=>handleVectorize(f.id)} style={{...linkBtn, color:T.accent }}>向量化</button>}</td>
                <td style={td}>
                  <button onClick={()=>openEdit(f)} style={{...linkBtn, color:T.accent, marginRight:S.md }}>编辑</button>
                  <button onClick={()=>handleDelete(f.id)} style={{...linkBtn, color:T.danger }}>删除</button>
                </td>
              </tr>
            ))}
            {!isFetching && faqs.length===0 && <tr><td colSpan={5} style={{ textAlign:'center', padding:S.xxxl, color:T.secondary }}>暂无数据</td></tr>}
          </tbody>
        </table>
      </div>
      {totalPages>1 && (
        <div style={{ marginTop:S.base, display:'flex', justifyContent:'center', alignItems:'center', gap:S.md }}>
          <button disabled={page<=1} onClick={()=>setPage(p=>p-1)} style={{ padding:`${S.xs}px ${S.md}px`, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, cursor:'pointer', fontSize:13 }}>上一页</button>
          <span style={{ fontSize:13, color:T.secondary }}>{page} / {totalPages}</span>
          <button disabled={page>=totalPages} onClick={()=>setPage(p=>p+1)} style={{ padding:`${S.xs}px ${S.md}px`, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, cursor:'pointer', fontSize:13 }}>下一页</button>
        </div>
      )}

      {showForm && (
        <div onClick={()=>setShowForm(false)} style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.4)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}>
          <div onClick={e=>e.stopPropagation()} style={{ background:T.surface, borderRadius:12, padding:S.xl, width:480, maxHeight:'80vh', overflow:'auto', boxShadow:'0 8px 32px rgba(0,0,0,0.10)' }}>
            <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text, marginBottom:S.lg }}>{editId?'编辑 FAQ':'新增 FAQ'}</h3>
            <input placeholder="问题" value={question} onChange={e=>setQuestion(e.target.value)} style={{ display:'block', width:'100%', padding:'10px 14px', marginBottom:S.md, border:`1px solid ${T.border}`, borderRadius:8, fontSize:14, color:T.text, boxSizing:'border-box', fontFamily:'inherit', outline:'none' }} />
            <textarea placeholder="回答" value={answer} onChange={e=>setAnswer(e.target.value)} rows={4} style={{ display:'block', width:'100%', padding:'10px 14px', marginBottom:S.md, border:`1px solid ${T.border}`, borderRadius:8, fontSize:14, color:T.text, boxSizing:'border-box', fontFamily:'inherit', outline:'none', resize:'vertical' }} />
            <input placeholder="标签（逗号分隔）" value={tags} onChange={e=>setTags(e.target.value)} style={{ display:'block', width:'100%', padding:'10px 14px', marginBottom:S.md, border:`1px solid ${T.border}`, borderRadius:8, fontSize:14, color:T.text, boxSizing:'border-box', fontFamily:'inherit', outline:'none' }} />
            {!editId && <label style={{ display:'flex', alignItems:'center', gap:S.sm, fontSize:13, color:T.secondary, cursor:'pointer', marginBottom:S.md }}><input type="checkbox" checked={vectorize} onChange={e=>setVectorize(e.target.checked)} style={{ accentColor:T.accent }} />生成语义向量</label>}
            <div style={{ display:'flex', gap:S.md, justifyContent:'flex-end', marginTop:S.base }}>
              <button onClick={()=>setShowForm(false)} style={{ padding:`${S.sm}px ${S.lg}px`, borderRadius:6, fontSize:13, cursor:'pointer', background:T.surface, border:`1px solid ${T.border}`, color:T.text }}>取消</button>
              <button onClick={handleSave} style={btnPrimary}>保存</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
