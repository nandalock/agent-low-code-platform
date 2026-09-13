'use client';

// FileTreePanel —— 右栏「文件」：懒加载递归文件树（对齐 DSH 右侧文件树）
//
// 目录优先、懒加载（点开才 GET files?path=）、展开状态按会话保留、
// truncated 标志诚实显示（后端 2000 条截断）。新建文件/文件夹是占位按钮
// （后端只读设计，写入口只有上传）。

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronRight, ChevronDown, FileText, FileCode, FileImage, Folder, FolderOpen, FilePlus, FolderPlus, RefreshCw, Search, File } from 'lucide-react';
import { W, WS } from '../theme';
import { type FileEntry, fetchDir } from './useWorkspace';

interface Props {
  sessionId: string | null;
  refreshSignal: number;              // 每轮对话结束 +1 → 已加载目录全部重载
  selectedPath: string | null;
  onSelectFile: (path: string, entry: FileEntry) => void;
}

const joinPath = (dir: string, name: string) => (dir ? `${dir}/${name}` : name);

function fileIcon(entry: FileEntry, open: boolean) {
  if (entry.is_dir) return open ? <FolderOpen size={15} color="#6B7688" /> : <Folder size={15} color="#6B7688" />;
  const ext = entry.name.split('.').pop()?.toLowerCase() || '';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return <FileImage size={14} color="#8B93A5" />;
  if (['js', 'ts', 'tsx', 'jsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'css', 'html', 'sh', 'sql', 'json', 'yaml', 'yml', 'toml'].includes(ext)) return <FileCode size={14} color="#8B93A5" />;
  if (['md', 'txt', 'log', 'csv'].includes(ext)) return <FileText size={14} color="#8B93A5" />;
  return <File size={14} color="#8B93A5" />;
}

export default function FileTreePanel({ sessionId, refreshSignal, selectedPath, onSelectFile }: Props) {
  // state 供渲染；ref 供逻辑（load/toggle 需要读最新值，避免闭包过期）
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [children, setChildren] = useState<Record<string, FileEntry[]>>({});
  const [root, setRoot] = useState<FileEntry[] | null>(null);
  const [truncated, setTruncated] = useState<Record<string, boolean>>({});
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');
  const [q, setQ] = useState('');

  const expandedRef = useRef<Set<string>>(new Set());
  const childrenRef = useRef<Record<string, FileEntry[]>>({});

  const load = useCallback(async (path: string, force = false) => {
    if (!sessionId) return;
    if (!force && childrenRef.current[path] !== undefined) return;   // 已有缓存
    setLoading(true);
    try {
      const { entries, truncated: t } = await fetchDir(sessionId, path);
      childrenRef.current = { ...childrenRef.current, [path]: entries };
      if (path === '') setRoot(entries); else setChildren(childrenRef.current);
      setTruncated(prev => ({ ...prev, [path]: t }));
    } catch (e: any) {
      setErr(e?.message || String(e));
    } finally {
      setLoading(false);
    }
  }, [sessionId]);

  function setExpandedBoth(next: Set<string>) {
    expandedRef.current = next;
    setExpanded(next);
  }

  // 会话切换：清空并加载根目录
  useEffect(() => {
    childrenRef.current = {};
    setChildren({}); setRoot(null); setTruncated({}); setErr(''); setQ('');
    setExpandedBoth(new Set());
    if (sessionId) load('', true);
  }, [sessionId]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 每轮对话结束：根 + 已展开目录全部失效重载（模型可能写了任何地方），展开状态保留
  useEffect(() => {
    if (!sessionId || refreshSignal <= 0) return;
    load('', true);
    for (const p of Array.from(expandedRef.current)) load(p, true);
  }, [refreshSignal]);   // eslint-disable-line react-hooks/exhaustive-deps

  function toggleDir(path: string) {
    const next = new Set(expandedRef.current);
    if (next.has(path)) {
      next.delete(path);
    } else {
      next.add(path);
      if (childrenRef.current[path] === undefined) load(path);
    }
    setExpandedBoth(next);
  }

  function collapseAll() {
    setExpandedBoth(new Set());
  }

  function refreshTree() {
    childrenRef.current = {};
    setChildren({});
    load('', true);
  }

  const matchQ = (name: string) => !q.trim() || name.toLowerCase().includes(q.trim().toLowerCase());

  // 递归渲染：过滤命中的目录强制展开显示（临时展开，不改持久展开态）
  function renderEntries(entries: FileEntry[], dir: string, depth: number): ReactNode[] {
    const out: ReactNode[] = [];
    for (const e of entries) {
      const path = joinPath(dir, e.name);
      const open = expanded.has(path);
      if (!matchQ(e.name)) {
        // 目录自身不命中，但子树可能命中：下钻找命中项
        if (e.is_dir && !e.outside && open) {
          const kids = renderEntries(childrenRef.current[path] || [], path, depth + 1);
          if (kids.length) out.push(<div key={path}>{nodeRow(e, path, depth, true)}{kids}</div>);
        }
        continue;
      }
      const kids = e.is_dir && !e.outside && open
        ? renderEntries(childrenRef.current[path] || [], path, depth + 1)
        : [];
      out.push(
        <div key={path}>
          {nodeRow(e, path, depth, kids.length > 0)}
          {kids}
        </div>,
      );
    }
    return out;
  }

  function nodeRow(e: FileEntry, path: string, depth: number, open: boolean) {
    const active = path === selectedPath && !e.is_dir;
    return (
      <div onClick={() => (e.is_dir ? (!e.outside && toggleDir(path)) : onSelectFile(path, e))}
        title={e.outside ? '符号链接指向工作区外（不可下钻）' : path}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, padding: `4px ${WS.sm}px 4px ${8 + depth * 14}px`,
          borderRadius: 6, cursor: e.is_dir && e.outside ? 'not-allowed' : 'pointer',
          background: active ? W.active : 'transparent', color: W.text, fontSize: 13,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          opacity: e.outside ? 0.45 : 1,
        }}>
        {e.is_dir ? (open ? <ChevronDown size={13} color={W.tertiary} /> : <ChevronRight size={13} color={W.tertiary} />) : <span style={{ width: 13, flexShrink: 0 }} />}
        {fileIcon(e, open)}
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.name}</span>
        {!e.is_dir && <span style={{ fontSize: 10, color: W.tertiary, flexShrink: 0 }}>{e.size > 1024 ? `${(e.size / 1024).toFixed(1)}K` : `${e.size}B`}</span>}
      </div>
    );
  }

  if (!sessionId) {
    return (
      <div style={{ padding: WS.xl, textAlign: 'center', color: W.tertiary, fontSize: 13, fontFamily: W.font }}>
        选择左侧会话后查看它的工作区文件
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontFamily: W.font }}>
      {/* 工具栏：过滤 + 占位写操作 + 刷新 + 折叠 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: WS.xs, padding: `${WS.sm}px ${WS.base}px`, borderBottom: `1px solid ${W.border}` }}>
        <div style={{ flex: 1, display: 'flex', alignItems: 'center', gap: WS.xs, padding: '4px 8px', borderRadius: 6, background: W.surface, minWidth: 0 }}>
          <Search size={12} color={W.tertiary} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="过滤文件" style={{
            flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 12, fontFamily: 'inherit',
          }} />
        </div>
        <button title="新建文件（后端待支持）" disabled style={{ ...iconBtn, cursor: 'not-allowed', opacity: 0.4 }}><FilePlus size={14} /></button>
        <button title="新建文件夹（后端待支持）" disabled style={{ ...iconBtn, cursor: 'not-allowed', opacity: 0.4 }}><FolderPlus size={14} /></button>
        <button title="刷新" onClick={refreshTree} style={iconBtn}><RefreshCw size={14} className={loading ? 'ws-spin' : ''} /></button>
        <button title="全部折叠" onClick={collapseAll} style={iconBtn}><ChevronDown size={14} /></button>
        <style>{`@keyframes wsSpin{to{transform:rotate(360deg)}} .ws-spin{animation:wsSpin 1s linear infinite}`}</style>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `${WS.sm}px ${WS.sm}px ${WS.base}px` }}>
        {err && <div style={{ padding: WS.sm, color: W.danger, fontSize: 12 }}>{err}</div>}
        {!err && root === null && <div style={{ padding: WS.xl, textAlign: 'center', color: W.tertiary, fontSize: 12 }}>加载中...</div>}
        {root !== null && (
          <>
            {renderEntries(root, '', 0)}
            {root.length === 0 && <div style={{ padding: `${WS.xl}px 0`, textAlign: 'center', color: W.tertiary, fontSize: 12, whiteSpace: 'pre-line' }}>{'工作区还是空的\n发起一次对话，模型写的文件会出现在这里'}</div>}
            {truncated[''] && <div style={{ padding: `${WS.sm}px ${WS.xs}px`, color: W.warning, fontSize: 11 }}>目录过大，已截断显示</div>}
          </>
        )}
      </div>
    </div>
  );
}

const iconBtn: React.CSSProperties = {
  width: 26, height: 26, borderRadius: 6, border: 'none', cursor: 'pointer',
  background: 'transparent', color: W.secondary, display: 'flex', alignItems: 'center', justifyContent: 'center',
};
