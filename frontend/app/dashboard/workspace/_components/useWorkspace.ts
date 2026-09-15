'use client';

// useWorkspace —— 工作区页的数据层（薄封装，全部直连后端 REST）
//
// 后端端点清单（backend/api/workspace.py + agents.py）：
//   GET  /api/workspace/sessions                 会话列表（按 agent 分组）
//   GET  /api/workspace/folders?path=            选择器列目录（只列目录）
//   POST /api/workspace/folders                  选择器里新建空文件 / 目录（即时落盘）
//   GET  /api/workspace/{sid}/files?path=        列目录（2000 条截断 + truncated）
//   POST /api/workspace/{sid}/files              会话工作区里新建空文件 / 目录
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

/** 非 2xx → 抛后端的人话 detail；拿不到 JSON body 才退回 HTTP 状态码。 */
async function throwHttp(r: Response): Promise<never> {
  let detail = `HTTP ${r.status}`;
  try { detail = (await r.json()).detail || detail; } catch { /* 保持状态码 */ }
  throw new Error(detail);
}

/** 新建条目的类型（后端 ENTRY_KINDS 的镜像）。 */
export type EntryKind = 'file' | 'dir';

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
  /** 在该目录里写入**是否无需授权**（落在 SANDBOX_WRITE_ROOTS 写根内）。
   *  只有文件选择器会带这个字段（文件树不带）。两类根都能当工作区——读根里的
   *  目录可选，只是会话默认只读、模型写入时任要过一次升权审批；这个标志如今
   *  只影响措辞，不拦选择。 */
  writable?: boolean;
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

export async function fetchDir(sessionId: string, path: string): Promise<{ entries: FileEntry[]; truncated: boolean; writable: boolean }> {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const r = await fetch(`${API}/api/workspace/${sessionId}/files${q}`, { headers: H });
  if (!r.ok) await throwHttp(r);
  const d = await r.json();
  // writable 缺字段按 true 兜：后端没这个字段时不该把新建入口整个禁掉
  return { entries: d.entries || [], truncated: !!d.truncated, writable: d.writable !== false };
}

/** 在**会话工作区**的某个目录里新建空文件 / 目录（即时落盘）。返回新建的条目。
 *
 *  只建空条目：内容得靠上传覆盖或让模型写（后端有意只开这两个窄写入口）。 */
export async function createWorkspaceEntry(
  sessionId: string, dirPath: string, name: string, kind: EntryKind,
): Promise<FileEntry> {
  const r = await fetch(`${API}/api/workspace/${sessionId}/files`, {
    method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: dirPath, name, type: kind }),
  });
  if (!r.ok) await throwHttp(r);
  return (await r.json()).entry as FileEntry;
}

/** 在**文件夹选择器当前浏览的目录**里新建空文件 / 目录（即时落盘）。返回新建的条目。
 *
 *  与 createWorkspaceEntry 是同一套后端校验，只是位置来源不同：那个固定在建好的
 *  会话工作区里，这个跟着选择器的浏览路径走。 */
export async function createBrowseEntry(
  browsePath: string, name: string, kind: EntryKind,
): Promise<FileEntry> {
  const r = await fetch(`${API}/api/workspace/folders`, {
    method: 'POST', headers: { ...H, 'Content-Type': 'application/json' },
    body: JSON.stringify({ path: browsePath, name, type: kind }),
  });
  if (!r.ok) await throwHttp(r);
  return (await r.json()).entry as FileEntry;
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
  if (!r.ok) await throwHttp(r);
  const d = await r.json();
  return d.uploaded || [];
}

/** 文件夹选择器的列目录结果。
 *
 *  path 的第一段是**浏览根名**（写根 / 读根同一套命名），其后是根内相对路径。
 *  writable 说的是**当前目录**写入要不要授权——两类根都能当工作区，这个标志
 *  现在只决定文案（"写入需授权" vs 直接可写），不再拦选择。
 *  hostPath 是宿主绝对路径，展示给用户看（用户认的是 D:/jk/Nexus，
 *  不是 backend 容器里的 /srv/host-read/D）。 */
export interface FolderListing {
  entries: FileEntry[];
  truncated: boolean;
  /** 模型在这里写入**要不要授权**（写根内 = 不要）。措辞用，不拦选择。 */
  writable: boolean;
  /** **用户**能不能在这里新建（看的是实际挂载标志，读根也已放开成可写）。
   *  与 writable 是两件事：读根上用户能建、模型写入要过审批。 */
  canCreate: boolean;
  hostPath: string;
  /** 交给 POST /sessions 的 folder 值，**后端翻译好的**（浏览根名 + 根内相对路径）。
   *
   *  进了某个根之后它就等于浏览路径（segments.join('/')），但仍别自己拼：
   *  根层拿不到值（null → 拦住提交），命名规则（_folder_value）也归后端所有，
   *  前端照抄就是第二个事实源。 */
  folder: string | null;
}

export async function browseWorkspaceFolders(path: string): Promise<FolderListing> {
  const q = path ? `?path=${encodeURIComponent(path)}` : '';
  const r = await fetch(`${API}/api/workspace/folders${q}`, { headers: H });
  if (!r.ok) throw new Error(`HTTP ${r.status}`);
  const d = await r.json();
  return {
    entries: d.entries || [],
    truncated: !!d.truncated,
    writable: !!d.writable,
    canCreate: d.can_create === true,
    hostPath: d.host_path || '',
    folder: d.folder ?? null,
  };
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
  if (!r.ok) await throwHttp(r);
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
