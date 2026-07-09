'use client';

import { useState, useEffect, useCallback } from 'react';
import { useRouter } from 'next/navigation';
import { Search, Plus, FileText, Upload, ChevronDown, Workflow, Clock } from 'lucide-react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TABS = [
  { key:'all', label:'全部' },
  { key:'workflow', label:'工作室' },
];

interface WorkflowItem {
  id: number;
  name: string;
  status: string;
  node_count: number;
  created_at: string;
  updated_at: string;
}

export default function WorkflowStudioPage() {
  const router = useRouter();
  const [activeTab, setActiveTab] = useState('all');
  const [search, setSearch] = useState('');
  const [showCreateMenu, setShowCreateMenu] = useState(false);
  const [items, setItems] = useState<WorkflowItem[]>([]);
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    try {
      const r = await fetch(`${API}/api/workflows`);
      if (r.ok) { const d = await r.json(); setItems(d.items || []); }
    } catch {}
    setLoading(false);
  }, []);

  useEffect(() => { load(); }, [load]);

  const handleCreateBlank = () => {
    router.push('/dashboard/workflow/new');
  };

  const filtered = items.filter(item => {
    if (!search) return true;
    return item.name.toLowerCase().includes(search.toLowerCase());
  });

  const formatTime = (t: string) => {
    if (!t) return '-';
    const d = new Date(t);
    const now = new Date();
    const diffMs = now.getTime() - d.getTime();
    const diffMin = Math.floor(diffMs / 60000);
    if (diffMin < 1) return '刚刚';
    if (diffMin < 60) return `${diffMin} 分钟前`;
    const diffHour = Math.floor(diffMin / 60);
    if (diffHour < 24) return `${diffHour} 小时前`;
    const diffDay = Math.floor(diffHour / 24);
    if (diffDay < 7) return `${diffDay} 天前`;
    return d.toLocaleDateString('zh-CN', { month:'short', day:'numeric' });
  };

  return (
    <div>
      {/* ── Top tabs ── */}
      <div style={{ display:'flex', alignItems:'center', gap:0, marginBottom:S.xl, borderBottom:`1px solid ${T.border}` }}>
        {TABS.map(tab => (
          <button key={tab.key} onClick={() => setActiveTab(tab.key)} style={{
            padding:`${S.sm}px ${S.base}px`, fontSize:13, fontWeight:activeTab===tab.key?600:400,
            color:activeTab===tab.key?T.text:T.secondary, background:'none', border:'none', cursor:'pointer',
            borderBottom:activeTab===tab.key?`2px solid ${T.accent}`:'2px solid transparent',
            marginBottom:-1, transition:'color .12s',
          }}>
            {tab.label}
          </button>
        ))}
      </div>

      {/* ── Search + Create ── */}
      <div style={{ display:'flex', alignItems:'center', gap:S.sm, marginBottom:S.xl }}>
        <div style={{ flex:1, position:'relative', maxWidth:320 }}>
          <Search size={14} color={T.tertiary} style={{ position:'absolute', left:10, top:9 }} />
          <input placeholder="搜索工作流" value={search} onChange={e => setSearch(e.target.value)} style={{
            width:'100%', padding:'7px 10px 7px 30px', background:T.surface, border:`1px solid ${T.border}`,
            borderRadius:6, fontSize:13, color:T.text, outline:'none', boxSizing:'border-box', fontFamily:'inherit',
          }} />
        </div>

        <div style={{ position:'relative' }}>
          <button onClick={() => setShowCreateMenu(!showCreateMenu)} style={{
            display:'flex', alignItems:'center', gap:6, padding:`${S.sm}px ${S.base}px`,
            borderRadius:6, fontSize:13, cursor:'pointer', background:T.accent, color:'#fff', border:'none', fontWeight:500,
          }}>
            <Plus size={14} /> 创建应用
          </button>
          {showCreateMenu && (
            <div style={{
              position:'absolute', top:38, right:0, background:T.surface, borderRadius:8, width:180,
              boxShadow:'0 4px 16px rgba(0,0,0,0.10)', border:`1px solid ${T.border}`, zIndex:100, overflow:'hidden',
            }} onClick={() => setShowCreateMenu(false)}>
              <div onClick={handleCreateBlank} style={{
                padding:`${S.sm}px ${S.base}px`, fontSize:13, cursor:'pointer', color:T.text, display:'flex', alignItems:'center', gap:S.sm,
              }}><Plus size={14} /> 创建空白工作室</div>
              <div style={{
                padding:`${S.sm}px ${S.base}px`, fontSize:13, cursor:'pointer', color:T.text, display:'flex', alignItems:'center', gap:S.sm,
              }}><FileText size={14} /> 从模板创建</div>
              <div style={{
                padding:`${S.sm}px ${S.base}px`, fontSize:13, cursor:'pointer', color:T.text, display:'flex', alignItems:'center', gap:S.sm, borderTop:`1px solid ${T.border}`,
              }}><Upload size={14} /> 导入 DSL 文件</div>
            </div>
          )}
        </div>
      </div>

      {/* ── Content ── */}
      {loading ? (
        <div style={{ textAlign:'center', padding:`${S.huge}px 0`, color:T.secondary, fontSize:14 }}>加载中...</div>
      ) : filtered.length === 0 ? (
        <div style={{ textAlign:'center', padding:`${S.huge}px 0`, color:T.tertiary }}>
          <div style={{ width:64, height:64, borderRadius:16, background:T.bg, display:'flex', alignItems:'center', justifyContent:'center', margin:'0 auto', marginBottom:S.lg }}>
            <Workflow size={32} color={T.tertiary} />
          </div>
          <div style={{ fontSize:14, color:T.secondary, marginBottom:S.xs }}>
            {search ? '没有匹配的工作流' : '暂无工作室'}
          </div>
          <div style={{ fontSize:12 }}>点击"创建应用"开始编排你的第一个工作流</div>
        </div>
      ) : (
        <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(280px, 1fr))', gap:S.base }}>
          {filtered.map(item => (
            <div key={item.id} onClick={() => router.push(`/dashboard/workflow/${item.id}`)} style={{
              background:T.surface, borderRadius:10, border:`1px solid ${T.border}`,
              padding:S.base, cursor:'pointer', transition:'box-shadow .15s',
            }}
            onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 4px 16px rgba(0,0,0,0.08)')}
            onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
            >
              {/* Icon + Name */}
              <div style={{ display:'flex', alignItems:'flex-start', gap:S.md, marginBottom:S.md }}>
                <div style={{
                  width:40, height:40, borderRadius:10, background:'linear-gradient(135deg, #3370FF 0%, #6B9FFF 100%)',
                  display:'flex', alignItems:'center', justifyContent:'center', flexShrink:0,
                }}>
                  <Workflow size={20} color="#fff" />
                </div>
                <div style={{ flex:1, minWidth:0 }}>
                  <div style={{ fontSize:14, fontWeight:600, color:T.text, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                    {item.name}
                  </div>
                  <div style={{ display:'flex', alignItems:'center', gap:S.xs, marginTop:4 }}>
                    <span style={{ fontSize:11, color:T.accent, background:T.accentBg, padding:'1px 6px', borderRadius:4 }}>
                      工作流
                    </span>
                    <span style={{ fontSize:11, color:T.tertiary }}>添加标签</span>
                  </div>
                </div>
              </div>

              {/* Footer */}
              <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between' }}>
                <div style={{ display:'flex', alignItems:'center', gap:4, fontSize:11, color:T.tertiary }}>
                  <Clock size={11} />
                  <span>编辑于{formatTime(item.updated_at)}</span>
                </div>
                <span style={{
                  fontSize:11, padding:'1px 8px', borderRadius:10,
                  background: item.status === 'published' ? '#E8F8EF' : T.bg,
                  color: item.status === 'published' ? '#00B42A' : T.secondary,
                }}>
                  {item.status === 'published' ? '已发布' : '草稿'}
                </span>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
