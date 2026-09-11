'use client';

/**
 * 工作区浏览器 —— 会话工作区的**只读**视图
 *
 * 布局对齐 DeepSeek Harness 的官方形态（不是插件那种把聊天挤到右边的三栏）：
 *   左 Sidebar —— 按 agent 分组的会话列表
 *   中央内容面板 —— 与 Agent 的**对话**（主区）
 *   右 Sidebar —— 工作区文件：上递归文件树（目录优先、可展开）/ 下预览
 *
 * 对话在中间是刻意的：工作区的意义是「模型跑出来的东西」，聊天是主任务，
 * 文件面板是随时可看的旁路。
 *
 * 只读是刻意的：工作区唯一的写方是沙箱里的模型。这里没有删除/重命名/上传 ——
 * 「浏览器删文件」和「模型正在写文件」之间没有任何同步手段。
 *
 * 类型判定一律**听服务端的**：预览请求回来是 `image/*` 就按图片渲染，是
 * `text/plain` 就当文本。前端不按扩展名猜，因为扩展名是模型起的。
 */

import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  AlertTriangle, ChevronDown, ChevronRight, Download, FileText, Folder,
  FolderOpen, LayoutGrid, Link2, Loader2, RefreshCw, Search, TreePine,
} from 'lucide-react';
import WorkspaceChat from './_components/WorkspaceChat';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import hljs from 'highlight.js/lib/core';
import bash from 'highlight.js/lib/languages/bash';
import c from 'highlight.js/lib/languages/c';
import cpp from 'highlight.js/lib/languages/cpp';
import css from 'highlight.js/lib/languages/css';
import go from 'highlight.js/lib/languages/go';
import ini from 'highlight.js/lib/languages/ini';
import java from 'highlight.js/lib/languages/java';
import javascript from 'highlight.js/lib/languages/javascript';
import json from 'highlight.js/lib/languages/json';
import markdown from 'highlight.js/lib/languages/markdown';
import python from 'highlight.js/lib/languages/python';
import rust from 'highlight.js/lib/languages/rust';
import sql from 'highlight.js/lib/languages/sql';
import typescript from 'highlight.js/lib/languages/typescript';
import xml from 'highlight.js/lib/languages/xml';
import yaml from 'highlight.js/lib/languages/yaml';
import 'highlight.js/styles/github.css';
import './workspace.css';
import { T, S } from '@/app/theme';

for (const [name, lang] of Object.entries({
  bash, c, cpp, css, go, ini, java, javascript, json, markdown,
  python, rust, sql, typescript, xml, yaml,
})) hljs.registerLanguage(name, lang);

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;
const HEADERS = { 'X-Tenant-ID': String(TENANT_ID) };

/** 非 agent 通道（xianyu / test …）的会话归到这一组 */
const OTHER_GROUP = '__other__';

// ── 类型 ──

interface SessionItem {
  session_id: string;
  /** 归属 agent（后端从 channel 的 `agent:<key>` 解出）；非 agent 通道为 null */
  agent_key: string | null;
  /** 右栏续聊用：带上它后端才不会每轮新建 conversation */
  conversation_id: number | null;
  label: string;
  channel: string | null;
  updated_at: string | null;
  entries: number;
}

interface Entry {
  name: string;
  is_dir: boolean;
  size: number;
  mtime: string | null;
  is_link: boolean;
  /** 指向工作区之外的符号链接：不可进入（后端也会拒），列表里照实显示 */
  outside: boolean;
}

type Preview =
  | { state: 'idle' }
  | { state: 'loading' }
  | { state: 'image'; url: string; mime: string }
  | { state: 'text'; text: string; name: string }
  | { state: 'error'; message: string; code: number };

// ── 格式化 / 工具 ──

function fmtSize(n: number): string {
  if (n < 1024) return `${n} B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)} KB`;
  if (n < 1024 ** 3) return `${(n / 1024 ** 2).toFixed(1)} MB`;
  return `${(n / 1024 ** 3).toFixed(2)} GB`;
}

function fmtTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '';
  return d.toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' });
}

async function errorDetail(r: Response): Promise<string> {
  try {
    const j = await r.json();
    if (typeof j?.detail === 'string') return j.detail;
  } catch { /* 非 JSON 响应：退回状态码 */ }
  return `请求失败（HTTP ${r.status}）`;
}

