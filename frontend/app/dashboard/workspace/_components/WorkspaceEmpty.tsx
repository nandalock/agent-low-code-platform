'use client';

// WorkspaceEmpty —— 中栏空态：居中标题 + 选择 chip 行 + 输入卡（参考实现 EmptyHero）
//
// 这是「新建工作区」的入口：选好 agent 与工作文件夹、输入第一条消息，回车即
// 建会话并发出该消息（POST /api/workspace/sessions → onCreated(s, question)），
// 页面随即切到正常三栏聊天。
//
// 版面就三行（标题 / chip / 输入卡，行距 12），不做卡片套卡片——改造前那张
// 660px 的浮空大卡把选择器、欢迎语、输入条全裹在一起，视觉重心是散的。
//
// 文件夹选择器：
//   - 浏览 SANDBOX_WRITE_ROOTS 写根下的目录（GET /api/workspace/folders）
//   - **新建文件夹**：只把名字追加进路径，不导航、不调后端 —— 目录由
//     POST /sessions 时后端 mkdir。不导航是刻意的：浏览一个尚不存在的目录只会
//     拿到 404 / 空列表，看起来像出错。
//   - 「自动」= folder 为 null，会话工作区落在 <SANDBOX_WORKSPACE_ROOT>/<sid>
//
// 根层（segments 为空）**不显示新建输入框**：folder 的第一段必须是写根目录名
// （后端 _write_root_by_name 按目录名匹配，自由文本必然 400），根层唯一合法的
// 操作就是点击一个已有写根进入。

import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronDown, ChevronRight, Folder, FolderOpen, Loader2, Plus } from 'lucide-react';
import { W, R, WS, CHAT_COLUMN } from '../theme';
import {
  browseWorkspaceFolders, createWorkspaceSession,
  type CreatedSession, type FileEntry,
} from './useWorkspace';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const H = { 'X-Tenant-ID': '1' };

interface Props {
  /** 会话建好之后：交回创建结果与用户输入的首条消息，由页面切到聊天并发出。 */
  onCreated: (s: CreatedSession, question: string) => void;
}

