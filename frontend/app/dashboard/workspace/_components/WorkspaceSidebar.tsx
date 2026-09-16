'use client';

// WorkspaceSidebar —— 左栏：新建会话 + 搜索 + **三级**会话树
//
//   智能体（agent） → 文件夹（project） → 对话（session）
//
// 数据源 GET /api/workspace/sessions，分组所需的字段全部由后端算好（agent_key /
// agent_name、project、title），前端只做展示与折叠 —— 分组的**判据**在后面：
// 「什么算一个项目」「cwd 校验不过怎么办」都是后端契约，前端各算一遍就是第二个
// 事实源，两边迟早在同名目录 / 尾斜杠 / 大小写上分叉。
//
// 三个层级的语义差别（决定了它们各自有什么交互）：
//   - **智能体**：注册表里的定义，不是会话的实体。只能折叠，没有改名 / 删除。
//   - **文件夹（项目）**：一个真实目录在**项目注册表**里的登记。行菜单给
//     「重命名 / 移除」—— 两个改的都是**登记**，不是目录：重命名只改显示名，
//     移除只撤销登记（目录、文件、会话记录、事件日志一律不动，其会话落进未分组）。
//     这里刻意**没有**「改目录名 / 删目录」：那等于移动、删除文件，而工作区只开了
//     「读 + 建空条目」两个窄写入口（见 api/workspace.py 的边界说明）。
//   - **未分组**：**虚拟桶，不是项目**。会话没有 cwd、或项目登记已被移除 / 目录
//     已被删掉时落进来。它没有实体，所以也没有 hover 卡 / 行菜单 —— 这是它与上面
//     「文件夹」的区别。桶**里面**的会话仍然是真实会话，照常有行内菜单。
//   - **对话**：可改名（PATCH 写一条 user 源标题事件 → 从此钉住，不再被自动标题覆盖）。

import { useEffect, useRef, useState } from 'react';
import { Plus, Search, RefreshCw, ChevronRight, Folder, Pencil, MoreHorizontal, Trash2 } from 'lucide-react';
import { W, R, WS } from '../theme';
import Modal from './Modal';
import { type WorkspaceSession, fmtRelTime } from './useWorkspace';

interface Props {
  sessions: WorkspaceSession[];
  selectedId: string | null;
  onSelect: (s: WorkspaceSession) => void;
  onNew: () => void;
  onRefresh: () => void;
  /** 会话改名。抛错即视为失败（调用方负责回滚显示），返回后由调用方刷新列表。 */
  onRename: (sessionId: string, title: string) => Promise<void>;
  /** **项目**改名（PATCH 登记行）。抛错即失败，弹窗保持打开并显示错误。 */
  onRenameProject: (projectId: number, title: string) => Promise<void>;
  /** **移除项目登记**（DELETE 登记行）。**必须先把这个项目从本地列表摘掉再
   *  resolve** —— 确认弹窗要等本地投影接受了这次移除才关（见 confirmRemove）。 */
  onRemoveProject: (projectId: number) => Promise<void>;
  loading: boolean;
}

/** 「未分组」这个**虚拟桶**的显示名。它不是一个真实项目，只是数据的归属兜底。 */
const UNGROUPED = '未分组';

/** 没有 agent 归属的会话（channel 不是 `agent:`）落这一组。 */
const NO_AGENT = '其他';

interface ProjectNode {
  /** 登记行主键，行菜单的改 / 删用它。 */
  id: number;
  /** 规范化路径字符串：折叠状态的键，也是项目身份（见 WorkspaceProject 的注释）。 */
  key: string;
  label: string;
  sessions: WorkspaceSession[];
}

interface AgentNode {
  key: string;
  label: string;
  projects: ProjectNode[];
  ungrouped: WorkspaceSession[];
  total: number;
}

/** 按 agent → project 建树。后端已按 updated_at 倒序返回，这里**保序**：
 *  组内顺序 = 首次出现顺序，也就是「最近有活动的组排在前面」。 */
