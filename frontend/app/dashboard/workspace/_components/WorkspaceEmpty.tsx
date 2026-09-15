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
//   - 浏览读写两类根下的目录（GET /api/workspace/folders）。**两类都能当工作区**：
//     选写根 = 直接可写；选读根 = 会话默认只读，模型写入时走升权审批。
//   - **新建文件 / 文件夹**（POST /api/workspace/folders）：**即时落盘**。早先只有
//     「新建文件夹」，且只把名字追加进路径、等 POST /sessions 时后端才 mkdir ——
//     那个形状是「建目录再拿它当工作区」这条唯一路径留下的。现在文件也能建，而
//     POST /sessions 只会 mkdir、不会建文件，所以一律改成即时创建，两处行为统一。
//     建文件夹后**进入**它（旧「创建即选中」语义的等价物：目录真的存在了，进去不会
//     404）；建文件后原地不动，只回一行确认 —— 列表只列目录，进去也没东西可看。
//   - 「自动」= folder 为 null，会话工作区落在 <SANDBOX_WORKSPACE_ROOT>/<sid>
//
// 根层（segments 为空）**不显示新建输入框**：folder 的第一段必须是根目录名
// （后端按名查 _browse_roots，自由文本必然 400），根层唯一合法的操作就是
// 点击一个已有根进入。