export default function WorkspaceEmpty({ onCreated }: Props) {
  const [agents, setAgents] = useState<{ key: string; name: string }[]>([]);
  const [agentKey, setAgentKey] = useState('');

  // 文件夹选择
  const [folderOpen, setFolderOpen] = useState(false);
  const [useAuto, setUseAuto] = useState(true);
  const [segments, setSegments] = useState<string[]>([]);   // 已进入的路径段
  const [pendingDir, setPendingDir] = useState<string | null>(null);  // 新建的目录名（尾部追加，不导航）
  const [dirs, setDirs] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  const [newName, setNewName] = useState('');
  const [browseErr, setBrowseErr] = useState('');

  const [question, setQuestion] = useState('');
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);

  const curPath = segments.join('/');
  const folderLabel = useAuto ? '自动分配目录' : (curPath ? curPath : '根') + (pendingDir ? `/${pendingDir}` : '');

  // agent 名单（默认选第一个）
  useEffect(() => {
    fetch(`${API}/api/agents`, { headers: H })
      .then(r => r.json())
      .then((list: any[]) => {
        const items = (Array.isArray(list) ? list : []).map((a: any) => ({ key: a.key, name: a.name || a.key }));
        setAgents(items);
        if (items.length) setAgentKey(k => k || items[0].key);
      })
      .catch(() => {});
  }, []);

  // 目录列表（popover 打开时才拉，省得空态一挂载就打接口）
  useEffect(() => {
    if (!folderOpen) return;
    let cancelled = false;
    setLoading(true); setBrowseErr('');
    browseWorkspaceFolders(curPath)
      .then(d => { if (!cancelled) setDirs(d.entries); })
      .catch((e: any) => { if (!cancelled) setBrowseErr(e?.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [curPath, folderOpen]);

  function enter(name: string) {
    setSegments(prev => [...prev, name]);
    setPendingDir(null);        // 换目录了，尾巴上的新目录名不再适用
    setUseAuto(false);          // 进入目录 = 选它当工作文件夹
  }

  function goUp() {
    setSegments(prev => prev.slice(0, -1));
    setPendingDir(null);
  }

  function addFolder() {
    const name = newName.trim();
    if (!name) return;
    // 与后端 resolve_workspace_member / _host_cwd_to_backend 同一套底线：
    // 分隔符与 '..' 一律不放过（那两个地方会拒，这里早筛给个即时反馈）
    if (name.includes('/') || name.includes('\\') || name === '.' || name === '..') {
      setBrowseErr('文件夹名不能含 / 或 \\，也不能是 . 或 ..');
      return;
    }
    setBrowseErr('');
    setPendingDir(name);
    setNewName('');
    setUseAuto(false);
  }

  function chooseAuto() {
    setUseAuto(true);
    setPendingDir(null);
    setFolderOpen(false);
  }

  async function submit() {
    const q = question.trim();
    if (!q || !agentKey || creating) return;
    setCreating(true); setErr('');
    const folder = useAuto
      ? null
      : (curPath + (pendingDir ? `/${pendingDir}` : '')) || null;
    try {
      const s = await createWorkspaceSession(agentKey, folder);
      onCreated(s, q);            // 成功后本组件随 active 变更卸载
    } catch (e: any) {
      setErr(e?.message || String(e));   // 失败留在空态：消息内容保留，可改选项重试
      setCreating(false);
    }
  }

  return (
    <div style={{
      flex: 1, minHeight: 0, display: 'flex', alignItems: 'center', justifyContent: 'center',
      // 横向留白由中栏（page.tsx）统一给，这里再加就双份了
      background: W.bg, fontFamily: W.font, color: W.text, overflow: 'auto',
    }}>
      {/* 内容轴与对话页同一根：这样建完会话切过去时，输入框不会横向跳一下 */}
      <div style={{ width: '100%', maxWidth: CHAT_COLUMN, paddingBottom: 64 }}>

        <div style={{ fontSize: 26, lineHeight: '32px', fontWeight: 500, marginBottom: WS.md, paddingLeft: WS.xs }}>
          你好，我是工作区智能体
        </div>

        {/* 选择 chip 行 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, marginBottom: WS.md, paddingLeft: WS.xs, flexWrap: 'wrap' }}>
          <div style={{ position: 'relative' }}>
            <button onClick={() => setFolderOpen(v => !v)}
              title="选择工作文件夹（模型读写文件的位置）"
              className="ws-btn ws-round"
              style={{
                height: 28, maxWidth: 360, padding: '0 10px', borderRadius: 16,
                fontSize: 13, fontFamily: 'inherit',
                color: useAuto ? W.tertiary : W.text,
                display: 'flex', alignItems: 'center', gap: 6,
              }}>
              {useAuto ? <FolderOpen size={13} /> : <Folder size={13} />}
              <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{folderLabel}</span>
              <ChevronDown size={12} style={{ flexShrink: 0, opacity: 0.7 }} />
            </button>

            {folderOpen && (
              <>
                <div onClick={() => setFolderOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
                <div style={{
                  position: 'absolute', top: 'calc(100% + 8px)', left: 0, width: 380, zIndex: 50,
                  // 浮层用 overlay（比输入卡所在的 surfaceHi 再亮一档）
                  background: W.overlay, borderRadius: R.md, boxShadow: W.elevProminent,
                  padding: WS.md, display: 'flex', flexDirection: 'column', gap: WS.sm,
                }}>
                  {/* 面包屑 + 上级 */}
                  <div style={{ display: 'flex', alignItems: 'center', gap: WS.xs, padding: '5px 8px', borderRadius: R.sm, background: W.bg, fontSize: 12, flexWrap: 'wrap' }}>
                    <button onClick={goUp} disabled={segments.length === 0} title="返回上级"
                      style={{ background: 'none', border: 'none', padding: 2, display: 'flex', color: W.secondary, cursor: segments.length ? 'pointer' : 'not-allowed', opacity: segments.length ? 1 : 0.4 }}>
                      <ArrowUp size={13} />
                    </button>
                    <span style={{ color: W.dimmed }}>根</span>
                    {segments.map((s, i) => (
                      <span key={i} style={{ display: 'flex', alignItems: 'center', gap: WS.xs }}>
                        <ChevronRight size={11} color={W.dimmed} />
                        <span style={{ color: W.text }}>{s}</span>
                      </span>
                    ))}
                    {pendingDir && (
                      <span style={{ display: 'flex', alignItems: 'center', gap: WS.xs }}>
                        <ChevronRight size={11} color={W.dimmed} />
                        <span style={{ color: W.accent }}>{pendingDir}（新建）</span>
                      </span>
                    )}
                  </div>

                  {/* 目录列表 */}
                  <div style={{ maxHeight: 220, overflowY: 'auto', borderRadius: R.sm, background: W.bg }}>
                    {loading && <div style={{ padding: WS.lg, textAlign: 'center', color: W.dimmed, fontSize: 12 }}>加载中…</div>}
                    {!loading && dirs.length === 0 && (
                      <div style={{ padding: `${WS.lg}px ${WS.base}px`, textAlign: 'center', color: W.dimmed, fontSize: 12 }}>
                        {curPath ? '没有子目录' : '配置的写根（SANDBOX_WRITE_ROOTS）里还没有子目录'}
                      </div>
                    )}
                    {dirs.map(d => (
                      <div key={d.name} onClick={() => enter(d.name)}
                        className="ws-hover"
                        style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '7px 12px', borderRadius: R.xs, cursor: 'pointer', fontSize: 13, color: W.text }}>
                        <Folder size={13} color={W.dimmed} />
                        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                        <ChevronRight size={12} color={W.dimmed} />
                      </div>
                    ))}
                  </div>

                  {/* 新建文件夹：根层隐藏（第一段必须是写根目录名，自由文本必 400） */}
                  {segments.length >= 1 && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm }}>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: R.sm, background: W.bg }}>
                        <Plus size={13} color={W.dimmed} />
                        <input value={newName} onChange={e => setNewName(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); addFolder(); } }}
                          placeholder="新建文件夹名"
                          style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 12.5, fontFamily: 'inherit' }} />
                      </div>
                      <button onClick={addFolder} disabled={!newName.trim()}
                        className={`${newName.trim() ? 'ws-btn-primary' : 'ws-btn'} ws-round`}
                        style={{
                          height: 28, padding: '0 12px', borderRadius: 16,
                          color: newName.trim() ? W.primaryText : W.dimmed,
                          fontSize: 12.5, fontFamily: 'inherit',
                        }}>新建</button>
                    </div>
                  )}

                  {/* 自动 */}
                  <button onClick={chooseAuto} className="ws-btn"
                    style={{
                      display: 'flex', alignItems: 'center', gap: WS.sm, padding: '7px 10px',
                      borderRadius: R.sm, fontSize: 13, color: W.text,
                      fontFamily: 'inherit', textAlign: 'left',
                    }}>
                    <span style={{ width: 14, flexShrink: 0, color: W.accent }}>{useAuto && <Check size={13} />}</span>
                    <FolderOpen size={13} color={W.dimmed} />
                    <span style={{ flex: 1 }}>自动（默认目录）</span>
                    <span style={{ fontSize: 11, color: W.dimmed }}>落在 &lt;root&gt;/&lt;session_id&gt;</span>
                  </button>

                  {browseErr && <div style={{ fontSize: 11.5, color: W.danger }}>{browseErr}</div>}
                </div>
              </>
            )}
          </div>

          <select value={agentKey} onChange={e => setAgentKey(e.target.value)}
            title="选择对话的 Agent"
            className="ws-round"
            style={{
              height: 28, padding: '0 10px', borderRadius: 16, border: 'none',
              background: 'transparent', color: W.tertiary, fontSize: 13,
              cursor: 'pointer', outline: 'none', fontFamily: 'inherit', maxWidth: 240,
            }}>
            {agents.length === 0 && <option value="">加载中…</option>}
            {agents.map(a => <option key={a.key} value={a.key}>{a.name}</option>)}
          </select>
        </div>

        {/* 输入卡：与 Composer 同一形态 */}
        <div style={{
          display: 'flex', flexDirection: 'column', borderRadius: R.xl,
          background: W.surfaceHi, boxShadow: W.elevSoft, paddingTop: WS.sm,
        }}>
          <textarea
            ref={taRef}
            value={question}
            onChange={e => setQuestion(e.target.value)}
            onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); } }}
            placeholder={creating ? '正在创建会话…' : '输入消息，Enter 创建会话并发送'}
            disabled={creating}
            rows={Math.min(14, Math.max(1, question.split('\n').length))}
            className="ws-round"
            style={{
              background: 'transparent', border: 'none', outline: 'none', resize: 'none',
              color: W.text, fontSize: 14, lineHeight: '24px', fontFamily: 'inherit',
              maxHeight: 336, padding: '4px 12px 0 14px',
            }} />
          <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '2px 8px 6px 8px' }}>
            <span style={{ flex: 1, fontSize: 12, color: W.dimmed, paddingLeft: 6 }}>
              选中的文件夹就是模型可读写的工作目录
            </span>
            <button onClick={submit} disabled={!question.trim() || !agentKey || creating}
              title="创建对话并发送"
              className="ws-send ws-round"
              style={{
                width: 34, height: 34, borderRadius: 999, flexShrink: 0,
                color: (!question.trim() || !agentKey || creating) ? W.dimmed : '#fff',
                display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
              {creating ? <Loader2 size={16} className="ws-spin" /> : <ArrowUp size={17} />}
            </button>
          </div>
        </div>

        {err && <div style={{ fontSize: 12, color: W.danger, marginTop: WS.sm, paddingLeft: WS.xs }}>{err}</div>}
      </div>
    </div>
  );
}
