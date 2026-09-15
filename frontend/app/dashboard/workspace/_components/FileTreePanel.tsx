'use client';

// FileTreePanel —— 右栏「文件」：懒加载递归文件树（对齐 DSH 右侧文件树）
//
// 目录优先、懒加载（点开才 GET files?path=）、展开状态按会话保留、
// truncated 标志诚实显示（后端 2000 条截断）。
//
// 新建：工具栏那两个按钮作用在**根目录**，目录行 hover 时浮现的两个按钮作用在
// **那一行**。树是多路懒加载的，不存在唯一的「当前目录」—— 用「最后点过的目录」
// 当默认在某些操作序列下会反直觉；「hover 谁就在谁里面建」才是资源管理器的肌肉
// 记忆。两者共用同一条内联输入行（渲染在目标目录那一条的下方）。
// 会话工作区落在读根上时后端一律拒写，由 GET files 的 writable 字段提前禁用
// 入口 —— 让用户点一个必然 400 的按钮是最差的一种诚实。

import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { ChevronRight, ChevronDown, Check, X, FileText, FileCode, FileImage, Folder, FolderOpen, FilePlus, FolderPlus, RefreshCw, Search, File } from 'lucide-react';
import { W, R, WS } from '../theme';
import { type EntryKind, type FileEntry, createWorkspaceEntry, fetchDir } from './useWorkspace';

interface Props {
  sessionId: string | null;
  refreshSignal: number;              // 每轮对话结束 +1 → 已加载目录全部重载
  selectedPath: string | null;
  onSelectFile: (path: string, entry: FileEntry) => void;
}

const joinPath = (dir: string, name: string) => (dir ? `${dir}/${name}` : name);

