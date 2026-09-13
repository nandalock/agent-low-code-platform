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
import { W, WS } from './theme';
import WorkspaceSidebar from './_components/WorkspaceSidebar';
import WorkspaceChat, { type ActiveSession } from './_components/WorkspaceChat';
import FileTreePanel from './_components/FileTreePanel';
import FilePreviewPanel from './_components/FilePreviewPanel';
import NewSessionDialog from './_components/NewSessionDialog';
import {
  type WorkspaceSession, type FileEntry,
  fetchWorkspaceSessions, uploadFiles, fetchSandboxMode, setSandboxMode,
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
  const [showNewDialog, setShowNewDialog] = useState(false);
  // 右栏
  const [tab, setTab] = useState<'files' | 'preview'>('files');
  const [selFile, setSelFile] = useState<{ path: string; entry: FileEntry } | null>(null);
  const [treeRefresh, setTreeRefresh] = useState(0);
  const [rightOpen, setRightOpen] = useState(true);
  // 上传 / 沙箱模式
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const [sandboxMode, setSandboxModeState] = useState('workspace-write');

  // 挂载：拉会话列表 + 恢复上次选中会话
  useEffect(() => {
    fetchWorkspaceSessions()
      .then(list => setSessions(list.filter(s => s.agent_key)))   // 非 agent 通道会话（agent_key=null）无法续聊，不进列表
      .catch(() => {})
      .finally(() => setLoadingSessions(false));
    const saved = loadSavedActive();
    if (saved?.agent_key) setActive(saved);
  }, []);

  const refreshSessions = useCallback(() => {
    fetchWorkspaceSessions()
      .then(list => setSessions(list.filter(s => s.agent_key)))
      .catch(() => {});
  }, []);

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
      label: s.label || s.session_id.slice(0, 8),
    });
  }

  function newSession() {
    setShowNewDialog(true);   // 选 agent + 选文件夹 → 后端真正建会话
  }

  function handleSessionCreated(s: { session_id: string; conversation_id: number; agent_key: string; label: string }) {
    setShowNewDialog(false);
    setActive({
      agent_key: s.agent_key,
      conversation_id: s.conversation_id,
      session_id: s.session_id,
      label: s.label,
    });
    refreshSessions();
    setSelFile(null);
  }

  // 每轮对话完成：刷新侧栏 + 文件树；草稿首问带回正式 id → 升级
  function handleTurnDone(ids?: { conversation_id: number | null; session_id: string | null }) {
    refreshSessions();
    setTreeRefresh(v => v + 1);
    if (ids?.session_id && active && active.session_id === null) {
      const upgraded: ActiveSession = {
        ...active,
        conversation_id: ids.conversation_id ?? active.conversation_id,
        session_id: ids.session_id,
      };
      setActive(upgraded);
    }
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
    <div style={{ display: 'flex', height: '100%', background: W.bg, color: W.text, fontFamily: W.font, overflow: 'hidden' }}>
      {/* 左栏 */}
      <WorkspaceSidebar
        sessions={sessions}
        selectedId={active?.session_id ?? null}
        onSelect={selectSession}
        onNew={newSession}
        onRefresh={refreshSessions}
        loading={loadingSessions}
      />

      {/* 中栏（minWidth 保底：窄视口下宁可挤右栏也不压扁对话） */}
      <div style={{ flex: 1, minWidth: 420, minHeight: 0, display: 'flex', flexDirection: 'column', borderLeft: `1px solid ${W.border}`, borderRight: `1px solid ${W.border}` }}>
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
        />
      </div>

      {/* 新建会话对话框 */}
      {showNewDialog && (
        <NewSessionDialog
          onClose={() => setShowNewDialog(false)}
          onCreated={handleSessionCreated}
        />
      )}

      {/* 右栏：文件 / 预览 tab + 内容（顶栏头像点击收起/展开） */}
      <div style={{ width: rightOpen ? 380 : 0, flexShrink: 0, minHeight: 0, display: 'flex', flexDirection: 'column', background: W.panel, overflow: 'hidden', transition: 'width .18s ease' }}>
        <div style={{ display: 'flex', gap: WS.xs, padding: `${WS.sm}px ${WS.base}px 0`, borderBottom: `1px solid ${W.border}` }}>
          {(['files', 'preview'] as const).map(t => (
            <button key={t} onClick={() => setTab(t)}
              style={{
                padding: '6px 14px', border: 'none', background: 'transparent', cursor: 'pointer',
                color: tab === t ? W.text : W.tertiary, fontSize: 13, fontFamily: 'inherit',
                borderBottom: tab === t ? `2px solid ${W.accent}` : '2px solid transparent',
                fontWeight: tab === t ? 600 : 400,
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