const fileUrl = (sid: string, path: string, params: Record<string, string> = {}) =>
  `${API}/api/workspace/${encodeURIComponent(sid)}/file?${new URLSearchParams({ path, ...params })}`;

const treeKey = (sid: string, rel: string) => sid + TREE_SEP + rel;
/** 树节点 / 展开状态的键：带上 session，切会话再切回来能保住展开状态。
 *  分隔符用 NUL —— 模型起的文件名里可以有空格，拿空格当分隔符就拆不回来了。
 *  用 fromCharCode 而不是字面量转义序列：源码里塞裸 NUL 会让编辑器、diff、
 *  grep 全部失能。 */
const TREE_SEP = String.fromCharCode(0);
const childPath = (parent: string, name: string) => (parent ? `${parent}/${name}` : name);

const extOf = (name: string) => (name.includes('.') ? name.slice(name.lastIndexOf('.') + 1).toLowerCase() : '');

/** 扩展名 → highlight.js 语言。只用于**怎么高亮**，不用于判断能不能预览。 */
const HLJS_LANG: Record<string, string> = {
  py: 'python', js: 'javascript', jsx: 'javascript', mjs: 'javascript', cjs: 'javascript',
  ts: 'typescript', tsx: 'typescript', json: 'json', sh: 'bash', bash: 'bash', zsh: 'bash',
  yml: 'yaml', yaml: 'yaml', html: 'xml', htm: 'xml', xml: 'xml', svg: 'xml', css: 'css',
  sql: 'sql', go: 'go', rs: 'rust', java: 'java', c: 'c', h: 'c', cpp: 'cpp', cc: 'cpp', hpp: 'cpp',
  toml: 'ini', ini: 'ini', conf: 'ini', md: 'markdown', markdown: 'markdown',
};

const isMarkdown = (name: string) => ['md', 'markdown', 'mdx'].includes(extOf(name));

// ── 页面 ──

