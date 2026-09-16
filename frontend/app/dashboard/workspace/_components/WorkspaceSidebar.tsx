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
//   - **文件夹**：一个真实目录。侧栏这里也**不给改名 / 删除** —— 改目录名等于
//     移动文件，而工作区有意只开了「读 + 建空条目」两个窄写入口，没有移动 / 改名
//     （见 api/workspace.py 的边界说明）。给了按钮却没接口，比没有按钮更糟。
//   - **未分组**：**虚拟桶，不是项目**。会话没有 cwd、或项目目录已被删掉时落进来。
//     它没有实体，所以也没有 hover / 右键菜单 —— 这是它与上面「文件夹」的区别。
//     桶**里面**的会话仍然是真实会话，照常有行内菜单。
//   - **对话**：可改名（PATCH 写一条 user 源标题事件 → 从此钉住，不再被自动标题覆盖）。

import { useEffect, useRef, useState } from 'react';
import { Plus, Search, RefreshCw, ChevronRight, Folder, Pencil } from 'lucide-react';
import { W, R, WS } from '../theme';
import { type WorkspaceSession, fmtRelTime } from './useWorkspace';

interface Props {
  sessions: WorkspaceSession[];
  selectedId: string | null;
  onSelect: (s: WorkspaceSession) => void;
  onNew: () => void;
  onRefresh: () => void;
  /** 改名。抛错即视为失败（调用方负责回滚显示），返回后由调用方刷新列表。 */
  onRename: (sessionId: string, title: string) => Promise<void>;
  loading: boolean;
}

/** 「未分组」这个**虚拟桶**的显示名。它不是一个真实项目，只是数据的归属兜底。 */
const UNGROUPED = '未分组';

/** 没有 agent 归属的会话（channel 不是 `agent:`）落这一组。 */
const NO_AGENT = '其他';

interface ProjectNode {
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
    let project = projects.get(s.project.key);
    if (!project) {
      project = { key: s.project.key, label: s.project.label, sessions: [] };
      projects.set(s.project.key, project);
      agent.projects.push(project);
    }
    project.sessions.push(s);
  }
  return agents;
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

export default function WorkspaceSidebar({ sessions, selectedId, onSelect, onNew, onRefresh, onRename, loading }: Props) {
  const [q, setQ] = useState('');
  // **默认全收起**：侧栏是个导航，不是内容本身。全展开时几十个会话铺满一屏，
  // 想找的那条反而要滚半天——「一层层点下去」比「一眼扫一屏」更快找到东西。
  // 所以记的是**已展开**集合（空 = 全收起），和「已折叠」集合是反的。
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [editingId, setEditingId] = useState<string | null>(null);
  const [draft, setDraft] = useState('');
  const [saving, setSaving] = useState(false);

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

  /** 一级 / 二级的分组头。**只有折叠**，没有改名 / 删除等菜单（见文件头注释）。 */
  function renderGroupHead(
    key: string, label: string, count: number, depth: 0 | 1,
    opts: { icon?: boolean; squashed?: boolean } = {},
  ) {
    const open = isOpen(key);
    return (
      <button className="ws-btn ws-hover" onClick={() => toggle(key)}
        style={{
          width: '100%', display: 'flex', alignItems: 'center', gap: WS.xs,
          padding: `5px 6px 5px ${depth === 0 ? 6 : 18}px`, borderRadius: R.sm,
          color: depth === 0 ? W.secondary : W.tertiary,
          fontSize: 12, fontFamily: 'inherit', textAlign: 'left',
          fontWeight: depth === 0 ? 600 : 500,
          letterSpacing: '0.02em',
        }}>
        <ChevronRight size={12} style={{
          flexShrink: 0, transition: 'transform .12s ' + W.ease,
          transform: open ? 'rotate(90deg)' : 'none',
        }} />
        {opts.icon && <Folder size={12} style={{ flexShrink: 0, opacity: 0.8 }} />}
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

  return (
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
                    {renderGroupHead(`project:${p.key}`, p.label, p.sessions.length, 1, { icon: true })}
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
  );
}