function buildTree(sessions: WorkspaceSession[]): AgentNode[] {
  const agents: AgentNode[] = [];
  const byAgent = new Map<string, AgentNode>();
  const byProject = new Map<string, Map<string, ProjectNode>>();

  for (const s of sessions) {
    const akey = s.agent_key || NO_AGENT;
    let agent = byAgent.get(akey);
    if (!agent) {
      agent = { key: akey, label: s.agent_name || akey, projects: [], ungrouped: [], total: 0 };
      byAgent.set(akey, agent);
      byProject.set(akey, new Map());
      agents.push(agent);
    }
    agent.total += 1;

    if (!s.project) {
      // 没有项目归属 → 未分组。**不**给它编一个假项目名。
      agent.ungrouped.push(s);
      continue;
    }
    const projects = byProject.get(akey)!;
    const { id, key, label } = s.project;
    let project = projects.get(key);
    if (!project) {
      project = { id, key, label, sessions: [] };
      projects.set(key, project);
      agent.projects.push(project);
    }
    project.sessions.push(s);
  }
  return agents;
}

/** 行菜单里的一个动作。 */
function MenuItem({ icon, label, onClick, danger = false }: {
  icon: React.ReactNode;
  label: string;
  onClick: () => void;
  danger?: boolean;
}) {
  return (
    <button role="menuitem" onClick={onClick}
      className={`ws-btn${danger ? ' ws-danger-item' : ''}`}
      style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: WS.sm,
        padding: '6px 8px', borderRadius: R.xs, fontSize: 13, fontFamily: 'inherit',
        textAlign: 'left', color: danger ? W.danger : W.text,
      }}>
      {icon}{label}
    </button>
  );
}

/** 项目行的操作菜单（重命名 / 移除）。
 *
 *  定位用 ``position: fixed`` + 视口坐标：这一行住在 overflow:auto 的滚动容器里，
 *  absolute 浮层会被裁剪掉（同 AgentTurn 的用量浮层）。祖先链上没有 transform /
 *  filter / contain，fixed 逃得出裁剪，也就不需要 portal。
 *
 *  「移除」用垃圾桶 + danger 色 —— 它是这里唯一会撤销登记的动作。 */
function ProjectMenu({ anchor, onRename, onRemove, onClose }: {
  anchor: { left: number; top: number };
  onRename: () => void;
  onRemove: () => void;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onClose(); };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onClose]);

  // 全屏 backdrop 收「点到别处」；右键也算别处（不然右键会同时叠出第二个菜单）
  const dismiss = (e: React.MouseEvent) => { e.preventDefault(); onClose(); };
  return (
    <>
      <div onClick={onClose} onContextMenu={dismiss}
        style={{ position: 'fixed', inset: 0, zIndex: 60 }} />
      <div role="menu" style={{
        position: 'fixed', left: anchor.left, top: anchor.top, zIndex: 61,
        minWidth: 148, padding: 4, background: W.overlay,
        borderRadius: R.sm, boxShadow: W.elevPanel,
      }}>
        <MenuItem icon={<Pencil size={13} />} label="重命名" onClick={onRename} />
        <MenuItem icon={<Trash2 size={13} />} label="移除项目" danger onClick={onRemove} />
      </div>
    </>
  );
}

/** 标题来源的提示文案（放在行 tooltip 里）：一句话说清这个名字是怎么来的。 */
function sourceHint(s: WorkspaceSession): string {
  switch (s.title_source) {
    case 'user': return '已改名（不再自动变）';
    case 'provider': return '模型生成的标题';
    case 'fallback': return '根据首条消息生成';
    default: return '还没有消息，显示的是原标签';
  }
}