export default function WorkspacePage() {
  const [sessions, setSessions] = useState<SessionItem[]>([]);
  const [agentNames, setAgentNames] = useState<Record<string, string>>({});
  const [sessionsLoading, setSessionsLoading] = useState(true);
  const [sessionsError, setSessionsError] = useState<string | null>(null);
  const [activeSession, setActiveSession] = useState<string | null>(null);
  const [collapsedAgents, setCollapsedAgents] = useState<Set<string>>(new Set());

  // 树：键带 session（见 treeKey），所以切会话再切回来展开状态还在
  const [tree, setTree] = useState<Record<string, Entry[]>>({});
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [loadingDirs, setLoadingDirs] = useState<Set<string>>(new Set());
  const [treeError, setTreeError] = useState<Record<string, string>>({});
  const [reloadKey, setReloadKey] = useState(0);

  const [selected, setSelected] = useState<{ entry: Entry; path: string } | null>(null);
  const [preview, setPreview] = useState<Preview>({ state: 'idle' });
  const [notice, setNotice] = useState<string | null>(null);
  const [filter, setFilter] = useState('');

  const activeMeta = sessions.find(s => s.session_id === activeSession) || null;

  // ── 会话列表 + agent 花名册 ──

  const loadSessions = useCallback(async (silent = false) => {
    if (!silent) setSessionsLoading(true);
    setSessionsError(null);
    try {
      const [sessR, agentsR] = await Promise.all([
        fetch(`${API}/api/workspace/sessions`, { headers: HEADERS }),
        fetch(`${API}/api/agents`).catch(() => null),
      ]);
      if (!sessR.ok) throw new Error(await errorDetail(sessR));
      const d = await sessR.json();
      const items: SessionItem[] = d.items || [];
      setSessions(items);
      setActiveSession(prev => prev ?? (items[0]?.session_id ?? null));

      if (agentsR?.ok) {
        const list = await agentsR.json().catch(() => []);
        const m: Record<string, string> = {};
        for (const a of list || []) if (a?.key) m[a.key] = a.name || a.key;
        setAgentNames(m);
      }
    } catch (e) {
      if (!silent) setSessionsError(e instanceof Error ? e.message : '加载失败');
    } finally {
      if (!silent) setSessionsLoading(false);
    }
  }, []);

  useEffect(() => { loadSessions(); }, [loadSessions]);

  const groups = useMemo(() => {
    const m = new Map<string, SessionItem[]>();
    for (const s of sessions) {
      const k = s.agent_key || OTHER_GROUP;
      const arr = m.get(k);
      if (arr) arr.push(s); else m.set(k, [s]);
    }
    return [...m.entries()].map(([key, items]) => ({ key, items }));
  }, [sessions]);

  // ── 目录加载 ──

  const loadDir = useCallback(async (sid: string, rel: string) => {
    const k = treeKey(sid, rel);
    setLoadingDirs(p => new Set(p).add(k));
    setTreeError(p => { const n = { ...p }; delete n[k]; return n; });
    try {
      const q = new URLSearchParams({ path: rel });
      const r = await fetch(`${API}/api/workspace/${encodeURIComponent(sid)}/files?${q}`, { headers: HEADERS });
      if (!r.ok) throw new Error(await errorDetail(r));
      const d = await r.json();
      setTree(p => ({ ...p, [k]: d.entries || [] }));
    } catch (e) {
      setTreeError(p => ({ ...p, [k]: e instanceof Error ? e.message : '加载失败' }));
    } finally {
      setLoadingDirs(p => { const n = new Set(p); n.delete(k); return n; });
    }
  }, []);

  // 切会话时只保证根目录已加载（子目录按展开懒加载）
  useEffect(() => {
    if (!activeSession) return;
    setSelected(null);
    setNotice(null);
    setFilter('');
    if (!tree[treeKey(activeSession, '')]) loadDir(activeSession, '');
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeSession, reloadKey, loadDir]);

  /** 镜像一份 tree，让 refreshAll 的身份保持稳定 —— 否则它每存一次目录就换一次
   *  引用，会把右栏聊天组件跟着重渲染一遍。 */
  const treeRef = useRef(tree);
  useEffect(() => { treeRef.current = tree; }, [tree]);

  /** 一轮对话结束后：模型可能刚落盘了文件，会话的「N 项」也变了。
   *  重载**已经加载过**的目录（保住展开状态），而不是整棵树推倒重来。 */
  const refreshAll = useCallback(() => {
    loadSessions(true);
    if (!activeSession) return;
    const prefix = activeSession + TREE_SEP;
    for (const k of Object.keys(treeRef.current)) {
      if (k.startsWith(prefix)) loadDir(activeSession, k.slice(prefix.length));
    }
  }, [activeSession, loadDir, loadSessions]);

  const toggleDir = (sid: string, rel: string) => {
    const k = treeKey(sid, rel);
    setExpanded(prev => {
      const n = new Set(prev);
      if (n.has(k)) n.delete(k);
      else { n.add(k); if (!tree[k]) loadDir(sid, rel); }
      return n;
    });
  };

  // ── 预览 ──

  const imageUrlRef = useRef<string | null>(null);
  useEffect(() => () => { if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current); }, []);

  useEffect(() => {
    if (!activeSession || !selected) { setPreview({ state: 'idle' }); return; }
    let cancelled = false;
    (async () => {
      setPreview({ state: 'loading' });
      try {
        const r = await fetch(fileUrl(activeSession, selected.path, { inline: '1' }), { headers: HEADERS });
        if (cancelled) return;
        if (!r.ok) { setPreview({ state: 'error', message: await errorDetail(r), code: r.status }); return; }

        // 类型听服务端的：它按魔数判，并且明确不允许 SVG
        const ct = r.headers.get('content-type') || '';
        if (ct.startsWith('image/')) {
          const url = URL.createObjectURL(await r.blob());
          if (cancelled) { URL.revokeObjectURL(url); return; }
          if (imageUrlRef.current) URL.revokeObjectURL(imageUrlRef.current);
          imageUrlRef.current = url;
          setPreview({ state: 'image', url, mime: ct });
        } else {
          setPreview({ state: 'text', text: await r.text(), name: selected.entry.name });
        }
      } catch (e) {
        if (!cancelled) setPreview({ state: 'error', message: e instanceof Error ? e.message : '加载失败', code: 0 });
      }
    })();
    return () => { cancelled = true; };
  }, [activeSession, selected]);

  /** 下载走 fetch + blob 而不是 <a download>：请求必须带 X-Tenant-ID 头，
   *  而裸链接带不了自定义头。代价是文件会先在内存里过一遍。 */
  const download = async (path: string, name: string) => {
    if (!activeSession) return;
    setNotice(null);
    try {
      const r = await fetch(fileUrl(activeSession, path), { headers: HEADERS });
      if (!r.ok) { setNotice(await errorDetail(r)); return; }
      const url = URL.createObjectURL(await r.blob());
      const a = document.createElement('a');
      a.href = url; a.download = name;
      document.body.appendChild(a); a.click(); a.remove();
      URL.revokeObjectURL(url);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : '下载失败');
    }
  };

  // ── 渲染 ──

  const rootEntries = activeSession ? tree[treeKey(activeSession, '')] : undefined;

  return (
    <div style={{ display: 'flex', height: '100%', background: T.bg, overflow: 'hidden' }}>

      {/* ── 左：会话（按 agent 分组）—— DSH 的左 Sidebar ── */}
      <div style={{ flex: '0 0 240px', background: T.surface, borderRight: `1px solid ${T.border}`, display: 'flex', flexDirection: 'column' }}>
        <div style={{ padding: `${S.base}px ${S.base}px ${S.sm}px`, fontSize: 13, fontWeight: 600, color: T.text }}>
          会话工作区
        </div>
        <div style={{ flex: 1, overflow: 'auto', padding: `0 ${S.sm}px ${S.base}px`, minHeight: 0 }}>
          {sessionsLoading && <Hint text="加载中…" />}
          {sessionsError && <Hint text={sessionsError} tone="danger" />}
          {!sessionsLoading && !sessionsError && groups.length === 0 && (
            <div style={{ textAlign: 'center', padding: `${S.xxl}px ${S.base}px`, color: T.tertiary }}>
              <LayoutGrid size={26} style={{ marginBottom: S.sm, opacity: .55 }} />
              <div style={{ fontSize: 13, color: T.secondary }}>还没有工作区</div>
              <div style={{ fontSize: 11, marginTop: S.xs, lineHeight: 1.6 }}>
                让 Agent 用一次 bash / python，<br />工作区会自动建好
              </div>
            </div>
          )}

          {groups.map(({ key, items }) => {
            const collapsed = collapsedAgents.has(key);
            return (
              <div key={key} style={{ marginBottom: S.xs }}>
                <div
                  onClick={() => setCollapsedAgents(prev => {
                    const n = new Set(prev);
                    n.has(key) ? n.delete(key) : n.add(key);
                    return n;
                  })}
                  style={{
                    display: 'flex', alignItems: 'center', gap: 5, padding: `${S.xs}px ${S.sm}px`,
                    cursor: 'pointer', borderRadius: 6, color: T.secondary,
                  }}
                >
                  {collapsed ? <ChevronRight size={12} /> : <ChevronDown size={12} />}
                  <span style={{ flex: 1, fontSize: 11, fontWeight: 600, letterSpacing: .3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                    {key === OTHER_GROUP ? '其它通道' : (agentNames[key] || key)}
                  </span>
                  <span style={{ fontSize: 10, color: T.tertiary }}>{items.length}</span>
                </div>

                {!collapsed && items.map(s => {
                  const active = s.session_id === activeSession;
                  return (
                    <div
                      key={s.session_id}
                      onClick={() => setActiveSession(s.session_id)}
                      style={{
                        padding: `${S.sm}px ${S.md}px`, borderRadius: 6, cursor: 'pointer', marginBottom: 1,
                        background: active ? T.accentBg : 'transparent',
                      }}
                    >
                      <div style={{
                        fontSize: 12, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace',
                        fontWeight: active ? 600 : 400, color: active ? T.accent : T.text,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                      }}>
                        {s.session_id.slice(0, 12)}
                      </div>
                      <div style={{ fontSize: 10, color: T.tertiary, marginTop: 2 }}>
                        {s.entries} 项 · {fmtTime(s.updated_at)}
                      </div>
                    </div>
                  );
                })}
              </div>
            );
          })}
        </div>
      </div>

      {/* ── 中：对话（DSH 的中央内容面板）──
          三个宽度都用「可收缩的 basis + 下限」，不用固定宽度：本页在应用左导航
          （220px）之内，1366 及以下的笔记本上固定 480 会把主区挤成一条。 */}
      <div style={{ flex: '1 1 420px', minWidth: 320, display: 'flex', flexDirection: 'column', background: T.surface, borderRight: `1px solid ${T.border}` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, padding: `${S.md}px ${S.base}px`, borderBottom: `1px solid ${T.border}`, minHeight: 22, flexShrink: 0 }}>
          <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 500, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
            {activeMeta?.agent_key ? (agentNames[activeMeta.agent_key] || activeMeta.agent_key) : '对话'}
          </span>
          {activeSession && (
            <span style={{ fontSize: 11, color: T.tertiary, fontFamily: 'ui-monospace, SFMono-Regular, Menlo, monospace', flexShrink: 0 }}>
              {activeSession.slice(0, 8)}
            </span>
          )}
        </div>
        <WorkspaceChat
          agentKey={activeMeta?.agent_key ?? null}
          sessionId={activeSession}
          conversationId={activeMeta?.conversation_id ?? null}
          onTurnDone={refreshAll}
        />
      </div>

      {/* ── 右：工作区（DSH 的右 Sidebar）—— 上文件树 / 下预览 ── */}
      <div style={{ flex: '0 1 460px', minWidth: 300, display: 'flex', flexDirection: 'column', background: T.surface, minHeight: 0 }}>

        {/* 文件树 */}
        <div style={{ flex: '0 0 42%', display: 'flex', flexDirection: 'column', borderBottom: `1px solid ${T.border}`, minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: S.xs, padding: `${S.sm}px ${S.md}px`, borderBottom: `1px solid ${T.border}`, flexShrink: 0 }}>
            <TreePine size={13} color={T.tertiary} style={{ flexShrink: 0 }} />
            <span style={{ fontSize: 12, fontWeight: 600, color: T.text, flexShrink: 0 }}>文件</span>
            <Search size={12} color={T.tertiary} style={{ flexShrink: 0, marginLeft: S.sm }} />
            <input
              value={filter}
              onChange={e => setFilter(e.target.value)}
              placeholder="过滤当前目录"
              style={{ flex: 1, minWidth: 0, border: 'none', outline: 'none', fontSize: 12, color: T.text, background: 'transparent', fontFamily: 'inherit' }}
            />
            {activeSession && (
              <button title="刷新" onClick={() => { setTree({}); setReloadKey(k => k + 1); }} style={iconBtn}>
                <RefreshCw size={13} />
              </button>
            )}
          </div>

          {notice && (
            <div style={{ padding: `${S.sm}px ${S.md}px`, background: '#FFF3E8', color: T.warning, fontSize: 12, flexShrink: 0 }}>
              {notice}
            </div>
          )}

          <div style={{ flex: 1, overflow: 'auto', padding: S.xs, minHeight: 0 }}>
            {!activeSession && <Center text="先选一个会话" sub="在左栏里选" />}
            {activeSession && !rootEntries && !loadingDirs.has(treeKey(activeSession, '')) && (
              treeError[treeKey(activeSession, '')]
                ? <Center icon={<AlertTriangle size={20} color={T.danger} />} text={treeError[treeKey(activeSession, '')]!} />
                : <Hint text="加载中…" />
            )}
            {activeSession && rootEntries?.length === 0 && (
              <Center text="这个工作区是空的" sub="模型还没有往这里写东西" />
            )}
            {activeSession && rootEntries && rootEntries.length > 0 && (
              <FileTree
                sid={activeSession}
                relPath=""
                depth={0}
                tree={tree}
                expanded={expanded}
                loadingDirs={loadingDirs}
                errors={treeError}
                selectedPath={selected?.path ?? null}
                filter={filter}
                onToggle={toggleDir}
                onSelect={(entry, path) => setSelected({ entry, path })}
              />
            )}
          </div>
        </div>

        {/* 预览 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', minHeight: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: S.md, padding: `${S.md}px ${S.base}px`, borderBottom: `1px solid ${T.border}`, minHeight: 22, flexShrink: 0 }}>
            <span style={{ flex: 1, minWidth: 0, fontSize: 13, fontWeight: 500, color: T.text, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {selected?.entry.name || '预览'}
            </span>
            {selected && (
              <button onClick={() => download(selected.path, selected.entry.name)} style={{ ...ghostBtn, display: 'flex', alignItems: 'center', gap: 6 }}>
                <Download size={13} /> 下载
              </button>
            )}
          </div>

          <div style={{ flex: 1, overflow: 'auto', minHeight: 0 }}>
            {preview.state === 'idle' && <Center text="选一个文件看看内容" sub="只读预览，不支持编辑" />}
            {preview.state === 'loading' && (
              <div style={{ display: 'flex', justifyContent: 'center', padding: S.huge, color: T.tertiary }}>
                <Loader2 size={18} className="ws-spin" />
              </div>
            )}
            {preview.state === 'error' && (
              <Center
                icon={<AlertTriangle size={22} color={preview.code === 415 || preview.code === 413 ? T.warning : T.danger} />}
                text={preview.message}
                sub={preview.code === 415 || preview.code === 413 ? '用右上角的「下载」取走它' : undefined}
              />
            )}
            {preview.state === 'image' && (
              <div style={{ padding: S.base, display: 'flex', justifyContent: 'center' }}>
                {/* 类型由服务端按魔数判定并显式排除 SVG —— 这里不需要再防一层 */}
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={preview.url} alt={selected?.entry.name || ''} style={{ maxWidth: '100%', borderRadius: 6, border: `1px solid ${T.border}` }} />
              </div>
            )}
            {preview.state === 'text' && (
              isMarkdown(preview.name)
                ? (
                  <div className="ws-prose" style={{ ...proseStyle, ...cssVars }}>
                    {/* 不挂 rehype-raw：模型写的原始 HTML 一律当文本，不渲染成 DOM */}
                    <ReactMarkdown remarkPlugins={[remarkGfm]} components={MD_COMPONENTS}>
                      {preview.text}
                    </ReactMarkdown>
                  </div>
                )
                : <CodeView text={preview.text} name={preview.name} />
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

// ── 文件树 ──

interface TreeProps {
  sid: string;
  relPath: string;
  depth: number;
  tree: Record<string, Entry[]>;
  expanded: Set<string>;
  loadingDirs: Set<string>;
  errors: Record<string, string>;
  selectedPath: string | null;
  filter: string;
  onToggle: (sid: string, rel: string) => void;
  onSelect: (entry: Entry, path: string) => void;
}

function FileTree(props: TreeProps) {
  const { sid, relPath, depth, tree, expanded, loadingDirs, errors, selectedPath, filter, onToggle, onSelect } = props;
  const key = treeKey(sid, relPath);
  const entries = tree[key];
  if (!entries) return null;

  const f = filter.trim().toLowerCase();
  const shown = f ? entries.filter(e => e.name.toLowerCase().includes(f)) : entries;

  if (f && shown.length === 0) {
    return <div style={{ padding: `${S.sm}px ${S.md}px`, fontSize: 11, color: T.tertiary }}>无匹配项</div>;
  }

  return (
    <>
      {shown.map(e => {
        const path = childPath(relPath, e.name);
        const nodeKey = treeKey(sid, path);
        const isOpen = expanded.has(nodeKey);
        const isLoading = loadingDirs.has(nodeKey);
        const isSelected = selectedPath === path;

        return (
          <div key={path}>
            <div
              onClick={() => {
                if (e.outside) return;
                if (e.is_dir) onToggle(sid, path);
                else onSelect(e, path);
              }}
              title={e.outside ? '指向工作区之外的符号链接，已屏蔽' : e.name}
              style={{
                display: 'flex', alignItems: 'center', gap: 4, paddingBlock: 4, paddingRight: S.sm,
                paddingLeft: S.sm + depth * 14,
                borderRadius: 5, cursor: e.outside ? 'not-allowed' : 'pointer',
                background: isSelected ? T.accentBg : 'transparent',
                opacity: e.outside ? .5 : 1,
              }}
              onMouseEnter={ev => { if (!isSelected && !e.outside) ev.currentTarget.style.background = T.hover; }}
              onMouseLeave={ev => { if (!isSelected) ev.currentTarget.style.background = 'transparent'; }}
            >
              <span style={{ width: 12, flexShrink: 0, display: 'flex', color: T.tertiary }}>
                {isLoading ? <Loader2 size={11} className="ws-spin" />
                  : e.is_dir ? (isOpen ? <ChevronDown size={11} /> : <ChevronRight size={11} />)
                  : null}
              </span>
              <span style={{ flexShrink: 0, display: 'flex' }}>
                {e.is_dir
                  ? (isOpen ? <FolderOpen size={14} color={T.accent} /> : <Folder size={14} color={T.accent} />)
                  : e.is_link
                    ? <Link2 size={14} color={e.outside ? T.danger : T.secondary} />
                    : isMarkdown(e.name)
                      ? <FileText size={14} color={T.secondary} />
                      : <FileText size={14} color={T.tertiary} />}
              </span>
              <span style={{
                flex: 1, minWidth: 0, fontSize: 12, color: T.text,
                overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
              }}>
                {e.name}
              </span>
              {e.outside && <span style={{ fontSize: 9, color: T.danger, flexShrink: 0 }}>区外</span>}
              {!e.is_dir && <span style={{ fontSize: 10, color: T.tertiary, flexShrink: 0 }}>{fmtSize(e.size)}</span>}
            </div>

            {e.is_dir && isOpen && (
              errors[nodeKey]
                ? <div style={{ paddingLeft: S.sm + (depth + 1) * 14, paddingBlock: 4, fontSize: 11, color: T.danger }}>{errors[nodeKey]}</div>
                : tree[nodeKey]
                  ? <FileTree {...props} relPath={path} depth={depth + 1} />
                  : null
            )}
          </div>
        );
      })}
    </>
  );
}

// ── 代码高亮 ──

function CodeView({ text, name }: { text: string; name: string }) {
  const lang = HLJS_LANG[extOf(name)];
  const html = useMemo(() => {
    if (!lang || !hljs.getLanguage(lang)) return null;
    try {
      return hljs.highlight(text, { language: lang, ignoreIllegals: true }).value;
    } catch {
      return null;  // 高亮失败不该让预览挂掉，退回纯文本
    }
  }, [text, lang]);

  if (html === null) {
    return <pre style={preStyle}>{text}</pre>;
  }
  // hljs 的输出是它自己生成的转义 HTML（输入已被 escape），不是原始文件内容
  return <pre style={preStyle}><code className="hljs" dangerouslySetInnerHTML={{ __html: html }} /></pre>;
}

// ── 小零件 ──

const iconBtn: React.CSSProperties = {
  border: 'none', background: 'transparent', cursor: 'pointer', color: T.secondary,
  padding: 4, borderRadius: 4, display: 'flex', alignItems: 'center', flexShrink: 0,
};

const ghostBtn: React.CSSProperties = {
  padding: '5px 10px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
  background: T.surface, border: `1px solid ${T.border}`, color: T.text, flexShrink: 0,
};

const preStyle: React.CSSProperties = {
  margin: 0, padding: S.base, fontSize: 12, lineHeight: 1.65, color: T.text,
  fontFamily: 'ui-monospace, SFMono-Regular, Menlo, Consolas, monospace',
  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
};

/** 预览区的 Markdown 排版。刻意不引 typography 插件，几条规则够用。 */
const proseStyle: React.CSSProperties = {
  padding: S.xl, fontSize: 13, lineHeight: 1.75, color: T.text, wordBreak: 'break-word',
};

/** 把主题令牌喂给 workspace.css，免得颜色在 CSS 里被硬编码成第二份真相。
 *  （CSSProperties 不认识自定义属性，这个 cast 是必须的。） */
const cssVars = {
  '--ws-text': T.text,
  '--ws-secondary': T.secondary,
  '--ws-border': T.border,
  '--ws-accent': T.accent,
  '--ws-hover': T.hover,
  '--ws-code-bg': T.hover,
} as unknown as React.CSSProperties;

/** Markdown 里的代码块走 highlight.js；行内代码原样交给 CSS。 */
const MD_COMPONENTS = {
  code({ className, children, ...props }: { className?: string; children?: React.ReactNode }) {
    const m = /language-([\w-]+)/.exec(className || '');
    const lang = m?.[1];
    if (lang && hljs.getLanguage(lang)) {
      try {
        const html = hljs.highlight(String(children), { language: lang, ignoreIllegals: true }).value;
        return <code className="hljs" dangerouslySetInnerHTML={{ __html: html }} />;
      } catch { /* 高亮失败退回纯文本 */ }
    }
    return <code className={className} {...props}>{children}</code>;
  },
};

function Hint({ text, tone }: { text: string; tone?: 'danger' }) {
  return (
    <div style={{ padding: S.base, fontSize: 12, color: tone === 'danger' ? T.danger : T.tertiary, textAlign: 'center' }}>
      {text}
    </div>
  );
}

function Center({ icon, text, sub }: { icon?: React.ReactNode; text: string; sub?: string }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', justifyContent: 'center', gap: S.sm, padding: `${S.huge}px ${S.xl}px`, textAlign: 'center' }}>
      {icon}
      <div style={{ fontSize: 13, color: T.secondary, lineHeight: 1.6 }}>{text}</div>
      {sub && <div style={{ fontSize: 11, color: T.tertiary }}>{sub}</div>}
    </div>
  );
}