import { useEffect, useRef, useState } from 'react';
import { ArrowUp, Check, ChevronDown, ChevronRight, FilePlus, Folder, FolderOpen, FolderPlus, Loader2 } from 'lucide-react';
import { W, R, WS, CHAT_COLUMN } from '../theme';
import {
  browseWorkspaceFolders, createBrowseEntry, createWorkspaceSession,
  type CreatedSession, type EntryKind, type FileEntry,
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
  const [dirs, setDirs] = useState<FileEntry[]>([]);
  const [loading, setLoading] = useState(false);
  // 新建：类型 + 名字 + 两个反馈槽（成功一行、失败一行，各自独立 —— 混用一个
  // 变量时「上次成功」会被「这次失败」覆盖得莫名其妙）
  const [newKind, setNewKind] = useState<EntryKind>('dir');
  const [newName, setNewName] = useState('');
  const [creatingEntry, setCreatingEntry] = useState(false);
  const [createErr, setCreateErr] = useState('');
  const [createMsg, setCreateMsg] = useState('');
  const [browseErr, setBrowseErr] = useState('');
  // 当前位置**写入是否需要授权**（落在写根内 = 不需要），以及它的宿主路径——
  // 展示用：用户认的是 D:/jk/Nexus，不是 backend 容器里的 /srv/host-read/D
  const [writable, setWritable] = useState(true);
  // **用户能不能在这里新建**（实际挂载标志）。与 writable 是两件事：读根上
  // 用户能建、模型写入要过审批，所以不能拿 writable 来管新建入口的显隐。
  const [canCreate, setCanCreate] = useState(false);
  const [hostPath, setHostPath] = useState('');
  // 交给 POST /sessions 的 folder 值，**由后端翻译**（浏览根名 + 根内相对路径，
  // 见 useWorkspace.ts 的 FolderListing.folder）。前端从不自己拼 —— 新建走的是
  // 独立的 POST /folders，落盘后重新列举时后端会给出新的 folderValue。
  const [folderValue, setFolderValue] = useState<string | null>(null);

  const [question, setQuestion] = useState('');
  const [creating, setCreating] = useState(false);
  const [err, setErr] = useState('');
  const taRef = useRef<HTMLTextAreaElement>(null);

  const curPath = segments.join('/');
  const folderLabel = useAuto
    ? '自动分配目录'
    : (hostPath || (curPath ? curPath : '根'));
  // 停在根层（还没点进任何根）：拿不到可提交的 folder 值，拦住提交并说清原因，
  // 而不是让后端用 400 来拒绝。判据只看 folderValue —— 读根里的目录**能**当
  // 工作区了，不再按 writable 拦：选目录是划边界，写入松紧由沙箱模式另算。
  const folderBlocked = !useAuto && !folderValue;
  // 在只读根里：可选，但会话默认只读，模型写入要过一次升权审批。措辞据此切换。
  const needsApproval = !useAuto && !writable && !!folderValue;

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
    setLoading(true); setBrowseErr(''); setCreateErr(''); setCreateMsg('');
    browseWorkspaceFolders(curPath)
      .then(d => {
        if (cancelled) return;
        setDirs(d.entries);
        setWritable(d.writable);
        setCanCreate(d.canCreate);
        setHostPath(d.hostPath);
        setFolderValue(d.folder);
      })
      .catch((e: any) => { if (!cancelled) setBrowseErr(e?.message || String(e)); })
      .finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [curPath, folderOpen]);

  function enter(name: string) {
    setSegments(prev => [...prev, name]);
    setUseAuto(false);          // 进入目录 = 选它当工作文件夹
  }

  function goUp() {
    setSegments(prev => prev.slice(0, -1));
  }

  /** 新建条目：即时落盘，不等 POST /sessions。 */
  async function createEntry() {
    const name = newName.trim();
    if (!name || creatingEntry) return;
    // 与后端 _validate_new_name 同一套底线：分隔符与点段一律不放过
    // （后端仍会再校验一次，这里只为省一个来回、给个即时反馈）
    if (name.includes('/') || name.includes('\\') || name === '.' || name === '..') {
      setCreateErr('名字不能含 / 或 \\，也不能是 . 或 ..');
      return;
    }
    setCreatingEntry(true); setCreateErr(''); setCreateMsg('');
    try {
      await createBrowseEntry(curPath, name, newKind);
      setNewName('');
      setUseAuto(false);
      if (newKind === 'dir') {
        // 进入新目录：旧的「创建即选中」在这里等价复现，且目录真的存在，进去不会 404
        setSegments(prev => [...prev, name]);
      } else {
        // 文件不进：列表只列目录，进去也没有东西可看。原地留一行确认即可
        setCreateMsg(`已创建文件「${name}」`);
      }
    } catch (e: any) {
      // 名字不丢：大多是重名，改一个字就能重试
      setCreateErr(e?.message || String(e));
    } finally {
      setCreatingEntry(false);
    }
  }

  function chooseAuto() {
    setUseAuto(true);
    setFolderOpen(false);
  }

  async function submit() {
    const q = question.trim();
    if (!q || !agentKey || creating || folderBlocked) return;
    setCreating(true); setErr('');
    // 用后端翻译好的 folderValue，**不是** curPath（浏览路径）——两套命名。
    // 新建不再往它尾巴上追加名字：目录已经真建出来了，列表重载时 folderValue
    // 就是新的那个，前端没有任何需要自己拼的部分。
    const folder = useAuto ? null : folderValue;
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
              {needsApproval && <span style={{ flexShrink: 0, fontSize: 11, color: W.warning }}>写入需授权</span>}
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
                  </div>

                  {/* 目录列表 */}
                  <div style={{ maxHeight: 220, overflowY: 'auto', borderRadius: R.sm, background: W.bg }}>
                    {loading && <div style={{ padding: WS.lg, textAlign: 'center', color: W.dimmed, fontSize: 12 }}>加载中…</div>}
                    {!loading && dirs.length === 0 && (
                      <div style={{ padding: `${WS.lg}px ${WS.base}px`, textAlign: 'center', color: W.dimmed, fontSize: 12 }}>
                        {curPath ? '没有子目录' : '配置的读写根（SANDBOX_READ_ROOTS / SANDBOX_WRITE_ROOTS）里还没有子目录'}
                      </div>
                    )}
                    {dirs.map(d => (
                      <div key={d.name} onClick={() => enter(d.name)}
                        className="ws-hover"
                        title={d.writable === false ? '只读位置：可以选为工作区，但默认只读，写入需要你批准' : undefined}
                        style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '7px 12px', borderRadius: R.xs, cursor: 'pointer', fontSize: 13, color: W.text }}>
                        <Folder size={13} color={W.dimmed} />
                        <span style={{ flex: 1, minWidth: 0, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{d.name}</span>
                        {/* 根层的条目各自标明写入要不要授权（写根 / 读根混在一起列） */}
                        {d.writable === false && <span style={{ flexShrink: 0, fontSize: 11, color: W.dimmed }}>写入需授权</span>}
                        <ChevronRight size={12} color={W.dimmed} />
                      </div>
                    ))}
                  </div>

                  {/* 停在只读位置：要说清的不是"选不了"，而是"选了会怎样"——
                      工作区照当，写入过一次审批即可；另给一条回可写区的路 */}
                  {needsApproval && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: '7px 10px', borderRadius: R.sm, background: W.bg, fontSize: 12, lineHeight: '18px' }}>
                      <span style={{ flex: 1, color: W.dimmed }}>
                        只读位置：可以当工作区，模型写入时会向你申请批准。
                      </span>
                      <button onClick={chooseAuto} className="ws-btn ws-round"
                        style={{ flexShrink: 0, height: 24, padding: '0 10px', borderRadius: 12, color: W.text, fontSize: 12, fontFamily: 'inherit' }}>
                        用自动分配
                      </button>
                    </div>
                  )}

                  {/* 新建文件 / 文件夹：根层隐藏（第一段必须是根目录名，自由文本必
                      400）；只读位置也隐藏。注意判据是 canCreate 而不是 writable ——
                      后者说的是「模型写入要不要审批」，读根上用户建得了、模型仍要审批 */}
                  {segments.length >= 1 && canCreate && (
                    <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm }}>
                      {/* 类型胶囊：空间紧张，用两个短标签而不是下拉框 */}
                      <div style={{ display: 'flex', flexShrink: 0, padding: 2, borderRadius: 999, background: W.bg }}>
                        {([['dir', '文件夹'], ['file', '文件']] as const).map(([k, label]) => (
                          <button key={k} onClick={() => { setNewKind(k); setCreateErr(''); }}
                            className="ws-round"
                            style={{
                              height: 22, padding: '0 9px', borderRadius: 999, border: 'none',
                              background: newKind === k ? W.surface : 'transparent',
                              color: newKind === k ? W.text : W.tertiary,
                              fontSize: 11.5, fontFamily: 'inherit', cursor: 'pointer',
                            }}>{label}</button>
                        ))}
                      </div>
                      <div style={{ flex: 1, minWidth: 0, display: 'flex', alignItems: 'center', gap: 6, padding: '5px 10px', borderRadius: R.sm, background: W.bg }}>
                        {newKind === 'dir' ? <FolderPlus size={13} color={W.dimmed} /> : <FilePlus size={13} color={W.dimmed} />}
                        <input value={newName} onChange={e => setNewName(e.target.value)}
                          onKeyDown={e => { if (e.key === 'Enter') { e.preventDefault(); createEntry(); } }}
                          placeholder={newKind === 'dir' ? '新建文件夹名' : '新建文件名'}
                          style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 12.5, fontFamily: 'inherit' }} />
                      </div>
                      <button onClick={createEntry} disabled={!newName.trim() || creatingEntry}
                        className={`${newName.trim() ? 'ws-btn-primary' : 'ws-btn'} ws-round`}
                        style={{
                          height: 28, padding: '0 12px', borderRadius: 16,
                          color: newName.trim() ? W.primaryText : W.dimmed,
                          fontSize: 12.5, fontFamily: 'inherit',
                        }}>{creatingEntry ? '创建中…' : '新建'}</button>
                    </div>
                  )}
                  {createErr && <div style={{ fontSize: 11.5, color: W.danger }}>{createErr}</div>}
                  {createMsg && <div style={{ fontSize: 11.5, color: W.success }}>{createMsg}</div>}

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
            <span style={{ flex: 1, fontSize: 12, color: folderBlocked ? W.warning : W.dimmed, paddingLeft: 6 }}>
              {folderBlocked
                ? '先选一个文件夹（当前停在根层，还没进去）'
                : needsApproval
                  ? '选中的文件夹是模型的工作目录；该位置默认只读，写入需你批准'
                  : '选中的文件夹就是模型可读写的工作目录'}
            </span>
            <button onClick={submit} disabled={!question.trim() || !agentKey || creating || folderBlocked}
              title={folderBlocked ? '先在文件夹选择器里点进一个目录' : '创建对话并发送'}
              className="ws-send ws-round"
              style={{
                width: 34, height: 34, borderRadius: 999, flexShrink: 0,
                color: (!question.trim() || !agentKey || creating || folderBlocked) ? W.dimmed : '#fff',
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
