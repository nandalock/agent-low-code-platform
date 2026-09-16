'use client';

// ToolBinding — Agent 的工具绑定（与工具来源正交）
//
// 数据源 /api/mcp/tools/all 返回**所有**工具（内建 + 各 MCP server），按
// server_name 分组渲染；内建工具（沙箱 bash / python）与 MCP 工具平级，
// 只是来源标签不同。
//
// **组默认收起。** 一个 MCP 导入一次就带来几十个工具（playwright 二十几个），
// 平铺出来整块面板全是复选框，反而看不出「这个 agent 到底绑了什么」——这里
// 要管理的对象是**组**，不是单个工具。所以：
//
//   标题行 = 折叠开关，右侧 n/N 显示该组的绑定情况（收起时也一眼可见）
//   标题行的复选框 = 组级开关：勾 = 绑定该来源下**全部**工具，半选 = 部分绑定
//
// 要挑单个工具时展开那一组即可，不必全局展开。
//
// 绑定走 /api/tools/bindings/{agentKey}（全量替换语义，每次提交完整列表）。

import { useState, useEffect } from 'react';
import { ChevronDown, ChevronRight } from 'lucide-react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface ToolInfo {
  name: string;
  description: string;
  server_name: string;
}

interface Props { agentKey: string; }

export default function ToolBinding({ agentKey }: Props) {
  const [tools, setTools] = useState<ToolInfo[]>([]);
  const [bound, setBound] = useState<string[]>([]);
  const [sandboxOn, setSandboxOn] = useState(true);
  // 只记**展开**的组：默认空集 = 全部收起，且新导入的 MCP 自动是收起的
  const [open, setOpen] = useState<Set<string>>(new Set());

  useEffect(() => { fetchData(); }, [agentKey]);

  async function fetchData() {
    try {
      const [tr, br, cr] = await Promise.all([
        fetch(`${API}/api/mcp/tools/all`).then(r => r.json()),
        fetch(`${API}/api/tools/bindings`).then(r => r.json()),
        fetch(`${API}/api/tools/capabilities/sandbox`).then(r => r.json()),
      ]);
      setTools(Array.isArray(tr) ? tr : []);
      setBound(br[agentKey] || []);
      setSandboxOn(cr?.enabled !== false);
    } catch {}
  }

  // 能力开关：停用时内建工具从注册表消失 → 重新拉列表即可看到它们不见了
  async function toggleSandbox(on: boolean) {
    setSandboxOn(on);
    await fetch(`${API}/api/tools/capabilities/sandbox`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ enabled: on }),
    });
    await fetchData();
  }

  // 全量替换。走 Set 去重：同名工具重复出现会把列表写重，而绑定是集合语义
  async function save(next: Iterable<string>) {
    const list = [...new Set(next)];
    setBound(list);
    await fetch(`${API}/api/tools/bindings/${agentKey}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(list),
    });
  }

  function toggle(toolName: string, on: boolean) {
    save(on ? [...bound, toolName] : bound.filter(t => t !== toolName));
  }

  // 组级开关：一次提交这一组的全部工具，不是逐个 PUT
  function toggleGroup(list: ToolInfo[], on: boolean) {
    const next = new Set(bound);
    for (const t of list) {
      if (on) next.add(t.name);
      else next.delete(t.name);
    }
    save(next);
  }

  function toggleOpen(source: string) {
    setOpen(prev => {
      const next = new Set(prev);
      if (next.has(source)) next.delete(source);
      else next.add(source);
      return next;
    });
  }

  // 按来源分组（内建 → "builtin"，MCP → server 名）
  const groups = tools.reduce<Record<string, ToolInfo[]>>((acc, t) => {
    const key = t.server_name || '其他';
    (acc[key] ||= []).push(t);
    return acc;
  }, {});
  const sources = Object.keys(groups);
  const allOpen = sources.length > 0 && sources.every(s => open.has(s));

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', marginBottom:S.sm }}>
        <span style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase' }}>
          工具绑定
        </span>
        <div style={{ flex:1 }} />
        {sources.length > 0 && (
          <button
            onClick={() => setOpen(allOpen ? new Set<string>() : new Set(sources))}
            style={{ background:'none', border:'none', padding:0, cursor:'pointer', fontSize:11, color:T.accent, fontFamily:'inherit' }}>
            {allOpen ? '全部收起' : '全部展开'}
          </button>
        )}
      </div>
      {/* 能力开关：管「这个部署有没有沙箱工具」，与下面「哪个 agent 能用」是两层 */}
      <label style={{
        display:'flex', alignItems:'center', gap:6, marginBottom:S.md,
        fontSize:12, cursor:'pointer', color:T.text,
      }}>
        <input type="checkbox" checked={sandboxOn}
          onChange={e => toggleSandbox(e.target.checked)}
          style={{ accentColor: T.accent, width:14, height:14, cursor:'pointer' }} />
        <span style={{ fontWeight:600 }}>沙箱能力</span>
        <span style={{ fontSize:10, color: sandboxOn ? T.success : T.tertiary }}>
          {sandboxOn ? '已启用' : '已停用'}
        </span>
      </label>
      <div style={{ display:'flex', flexDirection:'column', gap:S.xs }}>
        {Object.entries(groups).map(([source, list]) => {
          const isOpen = open.has(source);
          const checkedCount = list.filter(t => bound.includes(t.name)).length;
          const allChecked = checkedCount === list.length;
          const partial = checkedCount > 0 && !allChecked;
          return (
            <div key={source} style={{
              border:`1px solid ${T.border}`, borderRadius:6, background:T.surface, overflow:'hidden',
            }}>
              <div onClick={() => toggleOpen(source)} style={{
                display:'flex', alignItems:'center', gap:6, padding:`${S.xs}px ${S.sm}px`,
                cursor:'pointer', userSelect:'none',
              }}>
                {isOpen
                  ? <ChevronDown size={13} color={T.secondary} />
                  : <ChevronRight size={13} color={T.secondary} />}
                {/* 组级开关：点它只切换整组绑定，不折叠（stopPropagation） */}
                <input type="checkbox" checked={allChecked}
                  ref={el => { if (el) el.indeterminate = partial; }}
                  onClick={e => e.stopPropagation()}
                  onChange={e => toggleGroup(list, e.target.checked)}
                  title={allChecked ? `取消绑定 ${source} 的全部工具` : `绑定 ${source} 的全部工具`}
                  style={{ accentColor:T.accent, width:14, height:14, cursor:'pointer' }} />
                <span style={{
                  flex:1, fontSize:12, fontWeight:600, color:T.text,
                  overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap',
                }}>
                  {source}
                </span>
                <span style={{
                  fontSize:10, fontWeight: checkedCount ? 600 : 400,
                  color: checkedCount ? T.accent : T.tertiary,
                }}>
                  {checkedCount}/{list.length}
                </span>
              </div>
              {isOpen && (
                <div style={{ padding:`${S.xs}px ${S.sm}px ${S.sm}px 26px`, borderTop:`1px solid ${T.border}` }}>
                  <div style={{ display:'flex', flexDirection:'column', gap:S.xs }}>
                    {list.map(t => {
                      const checked = bound.includes(t.name);
                      return (
                        <label key={t.name} style={{
                          display:'flex', alignItems:'center', gap:6, padding:'3px 0',
                          fontSize:12, cursor:'pointer', color:T.text,
                        }} title={t.description}>
                          <input type="checkbox" checked={checked}
                            onChange={e => toggle(t.name, e.target.checked)}
                            style={{ accentColor: T.accent, width:14, height:14, cursor:'pointer' }} />
                          <span style={{ fontFamily:'monospace', fontSize:11 }}>{t.name}</span>
                        </label>
                      );
                    })}
                  </div>
                </div>
              )}
            </div>
          );
        })}
        {tools.length === 0 && <div style={{ fontSize:11, color:T.tertiary }}>无可用工具</div>}
      </div>
    </div>
  );
}
