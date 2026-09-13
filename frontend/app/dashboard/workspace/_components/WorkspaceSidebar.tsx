'use client';

// WorkspaceSidebar —— 左栏：新建会话 + 搜索 + 按 agent 分组的会话列表（对齐 DSH）
//
// 数据源 GET /api/workspace/sessions（后端只列「工作区目录存在」的会话）。
// 重命名 / 删除为占位（后端 DELETE 待补）：悬停显示「待后端支持」。

import { useState } from 'react';
import { Plus, Search, MoreHorizontal, RefreshCw } from 'lucide-react';
import { W, WS } from '../theme';
import { type WorkspaceSession, fmtRelTime } from './useWorkspace';

interface Props {
  sessions: WorkspaceSession[];
  selectedId: string | null;
  onSelect: (s: WorkspaceSession) => void;
  onNew: () => void;
  onRefresh: () => void;
  loading: boolean;
}

export default function WorkspaceSidebar({ sessions, selectedId, onSelect, onNew, onRefresh, loading }: Props) {
  const [q, setQ] = useState('');

  const filtered = q.trim()
    ? sessions.filter(s => (s.label || s.session_id).toLowerCase().includes(q.trim().toLowerCase()))
    : sessions;

  // 按 agent 分组（保序：会话列表后端已按 updated_at 倒序）
  const groups: { key: string; items: WorkspaceSession[] }[] = [];
  for (const s of filtered) {
    const key = s.agent_key || '其他';
    const g = groups.find(x => x.key === key);
    if (g) g.items.push(s); else groups.push({ key, items: [s] });
  }

  return (
    <div style={{ width: 280, flexShrink: 0, background: W.panel, display: 'flex', flexDirection: 'column', minHeight: 0, fontFamily: W.font }}>
      {/* 新建会话 */}
      <div style={{ padding: `${WS.md}px ${WS.base}px ${WS.sm}px` }}>
        <button onClick={onNew} style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: WS.sm, padding: '8px 14px',
          borderRadius: 8, border: `1px dashed ${W.border}`, background: 'transparent',
          color: W.text, fontSize: 13, cursor: 'pointer', fontFamily: 'inherit',
        }}>
          <Plus size={15} /> 新建会话
        </button>
      </div>

      {/* 搜索 */}
      <div style={{ padding: `0 ${WS.base}px ${WS.sm}px` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '6px 10px', borderRadius: 8, background: W.surface }}>
          <Search size={14} color={W.tertiary} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索会话"
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 13, fontFamily: 'inherit' }} />
          <button onClick={onRefresh} title="刷新会话列表" style={{ background: 'none', border: 'none', cursor: 'pointer', padding: 2, color: W.tertiary }}>
            <RefreshCw size={13} className={loading ? 'ws-spin' : ''} />
          </button>
        </div>
        <style>{`@keyframes wsSpin{to{transform:rotate(360deg)}} .ws-spin{animation:wsSpin 1s linear infinite}`}</style>
      </div>

      {/* 会话列表 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `0 ${WS.sm}px ${WS.sm}px` }}>
        {!loading && filtered.length === 0 && (
          <div style={{ padding: `${WS.xxl}px ${WS.base}px`, textAlign: 'center', color: W.tertiary, fontSize: 13 }}>
            {q.trim() ? '没有匹配的会话' : '还没有会话\n开始一次对话后，模型用工具落下的文件会出现在这里'}
          </div>
        )}
        {groups.map(g => (
          <div key={g.key} style={{ marginBottom: WS.xs }}>
            <div style={{ padding: `${WS.sm}px ${WS.sm}px ${WS.xs}px`, fontSize: 11, fontWeight: 600, color: W.tertiary, letterSpacing: '0.05em' }}>
              {g.key}
            </div>
            {g.items.map(s => {
              const active = s.session_id === selectedId;
              return (
                <div key={s.session_id} onClick={() => onSelect(s)} title={s.label || s.session_id}
                  style={{
                    display: 'flex', alignItems: 'center', gap: WS.sm, padding: '8px 10px', borderRadius: 8,
                    cursor: 'pointer', background: active ? W.active : 'transparent',
                    marginBottom: 2,
                  }}
                  onMouseEnter={e => (e.currentTarget.style.background = active ? W.active : W.hover)}
                  onMouseLeave={e => (e.currentTarget.style.background = active ? W.active : 'transparent')}>
                  <div style={{
                    width: 26, height: 26, borderRadius: 7, flexShrink: 0, fontSize: 14,
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: 'rgba(77,107,254,0.16)',
                  }}>💬</div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: 13, color: active ? W.text : W.secondary, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
                      {s.label || s.session_id.slice(0, 8)}
                    </div>
                    <div style={{ fontSize: 11, color: W.tertiary, marginTop: 1 }}>
                      {fmtRelTime(s.updated_at)}{s.entries > 0 ? ` · ${s.entries} 个文件` : ''}
                    </div>
                  </div>
                  <div title="重命名 / 删除（后端待支持）" style={{ color: W.tertiary, opacity: 0, cursor: 'not-allowed' }} className="ws-more">
                    <MoreHorizontal size={14} />
                  </div>
                  <style>{`.ws-more{transition:opacity .12s} div:hover > .ws-more{opacity:.7}`}</style>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
