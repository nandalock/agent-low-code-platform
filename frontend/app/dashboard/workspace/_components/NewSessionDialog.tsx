'use client';

// NewSessionDialog —— 「新建会话」对话框：选 agent + 选工作文件夹（对齐 DSH 选择工作区）
//
// 文件夹浏览器锚在部署工作区根（GET /api/workspace/folders）：
//   进入子目录下钻、返回上级、或选「自动（默认目录）」（folder=null → 会话自动
//   落在 <root>/<session_id>）。创建走 POST /api/workspace/sessions（后端预置
//   session cwd + seed，首次对话即绑定所选文件夹）。

import { useEffect, useState } from 'react';
import { ChevronRight, Folder, FolderOpen, ArrowUp, X, Loader2 } from 'lucide-react';
import { W, WS } from '../theme';
import { browseWorkspaceFolders, createWorkspaceSession, type CreatedSession, type FileEntry } from './useWorkspace';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Props {
  onClose: () => void;
  onCreated: (s: CreatedSession) => void;
}

export default function NewSessionDialog({ onClose, onCreated }: Props) {
  const [agents, setAgents] = useState<{ key: string; name: string }[]>([]);
  const [agentKey, setAgentKey] = useState('');
  const [segments, setSegments] = useState<string[]>([]);   // 当前浏览路径（相对根）
  const [dirs, setDirs] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [creating, setCreating] = useState(false);
  const [useAuto, setUseAuto] = useState(true);             // 「自动（默认目录）」开关

  const curPath = segments.join('/');

  useEffect(() => {
    fetch(`${API}/api/agents`, { headers: { 'X-Tenant-ID': '1' } })
      .then(r => r.json())
      .then((list: any[]) => {
        const items = (Array.isArray(list) ? list : []).map((a: any) => ({ key: a.key, name: a.name || a.key }));
        setAgents(items);
        if (items.length) setAgentKey(items[0].key);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true); setErr('');
    browseWorkspaceFolders(curPath)
      .then(d => { if (!cancelled) setDirs(d.entries); })
      .catch((e: any) => { if (!cancelled) setErr(e?.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [curPath]);

  function enter(name: string) {
    setSegments(prev => [...prev, name]);
  }

  function goUp() {
    setSegments(prev => prev.slice(0, -1));
  }

  async function handleCreate() {
    if (!agentKey || creating) return;
    setCreating(true); setErr('');
    try {
      const folder = useAuto ? null : curPath || null;
      const s = await createWorkspaceSession(agentKey, folder);
      onCreated(s);
    } catch (e: any) {
      setErr(e?.message || String(e));
      setCreating(false);
    }
  }

  return (
    <div onClick={onClose} style={{
      position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.55)', zIndex: 1000,
      display: 'flex', alignItems: 'center', justifyContent: 'center', fontFamily: W.font,
    }}>
      <div onClick={e => e.stopPropagation()} style={{
        width: 560, maxHeight: '82vh', display: 'flex', flexDirection: 'column',
        background: W.panel, border: `1px solid ${W.border}`, borderRadius: 14,
        boxShadow: '0 20px 60px rgba(0,0,0,0.5)', overflow: 'hidden',
      }}>
        {/* 标题 */}
        <div style={{ display: 'flex', alignItems: 'center', padding: `${WS.base}px ${WS.xl}px`, borderBottom: `1px solid ${W.border}` }}>
          <span style={{ fontSize: 15, fontWeight: 600, color: W.text }}>新建会话</span>
          <div style={{ flex: 1 }} />
          <button onClick={onClose} style={{ background: 'none', border: 'none', cursor: 'pointer', color: W.secondary, padding: 2 }}>
            <X size={16} />
          </button>
        </div>

        <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `${WS.base}px ${WS.xl}px`, display: 'flex', flexDirection: 'column', gap: WS.base }}>
          {/* Agent 选择 */}
          <label style={{ fontSize: 13, fontWeight: 500, color: W.secondary, display: 'flex', flexDirection: 'column', gap: WS.xs }}>
            Agent
            <select value={agentKey} onChange={e => setAgentKey(e.target.value)}
              style={{ padding: '8px 10px', borderRadius: 8, border: `1px solid ${W.border}`, background: W.surface, color: W.text, fontSize: 13, outline: 'none', fontFamily: 'inherit' }}>
              {agents.map(a => <option key={a.key} value={a.key}>{a.name}</option>)}
            </select>
          </label>

          {/* 文件夹选择 */}
          <div style={{ display: 'flex', flexDirection: 'column', gap: WS.xs }}>
            <span style={{ fontSize: 13, fontWeight: 500, color: W.secondary }}>工作文件夹</span>

            {/* 面包屑 + 上级 */}
            <div style={{ display: 'flex', alignItems: 'center', gap: WS.xs, padding: '6px 10px', borderRadius: 8, background: W.surface, fontSize: 12.5, color: W.text, flexWrap: 'wrap' }}>
              <button onClick={goUp} disabled={segments.length === 0} title="返回上级"
                style={{ background: 'none', border: 'none', cursor: segments.length ? 'pointer' : 'not-allowed', color: W.secondary, padding: 2, display: 'flex', opacity: segments.length ? 1 : 0.4 }}>
                <ArrowUp size={14} />
              </button>
              <span style={{ color: W.tertiary }}>根</span>
              {segments.map((s, i) => (
                <span key={i} style={{ display: 'flex', alignItems: 'center', gap: WS.xs }}>
                  <ChevronRight size={12} color={W.tertiary} />
                  <span style={{ color: W.text }}>{s}</span>
                </span>
              ))}
            </div>

            {/* 目录列表 */}
            <div style={{ maxHeight: 240, overflowY: 'auto', border: `1px solid ${W.border}`, borderRadius: 8, background: W.bg }}>
              {loading && <div style={{ padding: WS.xl, textAlign: 'center', color: W.tertiary, fontSize: 12 }}>加载中...</div>}
              {!loading && dirs.length === 0 && !err && (
                <div style={{ padding: `${WS.xl}px ${WS.base}px`, textAlign: 'center', color: W.tertiary, fontSize: 12 }}>
                  {curPath ? '没有子目录' : '配置的写根（SANDBOX_WRITE_ROOTS）里还没有子目录'}
                </div>
              )}
              {dirs.map(d => (
                <div key={d.name} onClick={() => enter(d.name)}
                  style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '7px 12px', cursor: 'pointer', fontSize: 13, color: W.text }}>
                  <Folder size={14} color="#6B7688" />
                  <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                  <ChevronRight size={13} color={W.tertiary} />
                </div>
              ))}
            </div>

            {/* 自动 / 使用当前 */}
            <div style={{ display: 'flex', flexDirection: 'column', gap: WS.xs }}>
              <div onClick={() => setUseAuto(true)} style={{
                display: 'flex', alignItems: 'center', gap: WS.sm, padding: '8px 12px', borderRadius: 8,
                cursor: 'pointer', border: useAuto ? `1px solid ${W.accent}` : `1px solid ${W.border}`,
                background: useAuto ? 'rgba(77,107,254,0.08)' : 'transparent', fontSize: 13, color: W.text,
              }}>
                <FolderOpen size={14} color={useAuto ? W.accent : W.tertiary} />
                <span style={{ flex: 1 }}>自动（默认目录）</span>
                <span style={{ fontSize: 11, color: W.tertiary }}>会话工作区落在 {'<root>/<session_id>'}</span>
              </div>
              <div onClick={() => setUseAuto(false)} style={{
                display: 'flex', alignItems: 'center', gap: WS.sm, padding: '8px 12px', borderRadius: 8,
                cursor: 'pointer', border: !useAuto ? `1px solid ${W.accent}` : `1px solid ${W.border}`,
                background: !useAuto ? 'rgba(77,107,254,0.08)' : 'transparent', fontSize: 13, color: W.text,
              }}>
                <Folder size={14} color={!useAuto ? W.accent : W.tertiary} />
                <span style={{ flex: 1 }}>使用当前文件夹</span>
                <span style={{ fontSize: 11, color: W.tertiary, maxWidth: 200, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                  {curPath || '（根）'}
                </span>
              </div>
            </div>
          </div>

          {err && <div style={{ fontSize: 12, color: W.danger }}>{err}</div>}
        </div>

        {/* 底部按钮 */}
        <div style={{ display: 'flex', gap: WS.sm, padding: `${WS.base}px ${WS.xl}px`, borderTop: `1px solid ${W.border}` }}>
          <button onClick={onClose} style={{
            padding: '8px 18px', borderRadius: 8, border: `1px solid ${W.border}`, background: 'transparent',
            color: W.secondary, fontSize: 13, cursor: 'pointer', fontFamily: 'inherit',
          }}>取消</button>
          <button onClick={handleCreate} disabled={!agentKey || creating} style={{
            padding: '8px 18px', borderRadius: 8, border: 'none', cursor: creating ? 'not-allowed' : 'pointer',
            background: W.accent, color: '#fff', fontSize: 13, opacity: creating ? 0.6 : 1,
            display: 'flex', alignItems: 'center', gap: WS.sm, fontFamily: 'inherit',
          }}>
            {creating && <Loader2 size={14} className="ws-spin" />}
            {creating ? '创建中...' : '创建'}
          </button>
          <style>{`@keyframes wsSpin{to{transform:rotate(360deg)}} .ws-spin{animation:wsSpin 1s linear infinite}`}</style>
        </div>
      </div>
    </div>
  );
}
