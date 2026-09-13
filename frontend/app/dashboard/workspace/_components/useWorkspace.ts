'use client';

// useWorkspace —— 工作区页的数据层（薄封装，全部直连后端 REST）
//
// 后端端点清单（backend/api/workspace.py + agents.py）：
//   GET  /api/workspace/sessions                 会话列表（按 agent 分组）
//   GET  /api/workspace/{sid}/files?path=        列目录（2000 条截断 + truncated）
//   GET  /api/workspace/{sid}/file?path=&inline=1  预览 / 下载（inline=0）
//   POST /api/workspace/{sid}/upload             多文件上传 → .dsh-drops/
//   GET  /api/agents/sessions/{sid}/sandbox_mode  读会话沙箱模式
//   POST /api/agents/sessions/{sid}/sandbox_mode  写会话沙箱模式（覆盖）
//
// 预览接口需要 X-Tenant-ID 头，<iframe>/<img> 直接指 URL 发不出自定义头，
// 所以预览一律 fetch → Blob → objectURL（本文件导出 fetchPreview 供分发）。

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;
const H = { 'X-Tenant-ID': String(TENANT_ID) };

export interface WorkspaceSession {
  session_id: string;
  agent_key: string | null;
  conversation_id: number;
  label: string;
  channel: string | null;
  updated_at: string | null;
  entries: number;
}

export interface FileEntry {
  name: string;
  is_dir: boolean;
  size: number;
  mtime: string | null;
  is_link: boolean;
  outside: boolean;   // 指向工作区外的符号链接：不可下钻
}

export interface SandboxModeInfo {
  override: string | null;
  default: string | null;
  effective: string;
}

export const SANDBOX_MODE_LABELS: Record<string, string> = {
  'read-only': '只读',
  'workspace-write': '标准模式',
  'danger-full-access': '完全访问',
};

export async function fetchWorkspaceSessions(): Promise<WorkspaceSession[]> {
  const r = await fetch(`${API}/api/workspace/sessions`, { headers: H });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  return d.items || [];
}

export async function fetchDir(sessionId: string, path: string): Promise<{ entries: FileEntry[]; truncated: boolean }> {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const r = await fetch(`${API}/api/workspace/${sessionId}/files${q}`, { headers: H });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  return { entries: d.entries || [], truncated: !!d.truncated };
}

/** 预览：fetch → Blob。调用方按 blob.type 分发渲染（pdf→iframe / image→img / text→文本）。 */
export async function fetchPreview(sessionId: string, path: string): Promise<Blob> {
  const r = await fetch(
    `${API}/api/workspace/${sessionId}/file?path=${encodeURIComponent(path)}&inline=1`,
    { headers: H },
  );
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.blob();
}

export function downloadUrl(sessionId: string, path: string): string {
  return `${API}/api/workspace/${sessionId}/file?path=${encodeURIComponent(path)}`;
}

/** 多文件上传 → .dsh-drops/。返回成功落盘的列表（后端整批校验失败时一个不落盘）。 */
export async function uploadFiles(sessionId: string, files: File[]): Promise<{ name: string; path: string; size: number }[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f, f.name);
  const r = await fetch(`${API}/api/workspace/${sessionId}/upload`, { method: 'POST', headers: H, body: fd });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(detail);
  }
  const d = await r.json();
  return d.uploaded || [];
}

/** 文件夹选择器：列出部署根下某目录的子目录（path 为相对部署根的路径）。 */
export async function browseWorkspaceFolders(path: string): Promise<{ entries: FileEntry[]; truncated: boolean }> {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const r = await fetch(`${API}/api/workspace/folders${q}`, { headers: H });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  return { entries: d.entries || [], truncated: !!d.truncated };
}

export interface CreatedSession {
  session_id: string;
  conversation_id: number;
  agent_key: string;
  agent_name: string;
  label: string;
  folder: string | null;
}

/** 新建绑定了工作文件夹的会话（folder 为 null/空 = 自动目录）。 */
export async function createWorkspaceSession(agentKey: string, folder: string | null): Promise<CreatedSession> {
  const r = await fetch(`${API}/api/workspace/sessions`, {
    method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
    body: JSON.stringify({ agent_key: agentKey, folder: folder ?? '' }),
  });
  if (!r.ok) {
    let detail = `HTTP ${r.status}`;
    try { detail = (await r.json()).detail || detail; } catch { /* keep */ }
    throw new Error(detail);
  }
  const d = await r.json();
  return d.session as CreatedSession;
}

export async function fetchSandboxMode(sessionId: string): Promise<SandboxModeInfo> {
  const r = await fetch(`${API}/api/agents/sessions/${sessionId}/sandbox_mode`, { headers: H });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  return await r.json();
}

export async function setSandboxMode(sessionId: string, mode: string): Promise<void> {
  const r = await fetch(`${API}/api/agents/sessions/${sessionId}/sandbox_mode`, {
    method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
}

/** DSH 侧栏风格相对时间：今天 HH:mm / 昨天 / MM-DD / YYYY-MM-DD */
export function fmtRelTime(iso: string | null): string {
  if (!iso) return '';
  const d = new Date(iso);
  if (isNaN(d.getTime())) return '';
  const now = new Date();
  const p = (n: number) => String(n).padStart(2, '0');
  const hm = `${p(d.getHours())}:${p(d.getMinutes())}`;
  const sameDay = (a: Date, b: Date) => a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
  const yesterday = new Date(now); yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return hm;
  if (sameDay(d, yesterday)) return '昨天';
  if (d.getFullYear() === now.getFullYear()) return `${p(d.getMonth() + 1)}-${p(d.getDate())}`;
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

/** 目录/文件的绝对显示大小（目录为 '-'）。 */
export function fmtSize(bytes: number, isDir: boolean): string {
  if (isDir) return '';
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
