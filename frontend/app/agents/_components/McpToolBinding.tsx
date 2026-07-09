'use client';
import { useState, useEffect } from 'react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Props { agentKey: string; }

export default function McpToolBinding({ agentKey }: Props) {
  const [allTools, setAllTools] = useState<{server:string; tools:{name:string;description:string}[]}[]>([]);
  const [bound, setBound] = useState<string[]>([]);

  useEffect(() => { fetchData(); }, [agentKey]);

  async function fetchData() {
    try {
      const [sr, br] = await Promise.all([
        fetch(`${API}/api/mcp/servers`).then(r => r.json()),
        fetch(`${API}/api/mcp/bindings`).then(r => r.json()),
      ]);
      // 拉每个 server 的工具列表
      const st: typeof allTools = [];
      for (const s of sr) {
        try {
          const r = await fetch(`${API}/api/mcp/servers/${s.id}/tools`, { method: 'POST' });
          const tools = await r.json();
          st.push({ server: s.name, tools });
        } catch {}
      }
      setAllTools(st);
      setBound(br[agentKey] || []);
    } catch {}
  }

  async function toggle(toolName: string, on: boolean) {
    const next = on ? [...bound, toolName] : bound.filter(t => t !== toolName);
    setBound(next);
    await fetch(`${API}/api/mcp/bindings/${agentKey}`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(next),
    });
  }

  return (
    <div>
      <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>
        MCP 工具绑定
      </div>
      <div style={{ display:'flex', flexDirection:'column', gap:S.xs }}>
        {allTools.flatMap(st => st.tools.map(t => ({ ...t, server: st.server }))).map(t => {
          const checked = bound.includes(t.name);
          return (
            <label key={t.name} style={{
              display:'flex', alignItems:'center', gap:6, padding:'3px 0',
              fontSize:12, cursor:'pointer', color:T.text,
            }}>
              <input type="checkbox" checked={checked}
                onChange={e => toggle(t.name, e.target.checked)}
                style={{ accentColor: T.accent, width:14, height:14, cursor:'pointer' }} />
              <span style={{ fontFamily:'monospace', fontSize:11 }}>{t.name}</span>
              <span style={{ fontSize:10, color:T.tertiary, marginLeft:'auto' }}>{t.server}</span>
            </label>
          );
        })}
        {allTools.length === 0 && <div style={{ fontSize:11, color:T.tertiary }}>无可用 MCP 服务</div>}
      </div>
    </div>
  );
}
