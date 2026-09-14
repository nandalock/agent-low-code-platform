'use client';

// WorkspaceSidebar —— 左栏：新建会话 + 搜索 + 按 agent 分组的会话列表
//
// 改造点：会话行不再套一个 emoji 头像方块（那是「丑」的主要来源之一），
// 改成一行的标题 + 次级元信息，选中靠表面色差、悬浮靠半透明蒙层。
//
// 数据源 GET /api/workspace/sessions（后端只列「工作区目录存在」的会话）。
// 重命名 / 删除为占位（后端 DELETE 待补）：悬停显示「待后端支持」。

import { useState } from 'react';
import { Plus, Search, MoreHorizontal, RefreshCw } from 'lucide-react';
import { W, R, WS } from '../theme';
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
    <div style={{
      width: 280, flexShrink: 0, background: W.panel,
      display: 'flex', flexDirection: 'column', minHeight: 0, fontFamily: W.font,
    }}>
      {/* 新建会话 */}
      <div style={{ padding: `${WS.md}px ${WS.md}px ${WS.sm}px` }}>
        <button onClick={onNew} className="ws-btn"
          style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: WS.sm,
            height: 34, padding: '0 10px', borderRadius: R.sm,
            color: W.text, fontSize: 13, fontFamily: 'inherit', textAlign: 'left',
          }}>
          <Plus size={15} color={W.secondary} />
          新建会话
        </button>
      </div>

      {/* 搜索 */}
      <div style={{ padding: `0 ${WS.md}px ${WS.sm}px` }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: WS.sm, height: 30, padding: '0 8px',
          borderRadius: R.sm, background: W.surface, boxShadow: `inset 0 0 0 0.5px ${W.borderSoft}`,
        }}>
          <Search size={13} color={W.dimmed} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索会话"
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 13, fontFamily: 'inherit' }} />
          <button onClick={onRefresh} title="刷新会话列表"
            className="ws-btn ws-round"
            style={{ padding: 2, color: W.dimmed, display: 'flex' }}>
            <RefreshCw size={13} className={loading ? 'ws-spin' : ''} />
          </button>
        </div>
      </div>

      {/* 会话列表 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `0 ${WS.sm}px ${WS.sm}px` }}>
        {!loading && filtered.length === 0 && (
          <div style={{ padding: `${WS.xxl}px ${WS.base}px`, textAlign: 'center', color: W.dimmed, fontSize: 13, lineHeight: '20px', whiteSpace: 'pre-line' }}>
            {q.trim() ? '没有匹配的会话' : '还没有会话\n开始一次对话后，模型用工具落下的文件会出现在这里'}
          </div>
        )}
        {groups.map(g => (
          <div key={g.key} style={{ marginBottom: WS.md }}>
            <div style={{
              padding: `${WS.sm}px ${WS.sm}px ${WS.xs}px`, fontSize: 11, lineHeight: '16px',
              fontWeight: 500, color: W.dimmed, letterSpacing: '0.04em',
            }}>
              {g.key}
            </div>
            {g.items.map(s => {
              const active = s.session_id === selectedId;
              return (
                <div key={s.session_id} onClick={() => onSelect(s)} title={s.label || s.session_id}
                  className="ws-item ws-hover-host" data-active={active}
                  style={{
                    position: 'relative', display: 'flex', alignItems: 'center', gap: WS.sm,
                    padding: '7px 10px', borderRadius: R.sm, marginBottom: 1,
                  }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{
                      fontSize: 13, lineHeight: '20px', color: active ? W.text : W.secondary,
                      whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
                    }}>
                      {s.label || s.session_id.slice(0, 8)}
                    </div>
                    <div style={{ fontSize: 11, lineHeight: '16px', color: W.dimmed, marginTop: 1 }}>
                      {fmtRelTime(s.updated_at)}{s.entries > 0 ? ` · ${s.entries} 个文件` : ''}
                    </div>
                  </div>
                  <div title="重命名 / 删除（后端待支持）"
                    className="ws-on-hover"
                    style={{ color: W.dimmed, cursor: 'not-allowed', display: 'flex', flexShrink: 0 }}>
                    <MoreHorizontal size={14} />
                  </div>
                </div>
              );
            })}
          </div>
        ))}
      </div>
    </div>
  );
}
