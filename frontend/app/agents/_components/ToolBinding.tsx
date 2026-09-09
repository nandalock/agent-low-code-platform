'use client';

// ToolBinding — Agent 的工具绑定（与工具来源正交）
//
// 数据源 /api/mcp/tools/all 返回**所有**工具（内建 + 各 MCP server），按
// server_name 分组渲染；内建工具（沙箱 bash / python）与 MCP 工具平级，
// 只是来源标签不同。
//
// 绑定走 /api/tools/bindings/{agentKey}（全量替换语义，每次勾选提交完整列表）。

import { useState, useEffect } from 'react';
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

  async function toggle(toolName: string, on: boolean) {
    const next = on ? [...bound, toolName] : bound.filter(t => t !== toolName);
    setBound(next);
    await fetch(`${API}/api/tools/bindings/${agentKey}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    });
  }

  // 按来源分组（内建 → "builtin"，MCP → server 名）
  const groups = tools.reduce<Record<string, ToolInfo[]>>((acc, t) => {
    const key = t.server_name || '其他';
    (acc[key] ||= []).push(t);
    return acc;
  }, {});

  return (
    <div>
      <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>
        工具绑定
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
      <div style={{ display:'flex', flexDirection:'column', gap:S.md }}>
        {Object.entries(groups).map(([source, list]) => (
          <div key={source}>
            <div style={{ fontSize:10, color:T.tertiary, marginBottom:2 }}>{source}</div>
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
        ))}
        {tools.length === 0 && <div style={{ fontSize:11, color:T.tertiary }}>无可用工具</div>}
      </div>
    </div>
  );
}
