'use client';

// Workspace —— 三栏工作区页（对齐 DeepSeek Harness web UI 布局）
//
//  左 280：会话列表（按 agent 分组 + 搜索 + 新建）
//  中 弹性：对话 + 工具时间线 + composer（上传 / 沙箱模式切换 / 发送）
//  右 380：文件树（懒加载）↕ 预览（PDF/图片/MD/代码/文本）
//
// 页面是共享状态的唯一持有者：选中会话、上传、文件树刷新信号、沙箱模式。
// 每轮对话完成 → 刷新侧栏列表（label/时间/文件数变了）+ 文件树（模型落了文件）。
// 「新建会话」是本地草稿（无 conversation/session id），首问 done 后升级为正式会话。

import { useCallback, useEffect, useState } from 'react';
import { W, WS, wsThemeCss } from './theme';
import './workspace.css';
import WorkspaceSidebar from './_components/WorkspaceSidebar';
import WorkspaceChat, { type ActiveSession } from './_components/WorkspaceChat';
import FileTreePanel from './_components/FileTreePanel';
import FilePreviewPanel from './_components/FilePreviewPanel';
import WorkspaceEmpty from './_components/WorkspaceEmpty';
import {
  type WorkspaceSession, type FileEntry, type CreatedSession,
  fetchWorkspaceSessions, uploadFiles, fetchSandboxMode, setSandboxMode,
  renameWorkspaceSession,
} from './_components/useWorkspace';

const LS_KEY = 'workspace_active_session';

function loadSavedActive(): ActiveSession | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = localStorage.getItem(LS_KEY);
    return raw ? JSON.parse(raw) : null;
  } catch { return null; }
}