export default function WorkspaceSidebar({
  sessions, selectedId, onSelect, onNew, onRefresh, onRename,
  onRenameProject, onRemoveProject, loading,
}: Props) {
  const [q, setQ] = useState('');
  // **默认全收起**：侧栏是个导航，不是内容本身。全展开时几十个会话铺满一屏，
  // 想找的那条反而要滚半天——「一层层点下去」比「一眼扫一屏」更快找到东西。
  // 所以记的是**已展开**集合（空 = 全收起），和「已折叠」集合是反的。
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

  // ── 项目行的菜单与两个弹窗 ──
  //
  // 这三份状态都住在**行之上**（行组件是 renderProjectRow 这个内联函数，不持有
  // 任何状态）。原因见 Modal.tsx：移除成功后那一行会卸载，弹窗要是挂在行里就会
  // 跟着一起没 —— 失败时连错误都来不及显示。
  const [menuFor, setMenuFor] = useState<{ id: number; label: string; left: number; top: number } | null>(null);
  const [renameTarget, setRenameTarget] = useState<{ id: number; label: string } | null>(null);
  const [renameDraft, setRenameDraft] = useState('');
  const [renameBusy, setRenameBusy] = useState(false);
  const [renameError, setRenameError] = useState<string | null>(null);
  const [removeTarget, setRemoveTarget] = useState<{ id: number; label: string } | null>(null);
  const [removeBusy, setRemoveBusy] = useState(false);
  const [removeError, setRemoveError] = useState<string | null>(null);

  /** 在元素下方弹出项目菜单（视口坐标；右侧与底部留出余量防止出界）。 */
  function showProjectMenu(p: ProjectNode, el: HTMLElement) {
    const r = el.getBoundingClientRect();
    setMenuFor({
      id: p.id, label: p.label,
      left: Math.min(Math.max(8, r.left), Math.max(8, window.innerWidth - 164)),
      top: Math.min(r.bottom + 4, Math.max(8, window.innerHeight - 92)),
    });
  }

  function startRenameProject(t: { id: number; label: string }) {
    setMenuFor(null);
    setRenameTarget(t);
    setRenameDraft(t.label);
    setRenameError(null);
  }

  /** 关改名弹窗。提交中一律无效 —— 关闭键 / Cancel / Escape（Modal 那边也挡了一层）。 */
  function closeRenameProject() {
    if (renameBusy) return;
    setRenameTarget(null);
    setRenameError(null);
  }

  async function confirmRenameProject() {
    if (renameBusy || !renameTarget) return;
    const next = renameDraft.trim();
    if (!next || next === renameTarget.label) { closeRenameProject(); return; }
    setRenameBusy(true);
    setRenameError(null);
    try {
      await onRenameProject(renameTarget.id, next);
      setRenameTarget(null);
    } catch (e: any) {
      // 失败：弹窗留着、错误摆出来、列表不动。关掉弹窗等于假装改名成功了
      setRenameError(e?.message || String(e));
    } finally {
      setRenameBusy(false);
    }
  }

  function startRemoveProject(t: { id: number; label: string }) {
    setMenuFor(null);
    setRemoveTarget(t);
    setRemoveError(null);
  }

  function closeRemoveProject() {
    if (removeBusy) return;
    setRemoveTarget(null);
    setRemoveError(null);
  }

  async function confirmRemoveProject() {
    if (removeBusy || !removeTarget) return;
    setRemoveBusy(true);
    setRemoveError(null);
    try {
      await onRemoveProject(removeTarget.id);
      // 到这里调用方已经把这一行从本地列表摘掉了（其会话也改成了未分组）。
      // 顺序不能反：先关窗会在中间那一帧露出一个「已经删了却还在列表里」的行，
      // 而下一次项目操作（比如再点一次移除）就会瞄着一份过期的列表。
      setRemoveTarget(null);
    } catch (e: any) {
      // 失败：弹窗不关、列表不变（验收第 3 条）
      setRemoveError(e?.message || String(e));
    } finally {
      setRemoveBusy(false);
    }
  }

  const needle = q.trim().toLowerCase();
  // 搜索同时匹配标题、项目名、智能体名：只在标题里找的话，用户按项目名搜会一无所获，
  // 而「这个项目下有哪些会话」正是这棵树要回答的问题。
  const filtered = needle
    ? sessions.filter(s =>
        [s.title, s.label, s.project?.label, s.agent_name].some(
          v => v && v.toLowerCase().includes(needle)))
    : sessions;
  const agents = buildTree(filtered);

  // 搜索时强制全展开：折叠状态是给浏览用的，搜索的意图是「找出来给我看」
  const isOpen = (key: string) => !!needle || expanded.has(key);
  const toggle = (key: string) => setExpanded(prev => {
    const next = new Set(prev);
    if (next.has(key)) next.delete(key); else next.add(key);
    return next;
  });

  // 选中会话时，把它所在的那条链展开 —— 否则「选中了却看不见」，最明显的是
  // 刷新页面：选中会话从 localStorage 恢复，而树默认全收起，用户看到的是一片
  // 没有高亮的组标题。新建会话后同理。
  //
  // lastExpanded 这层记忆是必须的：只在**选中项变化**时动，不在 sessions 刷新时
  // 反复动。否则用户手动收起含选中会话的那组，下一次刷新（每轮对话都刷）就会
  // 被这行代码顶开，形成「收不下去」的手感。
  const lastExpanded = useRef<string | null>(null);
  useEffect(() => {
    if (!selectedId || lastExpanded.current === selectedId) return;
    const s = sessions.find(x => x.session_id === selectedId);
    if (!s) return;   // 列表还没加载完 → 等 sessions 到达后这次 effect 再跑一遍
    lastExpanded.current = selectedId;
    const akey = `agent:${s.agent_key || NO_AGENT}`;
    const pkey = s.project ? `project:${s.project.key}` : `ungrouped:${s.agent_key || NO_AGENT}`;
    setExpanded(prev => new Set([...prev, akey, pkey]));
  }, [selectedId, sessions]);

  function startRename(s: WorkspaceSession) {
    setEditingId(s.session_id);
    setDraft(s.title);
  }

  /** 提交改名。空串 / 未变 → 当作取消（改名目标是「换个名字」，不是清空）。 */
  async function commitRename(s: WorkspaceSession) {
    if (saving) return;
    const next = draft.trim();
    setEditingId(null);
    if (!next || next === s.title) return;
    setSaving(true);
    try {
      await onRename(s.session_id, next);
    } catch {
      // 失败不弹窗：列表刷新后会回到服务端的真实值，那本身就是最准的反馈
    } finally {
      setSaving(false);
    }
  }

  /** 是否处于「按了 Esc 正在收尾」——用来把 blur 和「放弃编辑」区分开。
   *
   *  为什么需要它：Esc 之后输入框会被卸载，而浏览器/React 在这条路径上仍可能补
   *  一次 blur 回调。不标记的话，「放弃」会被当成「提交」，用户按 Esc 反而改了名。 */
  const abandoning = useRef(false);

  function abandonRename() {
    abandoning.current = true;
    setEditingId(null);
  }

  /** 一个会话行。缩进到第三层，标题 + 次级元信息，悬停出行内改名入口。 */
  function renderSession(s: WorkspaceSession) {
    const active = s.session_id === selectedId;
    const editing = editingId === s.session_id;
    return (
      <div key={s.session_id} onClick={() => !editing && onSelect(s)}
        title={editing ? undefined : `${s.title}\n${sourceHint(s)}`}
        className="ws-item ws-hover-host" data-active={active}
        style={{
          position: 'relative', display: 'flex', alignItems: 'center', gap: WS.sm,
          padding: '6px 8px 6px 30px', borderRadius: R.sm, marginBottom: 1,
        }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          {editing ? (
            <input
              autoFocus value={draft} disabled={saving}
              onChange={e => setDraft(e.target.value)}
              onClick={e => e.stopPropagation()}
              onKeyDown={e => {
                if (e.key === 'Enter') { e.preventDefault(); commitRename(s); }
                if (e.key === 'Escape') { e.preventDefault(); abandonRename(); }
              }}
              // 点到别处 = 「我改完了」：提交而不是丢弃。丢弃只留给 Esc ——
              // 提交是多数人的预期，而误提交可以再改一次，误丢弃只能重打一遍。
              onBlur={() => {
                if (abandoning.current) { abandoning.current = false; return; }
                if (editingId === s.session_id) commitRename(s);
              }}
              style={{
                width: '100%', background: W.surface, border: 'none', borderRadius: R.xs,
                outline: `1px solid ${W.accent}`, color: W.text, fontSize: 13,
                fontFamily: 'inherit', padding: '1px 5px', lineHeight: '18px',
              }} />
          ) : (
            <div style={{
              fontSize: 13, lineHeight: '20px', color: active ? W.text : W.secondary,
              whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis',
            }}>
              {s.title}
            </div>
          )}
          {!editing && (
            <div style={{ fontSize: 11, lineHeight: '16px', color: W.dimmed, marginTop: 1 }}>
              {fmtRelTime(s.updated_at)}{s.entries > 0 ? ` · ${s.entries} 个文件` : ''}
            </div>
          )}
        </div>
        {!editing && (
          <button
            title="重命名" className="ws-btn ws-on-hover ws-round"
            onClick={e => { e.stopPropagation(); startRename(s); }}
            style={{ padding: 2, color: W.dimmed, display: 'flex', flexShrink: 0, borderRadius: R.xs }}>
            <Pencil size={12} />
          </button>
        )}
      </div>
    );
  }

  /** 分组头的公共外观（智能体 / 项目行共用；项目行另加菜单入口）。 */
  function groupHeadStyle(depth: 0 | 1): React.CSSProperties {
    return {
      width: '100%', display: 'flex', alignItems: 'center', gap: WS.xs,
      padding: `5px 6px 5px ${depth === 0 ? 6 : 18}px`, borderRadius: R.sm,
      color: depth === 0 ? W.secondary : W.tertiary,
      fontSize: 12, fontFamily: 'inherit', textAlign: 'left',
      fontWeight: depth === 0 ? 600 : 500,
      letterSpacing: '0.02em',
    };
  }

  /**
   * 一级（智能体）与「未分组」的分组头。**只有折叠** —— 前者是注册表里的定义，
   * 后者连实体都没有（见文件头注释），都没有可改可删的东西。
   *
   * 「未分组」的 ``squashed`` 让它比项目行淡一档：它是归属兜底，不是「最不活跃的
   * 项目」。这里也**没有** hover 卡、没有行菜单 —— 与真实项目行的区别正在于此。
   */
  function renderGroupHead(
    key: string, label: string, count: number, depth: 0 | 1,
    opts: { squashed?: boolean } = {},
  ) {
    const open = isOpen(key);
    return (
      <button className="ws-btn ws-hover" onClick={() => toggle(key)} style={groupHeadStyle(depth)}>
        <ChevronRight size={12} style={{
          flexShrink: 0, transition: 'transform .12s ' + W.ease,
          transform: open ? 'rotate(90deg)' : 'none',
        }} />
        <span style={{
          flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden',
          textOverflow: 'ellipsis', opacity: opts.squashed ? 0.75 : 1,
        }}>
          {label}
        </span>
        <span style={{ flexShrink: 0, fontSize: 11, color: W.dimmed, fontWeight: 400 }}>{count}</span>
      </button>
    );
  }

  /**
   * 一个**项目**行：文件夹图标 + 名字 + 会话数，悬停浮出 ⋯ 菜单。
   *
   * 与 renderGroupHead 的区别全在「它是一个真实实体」：有 hover 动作、有行菜单
   * （重命名 / 移除），也因此行菜单里的两个动作改的都是**登记**而不是目录。
   *
   * 折叠按钮与 ⋯ 按钮是**兄弟**（不是嵌套）：⋯ 绝对定位压在行右端，点它不会
   * 冒泡到折叠按钮。右键也弹同一份菜单 —— 右键菜单与 ⋯ 是两个入口、一份菜单。
   */
  function renderProjectRow(p: ProjectNode) {
    const open = isOpen(`project:${p.key}`);
    return (
      <div className="ws-hover-host" style={{ position: 'relative' }}
        // 右键菜单：与 ⋯ 同一个入口函数，只是锚点取整行而不是按钮
        onContextMenu={e => {
          e.preventDefault();
          e.stopPropagation();
          showProjectMenu(p, e.currentTarget);
        }}>
        <button className="ws-btn ws-hover" onClick={() => toggle(`project:${p.key}`)}
          style={{ ...groupHeadStyle(1), paddingRight: 26 }}>
          <ChevronRight size={12} style={{
            flexShrink: 0, transition: 'transform .12s ' + W.ease,
            transform: open ? 'rotate(90deg)' : 'none',
          }} />
          <Folder size={12} style={{ flexShrink: 0, opacity: 0.8 }} />
          <span style={{ flex: 1, minWidth: 0, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {p.label}
          </span>
          <span style={{ flexShrink: 0, fontSize: 11, color: W.dimmed, fontWeight: 400 }}>{p.sessions.length}</span>
        </button>
        <button className="ws-btn ws-round ws-on-hover" title="项目操作"
          onClick={e => { e.stopPropagation(); showProjectMenu(p, e.currentTarget); }}
          style={{
            position: 'absolute', right: 4, top: '50%', transform: 'translateY(-50%)',
            padding: 2, color: W.dimmed, display: 'flex', borderRadius: R.xs,
          }}>
          <MoreHorizontal size={13} />
        </button>
      </div>
    );
  }

  /** 弹窗按钮的公共外观（高度/圆角/字号与侧栏其他按钮同一套）。 */
  const dialogBtn: React.CSSProperties = {
    height: 32, padding: '0 14px', borderRadius: R.sm, fontSize: 13, fontFamily: 'inherit',
  };

  return (
    <>
    <div style={{
      width: 280, flexShrink: 0, background: W.panel,
      display: 'flex', flexDirection: 'column', minHeight: 0, fontFamily: W.font,
    }}>
      {/* 新建会话 */}
      <div style={{ padding: `${WS.md}px ${WS.md}px ${WS.sm}px` }}>
        <button onClick={onNew} className="ws-btn"
          style={{
            width: '100%', display: 'flex', alignItems: 'center', gap: WS.sm,
            height: 34, padding: '0 10px', borderRadius: R.sm,
            color: W.text, fontSize: 13, fontFamily: 'inherit', textAlign: 'left',
          }}>
          <Plus size={15} color={W.secondary} />
          新建会话
        </button>
      </div>

      {/* 搜索 */}
      <div style={{ padding: `0 ${WS.md}px ${WS.sm}px` }}>
        <div style={{
          display: 'flex', alignItems: 'center', gap: WS.sm, height: 30, padding: '0 8px',
          borderRadius: R.sm, background: W.surface, boxShadow: `inset 0 0 0 0.5px ${W.borderSoft}`,
        }}>
          <Search size={13} color={W.dimmed} />
          <input value={q} onChange={e => setQ(e.target.value)} placeholder="搜索会话"
            style={{ flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none', color: W.text, fontSize: 13, fontFamily: 'inherit' }} />
          <button onClick={onRefresh} title="刷新会话列表"
            className="ws-btn ws-round"
            style={{ padding: 2, color: W.dimmed, display: 'flex' }}>
            <RefreshCw size={13} className={loading ? 'ws-spin' : ''} />
          </button>
        </div>
      </div>

      {/* 会话树 */}
      <div style={{ flex: 1, minHeight: 0, overflowY: 'auto', padding: `0 ${WS.sm}px ${WS.sm}px` }}>
        {!loading && filtered.length === 0 && (
          <div style={{ padding: `${WS.xxl}px ${WS.base}px`, textAlign: 'center', color: W.dimmed, fontSize: 13, lineHeight: '20px', whiteSpace: 'pre-line' }}>
            {needle ? '没有匹配的会话' : '还没有会话\n开始一次对话后，模型用工具落下的文件会出现在这里'}
          </div>
        )}
        {agents.map(agent => (
          <div key={agent.key} style={{ marginBottom: WS.md }}>
            {renderGroupHead(`agent:${agent.key}`, agent.label, agent.total, 0)}
            {isOpen(`agent:${agent.key}`) && (
              <div style={{ marginTop: 1 }}>
                {agent.projects.map(p => (
                  <div key={p.key} style={{ marginBottom: WS.xs }}>
                    {renderProjectRow(p)}
                    {isOpen(`project:${p.key}`) && p.sessions.map(renderSession)}
                  </div>
                ))}
                {/* 未分组永远排在**最后**：它是归属兜底，不是「最活跃的项目」 */}
                {agent.ungrouped.length > 0 && (
                  <div style={{ marginTop: WS.xs }}>
                    {renderGroupHead(`ungrouped:${agent.key}`, UNGROUPED, agent.ungrouped.length, 1, { squashed: true })}
                    {isOpen(`ungrouped:${agent.key}`) && agent.ungrouped.map(renderSession)}
                  </div>
                )}
              </div>
            )}
          </div>
        ))}
      </div>
    </div>

    {/* 项目行菜单。挂在这里而不是行里：行的重挂（列表每轮对话都刷）不该把菜单
        抖掉，而菜单里的动作会打开下面两个弹窗、由它们接手 */}
    {menuFor && (
      <ProjectMenu
        anchor={{ left: menuFor.left, top: menuFor.top }}
        onClose={() => setMenuFor(null)}
        onRename={() => startRenameProject({ id: menuFor.id, label: menuFor.label })}
        onRemove={() => startRemoveProject({ id: menuFor.id, label: menuFor.label })}
      />
    )}

    {/* 改名（PATCH 登记行）。**只改显示名**，不动目录、不动会话 */}
    <Modal
      open={renameTarget !== null}
      title="重命名项目"
      description={renameTarget ? `给「${renameTarget.label}」换个显示名。只改登记里的名字，目录本身不变。` : undefined}
      busy={renameBusy}
      onClose={closeRenameProject}
      footer={<>
        <button className="ws-btn ws-btn-outline" disabled={renameBusy}
          onClick={closeRenameProject} style={dialogBtn}>取消</button>
        <button className="ws-btn ws-btn-primary"
          disabled={renameBusy || !renameDraft.trim()}
          onClick={confirmRenameProject} style={dialogBtn}>
          {renameBusy ? '保存中…' : '保存'}
        </button>
      </>}>
      <input
        autoFocus value={renameDraft} disabled={renameBusy}
        aria-label="项目名称"
        onChange={e => { setRenameDraft(e.target.value); setRenameError(null); }}
        onKeyDown={e => {
          if (e.key === 'Enter') { e.preventDefault(); confirmRenameProject(); }
        }}
        style={{
          marginTop: WS.md, width: '100%', background: W.surface, border: 'none',
          borderRadius: R.sm, outline: `1px solid ${W.borderStrong}`, color: W.text,
          fontSize: 13, fontFamily: 'inherit', padding: '7px 10px',
        }} />
      {renameError && (
        <div role="alert" style={{ marginTop: WS.sm, fontSize: 13, lineHeight: '20px', color: W.danger }}>
          {renameError}
        </div>
      )}
    </Modal>

    {/* 移除项目。确认文案必须写明**三个后果**，缺一不可 —— 用户点下去之前要能
        看清「删的是什么、留下的又是什么」 */}
    <Modal
      open={removeTarget !== null}
      title="移除项目"
      description={removeTarget ? `将「${removeTarget.label}」从项目列表中移除。` : undefined}
      busy={removeBusy}
      onClose={closeRemoveProject}
      footer={<>
        <button className="ws-btn ws-btn-outline" disabled={removeBusy}
          onClick={closeRemoveProject} style={dialogBtn}>取消</button>
        <button className="ws-btn ws-btn-danger" disabled={removeBusy}
          onClick={confirmRemoveProject}
          style={{ ...dialogBtn, display: 'flex', alignItems: 'center', gap: 6 }}>
          <Trash2 size={13} />移除项目
        </button>
      </>}>
      <ul style={{
        margin: `${WS.sm}px 0 0`, paddingLeft: 18,
        fontSize: 13, lineHeight: '20px', color: W.tertiary,
      }}>
        <li>文件夹与会话记录会保留，磁盘上不会删掉任何文件</li>
        <li>它下面的会话会显示在「未分组」下</li>
        <li>重新添加同一个文件夹只会收养之后新建的会话，这些旧会话不会回来</li>
      </ul>
      {removeBusy && (
        <div role="status" style={{ marginTop: WS.sm, fontSize: 13, color: W.dimmed }}>
          正在移除项目…
        </div>
      )}
      {removeError && (
        <div role="alert" style={{ marginTop: WS.sm, fontSize: 13, lineHeight: '20px', color: W.danger }}>
          {removeError}
        </div>
      )}
    </Modal>
    </>
  );
}