function fileIcon(entry: FileEntry, open: boolean) {
  if (entry.is_dir) return open ? <FolderOpen size={15} color={W.dimmed} /> : <Folder size={15} color={W.dimmed} />;
  const ext = entry.name.split('.').pop()?.toLowerCase() || '';
  if (['png', 'jpg', 'jpeg', 'gif', 'webp', 'bmp', 'svg'].includes(ext)) return <FileImage size={14} color={W.tertiary} />;
  if (['js', 'ts', 'tsx', 'jsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'css', 'html', 'sh', 'sql', 'json', 'yaml', 'yml', 'toml'].includes(ext)) return <FileCode size={14} color={W.tertiary} />;
  if (['md', 'txt', 'log', 'csv'].includes(ext)) return <FileText size={14} color={W.tertiary} />;
  return <File size={14} color={W.tertiary} />;
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
  // 新建：creating 记「在哪个目录建、建什么」，输入行就渲染在那个目录的下一条
  const [creating, setCreating] = useState<{ dir: string; kind: EntryKind } | null>(null);
  const [newName, setNewName] = useState('');
  const [createErr, setCreateErr] = useState('');
  const [busy, setBusy] = useState(false);
  const [writable, setWritable] = useState(true);   // 读根当工作区时为 false，新建入口整个禁用
  const createInputRef = useRef<HTMLInputElement>(null);

  const expandedRef = useRef<Set<string>>(new Set());
  const childrenRef = useRef<Record<string, FileEntry[]>>({});

  const load = useCallback(async (path: string, force = false) => {
    if (!sessionId) return;
    if (!force && childrenRef.current[path] !== undefined) return;   // 已有缓存
    setLoading(true);
    try {
      const { entries, truncated: t, writable: w } = await fetchDir(sessionId, path);
      childrenRef.current = { ...childrenRef.current, [path]: entries };
      if (path === '') setRoot(entries); else setChildren(childrenRef.current);
      setTruncated(prev => ({ ...prev, [path]: t }));
      // 整棵树共用同一个可写标志，只在根上取（见后端 api_list_files 的 docstring）
      if (path === '') setWritable(w);
      // 曾经 404 过的会话（工作区目录还没被沙箱建出来）成功加载后要把旧错误擦掉，
      // 否则「新建第一个文件」之后树上还挂着一条吓人的红字
      setErr('');
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
    cancelCreate();
    setWritable(true);
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

  // ── 新建 ──

  function openCreate(dir: string, kind: EntryKind) {
    setCreating({ dir, kind });
    setNewName('');
    setCreateErr('');
  }

  function cancelCreate() {
    setCreating(null);
    setNewName('');
    setCreateErr('');
  }

  // 输入行挂上就聚焦。不用 autoFocus：那是挂载期行为，同一行在切换 kind 时不会重挂
  useEffect(() => { if (creating) createInputRef.current?.focus(); }, [creating]);

  async function submitCreate() {
    if (!creating || !sessionId) return;
    const { dir, kind } = creating;
    const name = newName.trim();
    if (!name || busy) return;
    setBusy(true); setCreateErr('');
    try {
      await createWorkspaceEntry(sessionId, dir, name, kind);
      setCreating(null); setNewName('');
      // 过滤词可能让新条目正好被挡在外面 —— 清掉，别让一次成功看起来像失败
      setQ('');
      await load(dir, true);   // force：否则缓存命中，新条目根本不出现
    } catch (e: any) {
      // 输入行保持打开、名字不丢：大多错误（重名）改个名就能重来
      setCreateErr(e?.message || String(e));
    } finally {
      setBusy(false);
    }
  }

  /** 内联新建输入行。depth 与 nodeRow 同源，缩进才对得上。 */
  function createRow(depth: number): ReactNode {
    const isDir = creating?.kind === 'dir';
    const ok = !!newName.trim() && !busy;
    return (
      <div style={{ padding: `2px ${WS.sm}px 2px ${8 + depth * 14}px` }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: 6, height: 26, padding: '0 8px',
          borderRadius: R.sm, background: W.bg, boxShadow: `inset 0 0 0 0.5px ${W.borderSoft}`,
        }}>
          {isDir ? <FolderPlus size={12} color={W.dimmed} /> : <FilePlus size={12} color={W.dimmed} />}
          <input
            ref={createInputRef}
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => {
              if (e.key === 'Enter') { e.preventDefault(); submitCreate(); }
              else if (e.key === 'Escape') { e.preventDefault(); cancelCreate(); }
            }}
            placeholder={isDir ? '新建文件夹名' : '新建文件名'}
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 12.5, fontFamily: 'inherit' }} />
          <button onClick={submitCreate} disabled={!ok} title="确定"
            className="ws-btn ws-round"
            style={{ padding: 1, flexShrink: 0, display: 'flex', color: ok ? W.accent : W.dimmed }}>
            <Check size={13} />
          </button>
          <button onClick={cancelCreate} title="取消" className="ws-btn ws-round"
            style={{ padding: 1, flexShrink: 0, display: 'flex', color: W.secondary }}>
            <X size={13} />
          </button>
        </div>
        {createErr && <div style={{ color: W.danger, fontSize: 11, padding: '3px 2px 1px' }}>{createErr}</div>}
      </div>
    );
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
          {/* 输入行放在 kids **之前**：目录收着、或还没加载过，照样能新建 ——
              不必为了建个东西先点开它 */}
          {creating?.dir === path && createRow(depth + 1)}
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
        className="ws-item ws-hover-host" data-active={active}
        style={{
          display: 'flex', alignItems: 'center', gap: 6, padding: `4px ${WS.sm}px 4px ${8 + depth * 14}px`,
          borderRadius: R.xs, cursor: e.is_dir && e.outside ? 'not-allowed' : 'pointer',
          color: W.text, fontSize: 13,
          whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
          opacity: e.outside ? 0.45 : 1,
        }}>
        {e.is_dir ? (open ? <ChevronDown size={13} color={W.tertiary} /> : <ChevronRight size={13} color={W.tertiary} />) : <span style={{ width: 13, flexShrink: 0 }} />}
        {fileIcon(e, open)}
        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis' }}>{e.name}</span>
        {!e.is_dir && <span style={{ fontSize: 10, color: W.tertiary, flexShrink: 0 }}>{e.size > 1024 ? `${(e.size / 1024).toFixed(1)}K` : `${e.size}B`}</span>}
        {/* 目录行：hover 才浮现的新建入口。stopPropagation 不可省 —— 事件会冒泡到
            行上的 onClick，点一下「+」顺带把目录折叠了。 */}
        {e.is_dir && !e.outside && writable && (
          <>
            <button onClick={ev => { ev.stopPropagation(); openCreate(path, 'file'); }}
              title="在此文件夹新建文件" className="ws-btn ws-round ws-on-hover" style={hoverBtn}>
              <FilePlus size={12} />
            </button>
            <button onClick={ev => { ev.stopPropagation(); openCreate(path, 'dir'); }}
              title="在此文件夹新建文件夹" className="ws-btn ws-round ws-on-hover" style={hoverBtn}>
              <FolderPlus size={12} />
            </button>
          </>
        )}
      </div>
    );
  }

  if (!sessionId) {
    return (
      <div style={{ padding: WS.xl, textAlign: 'center', color: W.dimmed, fontSize: 13, fontFamily: W.font }}>
        选择左侧会话后查看它的工作区文件
      </div>
    );
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontFamily: W.font }}>
      {/* 工具栏：过滤 + 占位写操作 + 刷新 + 折叠 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: WS.xs, padding: `${WS.sm}px ${WS.md}px`, borderBottom: `0.5px solid ${W.borderSoft}` }}>
        <div style={{
          flex: 1, display: 'flex', alignItems: 'center', gap: WS.xs, height: 28, padding: '0 8px',
          borderRadius: R.sm, background: W.bg, boxShadow: `inset 0 0 0 0.5px ${W.borderSoft}`, minWidth: 0,
        }}>
          <Search size={12} color={W.dimmed} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="过滤文件" style={{
            flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 12, fontFamily: 'inherit',
          }} />
        </div>
        <button onClick={() => openCreate('', 'file')} disabled={!writable}
          title={writable ? '在根目录新建文件' : '该会话工作区是只读位置，不能新建'}
          className="ws-btn ws-round" style={{ ...iconBtn, opacity: writable ? 1 : 0.4 }}><FilePlus size={14} /></button>
        <button onClick={() => openCreate('', 'dir')} disabled={!writable}
          title={writable ? '在根目录新建文件夹' : '该会话工作区是只读位置，不能新建'}
          className="ws-btn ws-round" style={{ ...iconBtn, opacity: writable ? 1 : 0.4 }}><FolderPlus size={14} /></button>
        <button title="刷新" onClick={refreshTree} className="ws-btn ws-round" style={iconBtn}><RefreshCw size={14} className={loading ? 'ws-spin' : ''} /></button>
        <button title="全部折叠" onClick={collapseAll} className="ws-btn ws-round" style={iconBtn}><ChevronDown size={14} /></button>
      </div>

      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `${WS.sm}px ${WS.sm}px ${WS.base}px` }}>
        {err && <div style={{ padding: WS.sm, color: W.danger, fontSize: 12 }}>{err}</div>}
        {!err && root === null && <div style={{ padding: WS.xl, textAlign: 'center', color: W.tertiary, fontSize: 12 }}>加载中...</div>}
        {/* 根目录的输入行**不能**放在 root !== null 里面：新会话的工作区目录还没被
            沙箱建出来时 GET files 是 404、root 一直是 null —— 而那正是最需要「先建个
            文件」的场景。挡在里面会让工具栏那个 + 点下去毫无反应。 */}
        {creating?.dir === '' && createRow(0)}
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
  width: 26, height: 26, borderRadius: 999, flexShrink: 0,
  color: W.secondary, display: 'flex', alignItems: 'center', justifyContent: 'center',
};

/** 目录行 hover 浮现的新建按钮：比工具栏那对更小，免得挤掉文件名 */
const hoverBtn: React.CSSProperties = {
  width: 20, height: 20, borderRadius: 999, flexShrink: 0,
  color: W.secondary, display: 'flex', alignItems: 'center', justifyContent: 'center',
};