export default function WorkspacePage() {
  const [sessions, setSessions] = useState<WorkspaceSession[]>([]);
  const [loadingSessions, setLoadingSessions] = useState(true);
  const [active, setActive] = useState<ActiveSession | null>(null);
  // 空态发出首问后交接给 WorkspaceChat 的一条待发消息。key = 新 session_id，
  // 供聊天侧认领时去重（StrictMode 恰好一次，见 WorkspaceChat 的首问 effect）。
  const [pendingQuestion, setPendingQuestion] = useState<{ text: string; key: string } | null>(null);
  // 右栏
  const [tab, setTab] = useState<'files' | 'preview'>('files');
  const [selFile, setSelFile] = useState<{ path: string; entry: FileEntry } | null>(null);
  const [treeRefresh, setTreeRefresh] = useState(0);
  const [rightOpen, setRightOpen] = useState(true);
  // 上传 / 沙箱模式
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sandboxMode, setSandboxModeState] = useState('workspace-write');

  /** 落库列表 + 把活动会话的标题同步成服务端值。
   *
   *  同步这步不是锦上添花：标题是**后端异步换的**（fallback 先出，LLM 版随后替换），
   *  前端不跟着走的话，顶栏会一直显示会话刚打开时那个旧标题。 */
  const applySessions = useCallback((list: WorkspaceSession[]) => {
    const items = list.filter(s => s.agent_key);  // 非 agent 通道会话无法续聊，不进列表
    setSessions(items);
    setActive(prev => {
      if (!prev) return prev;
      const hit = items.find(s => s.session_id === prev.session_id);
      return hit && hit.title !== prev.label ? { ...prev, label: hit.title } : prev;
    });
  }, []);

  // 挂载：拉会话列表 + 恢复上次选中会话
  useEffect(() => {
    fetchWorkspaceSessions()
      .then(applySessions)
      .catch(() => {})
      .finally(() => setLoadingSessions(false));
    const saved = loadSavedActive();
    if (saved?.agent_key) setActive(saved);
  }, [applySessions]);

  const refreshSessions = useCallback(() => {
    fetchWorkspaceSessions().then(applySessions).catch(() => {});
  }, [applySessions]);

  // 选中会话变化：持久化 + 拉它的沙箱模式；清掉右栏旧选中
  useEffect(() => {
    if (!active) return;
    try { localStorage.setItem(LS_KEY, JSON.stringify(active)); } catch { /* ignore */ }
    setSelFile(null);
    if (active.session_id) {
      fetchSandboxMode(active.session_id)
        .then(m => setSandboxModeState(m.effective))
        .catch(() => {});
    }
  }, [active]);

  function selectSession(s: WorkspaceSession) {
    setActive({
      agent_key: s.agent_key || 'paper_agent',
      conversation_id: s.conversation_id,
      session_id: s.session_id,
      // title 恒非空（后端保证），不用再写 label 兜底；label 现在只用于提示
      label: s.title,
    });
  }

  /** 侧栏改名：写一条 user 源标题事件（后端会**钉住**它），成功后刷新列表。
   *
   *  必须用后端返回的规范化标题刷新，而不是把用户输入直接塞进本地状态 ——
   *  后端会去掉控制符并截到 80 字节，本地先改会让「显示值」和「库里值」短暂分叉。 */
  async function handleRename(sessionId: string, title: string) {
    const saved = await renameWorkspaceSession(sessionId, title);
    setSessions(prev => prev.map(s => s.session_id === sessionId
      ? { ...s, title: saved, title_source: 'user' as const }
      : s));
    // 改的正是当前打开的会话 → 顶栏标题也跟上
    setActive(prev => (prev && prev.session_id === sessionId ? { ...prev, label: saved } : prev));
    refreshSessions();
  }

  /** 侧栏「新建」：清空选中回到空态（聊天框居中显示，在那里选 agent + 文件夹）。 */
  function newSession() {
    setActive(null);
    setSelFile(null);
    // 一并清掉持久化的选中：否则刷新会复活旧会话，回不到空态
    try { localStorage.removeItem(LS_KEY); } catch { /* ignore */ }
  }

  /** 空态提交首问：会话已在后端建好（cwd 已绑定所选文件夹），切到聊天并交出这条消息。 */
  function handleFirstQuestion(s: CreatedSession, question: string) {
    setActive({
      agent_key: s.agent_key,
      conversation_id: s.conversation_id,
      session_id: s.session_id,
      label: s.label,
    });
    setPendingQuestion({ text: question, key: s.session_id });
    refreshSessions();
    setSelFile(null);
  }

  // 每轮对话完成：刷新侧栏（label/时间/文件数可能变了）+ 文件树（模型落了文件）。
  // 早先这里还有「草稿会话首问带回正式 id → 升级」一支，现在会话一律先在后端建好
  // （空态的 POST /sessions）再进聊天，不存在没有 id 的活动会话，故已移除。
  function handleTurnDone() {
    refreshSessions();
    setTreeRefresh(v => v + 1);
  }

  async function handleUpload(files: File[]) {
    if (!active?.session_id) return;   // 草稿还没 session：上传入口天然禁用
    setUploading(true); setUploadError(null);
    try {
      await uploadFiles(active.session_id, files);
      refreshSessions();               // entries 计数变了
      setTreeRefresh(v => v + 1);      // 文件树出现新文件
    } catch (e: any) {
      setUploadError(e?.message || String(e));
    } finally {
      setUploading(false);
    }
  }

  async function handleModeChange(mode: string) {
    if (!active?.session_id) return;
    setSandboxModeState(mode);   // 乐观更新
    try {
      await setSandboxMode(active.session_id, mode);
    } catch {
      // 失败回滚到服务端值
      fetchSandboxMode(active.session_id).then(m => setSandboxModeState(m.effective)).catch(() => {});
    }
  }

  function handleSelectFile(path: string, entry: FileEntry) {
    setSelFile({ path, entry });
    setTab('preview');
  }

  return (
    <div className="ws-root" style={{ display: 'flex', height: '100%', background: W.bg, color: W.text, fontFamily: W.font, overflow: 'hidden' }}>
      {/* 主题变量：由 theme.ts 的 W 生成，workspace.css 消费。走 dangerouslySetInnerHTML
          是因为 <style>{str}</style> 的文本子节点 SSR 端会转义引号、客户端不转义，
          两边文本不一致会 hydrate 崩页（font/mono 里有引号）。 */}
      <style dangerouslySetInnerHTML={{ __html: wsThemeCss }} />

      {/* 左栏 */}
      <WorkspaceSidebar
        sessions={sessions}
        selectedId={active?.session_id ?? null}
        onSelect={selectSession}
        onNew={newSession}
        onRefresh={refreshSessions}
        onRename={handleRename}
        loading={loadingSessions}
      />

      {/* 中栏（minWidth 保底：窄视口下宁可挤右栏也不压扁对话）。
          左右 16px 是内容轴的最小呼吸位——对话页的居中轴按这个盒宽算 64%。 */}
      <div style={{ flex: 1, minWidth: 420, minHeight: 0, display: 'flex', flexDirection: 'column', padding: `0 ${WS.base}px`, borderLeft: `0.5px solid ${W.borderSoft}`, borderRight: `0.5px solid ${W.borderSoft}` }}>
        {active ? (
          <WorkspaceChat
            session={active}
            onTurnDone={handleTurnDone}
            uploading={uploading}
            uploadError={uploadError}
            onUpload={handleUpload}
            sandboxMode={sandboxMode}
            onModeChange={handleModeChange}
            rightOpen={rightOpen}
            onToggleRight={() => setRightOpen(v => !v)}
            pendingQuestion={pendingQuestion}
            onPendingQuestionConsumed={() => setPendingQuestion(null)}
          />
        ) : (
          <WorkspaceEmpty onCreated={handleFirstQuestion} />
        )}
      </div>

      {/* 右栏：文件 / 预览 tab + 内容（顶栏按钮收起/展开） */}
      <div style={{ width: rightOpen ? 380 : 0, flexShrink: 0, minHeight: 0, display: 'flex', flexDirection: 'column', background: W.panel, overflow: 'hidden', transition: 'width .18s ease' }}>
        <div style={{ display: 'flex', gap: WS.xs, padding: `${WS.sm}px ${WS.md}px`, flexShrink: 0 }}>
          {(['files', 'preview'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              className="ws-item ws-round" data-active={tab === t}
              style={{
                height: 28, padding: '0 12px', borderRadius: 16,
                color: tab === t ? W.text : W.tertiary, fontSize: 13, fontFamily: 'inherit',
              }}>
              {t === 'files' ? '文件' : '预览'}
            </button>
          ))}
        </div>
        <div style={{ flex: 1, minHeight: 0, display: tab === 'files' ? 'flex' : 'none', flexDirection: 'column' }}>
          <FileTreePanel
            sessionId={active?.session_id ?? null}
            refreshSignal={treeRefresh}
            selectedPath={selFile?.path ?? null}
            onSelectFile={handleSelectFile}
          />
        </div>
        <div style={{ flex: 1, minHeight: 0, display: tab === 'preview' ? 'flex' : 'none', flexDirection: 'column' }}>
          <FilePreviewPanel
            sessionId={active?.session_id ?? null}
            path={selFile?.path ?? null}
            entry={selFile?.entry ?? null}
          />
        </div>
      </div>
    </div>
  );
}
