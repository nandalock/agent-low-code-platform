'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { T, S, btnPrimary } from '@/app/theme';
import { Wrench, Circle, ChevronRight, Server, Globe, Terminal, Plus, X, Trash2, Box, Code2 } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface McpServer {
  id: number;
  name: string;
  transport: 'http' | 'stdio';
  url?: string;
  command?: string;
  args?: string;
}

const TRANSPORT_ICON: Record<string, React.ReactNode> = {
  http: <Globe size={13} />,
  stdio: <Terminal size={13} />,
};

function subtitle(s: McpServer): string {
  if (s.transport === 'http') return s.url || '';
  return `${s.command || 'python'} ${s.args || ''}`;
}

export default function McpListPage() {
  const router = useRouter();
  const [servers, setServers] = useState<McpServer[]>([]);
  const [statuses, setStatuses] = useState<Record<number, boolean | null>>({});
  const [showModal, setShowModal] = useState(false);

  const [importJson, setImportJson] = useState('');
  const [importError, setImportError] = useState('');

  const MCP_EXAMPLES = [
    {
      type: 'npx', label: 'Node.js 包',
      icon: <Code2 size={14} />,
      color: '#00B42A',
      desc: '最常见的 MCP Server 形式，一行 npx 安装运行',
      json: JSON.stringify({
        mcpServers: {
          server_name: {
            command: "npx",
            args: ["-y", "@modelcontextprotocol/server-filesystem", "/allowed/path"],
          },
        },
      }, null, 2),
    },
    {
      type: 'http', label: 'HTTP 远程',
      icon: <Globe size={14} />,
      color: '#3370FF',
      desc: '已部署好的 MCP 服务，通过 URL 连接',
      json: JSON.stringify({
        mcpServers: {
          server_name: {
            url: "https://mcp.example.com/mcp",
          },
        },
      }, null, 2),
    },
    {
      type: 'docker', label: 'Docker 容器',
      icon: <Box size={14} />,
      color: '#F59E0B',
      desc: '通过 Docker 运行，适合有环境依赖的服务',
      json: JSON.stringify({
        mcpServers: {
          server_name: {
            command: "docker",
            args: ["run", "-i", "--rm", "ghcr.io/org/server-name"],
            env: {
              API_KEY: "your_token_here",
            },
          },
        },
      }, null, 2),
    },
    {
      type: 'uvx', label: 'Python 包',
      icon: <Terminal size={14} />,
      color: '#8B5CF6',
      desc: '通过 uvx 运行 Python MCP 包，无需手动安装',
      json: JSON.stringify({
        mcpServers: {
          server_name: {
            command: "uvx",
            args: ["mcp-server-package-name"],
          },
        },
      }, null, 2),
    },
  ];

  async function fetchServers() {
    try {
      const r = await fetch(`${API}/api/mcp/servers`);
      const list: McpServer[] = await r.json();
      setServers(list);
      for (const s of list) {
        fetch(`${API}/api/mcp/servers/${s.id}/tools`, { method: 'POST' })
          .then(r => setStatuses(p => ({ ...p, [s.id]: r.ok })))
          .catch(() => setStatuses(p => ({ ...p, [s.id]: false })));
      }
    } catch {}
  }

  useEffect(() => { fetchServers(); }, []);

  async function importServers() {
    setImportError('');
    let parsed: any;
    try { parsed = JSON.parse(importJson); }
    catch { setImportError('JSON 格式错误'); return; }

    const r = await fetch(`${API}/api/mcp/import`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(parsed),
    });
    if (!r.ok) {
      const err = await r.text();
      setImportError(err);
      return;
    }
    setShowModal(false);
    setImportJson('');
    fetchServers();
  }

  async function removeServer(id: number) {
    await fetch(`${API}/api/mcp/servers/${id}`, { method: 'DELETE' });
    setServers(s => s.filter(x => x.id !== id));
  }

  return (
    <div style={{ height: '100vh', background: T.bg, color: T.text, fontFamily: "system-ui,-apple-system,'Segoe UI',sans-serif", overflow: 'auto' }}>
      <div style={{ maxWidth: 800, margin: '0 auto', padding: `${S.huge}px ${S.xl}px` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.xs }}>
          <Wrench size={22} color={T.accent} />
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>MCP 服务</h2>
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowModal(true)} style={{
            ...btnPrimary, display: 'flex', alignItems: 'center', gap: 5, padding: '8px 16px', fontSize: 13,
          }}>
            <Plus size={15} /> 导入 MCP
          </button>
        </div>
        <p style={{ margin: 0, marginBottom: S.xl, fontSize: 13, color: T.secondary }}>
          Model Context Protocol — 管理已注册的 MCP 服务与工具
        </p>

        <div style={{ display: 'flex', flexDirection: 'column', gap: S.base }}>
          {servers.map(s => {
            const status = statuses[s.id];
            return (
              <div key={s.id} style={{ position: 'relative' }}>
                <div
                  onClick={() => router.push(`/dashboard/mcp/${s.id}`)}
                  style={{
                    padding: `${S.lg}px ${S.xl}px`,
                    background: T.surface,
                    borderRadius: 10,
                    border: `1px solid ${T.border}`,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    gap: S.base,
                    transition: 'border-color .15s, box-shadow .15s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = T.accent; e.currentTarget.style.boxShadow = `0 0 0 1px ${T.accent}20`; }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = T.border; e.currentTarget.style.boxShadow = 'none'; }}
                >
                  <div style={{
                    width: 40, height: 40, borderRadius: 8, background: T.accentBg,
                    display: 'flex', alignItems: 'center', justifyContent: 'center', flexShrink: 0,
                  }}>
                    <Server size={18} color={T.accent} />
                  </div>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: S.sm }}>
                      <span style={{ fontSize: 14, fontWeight: 600 }}>{s.name}</span>
                      <span style={{
                        fontSize: 10, padding: '2px 8px', borderRadius: 10,
                        background: s.transport === 'http' ? '#3370FF15' : '#00B42A15',
                        color: s.transport === 'http' ? '#3370FF' : '#00B42A',
                        display: 'flex', alignItems: 'center', gap: 3, fontWeight: 500,
                      }}>
                        {TRANSPORT_ICON[s.transport]} {s.transport.toUpperCase()}
                      </span>
                    </div>
                    <div style={{ fontSize: 12, color: T.secondary, marginTop: 2, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', fontFamily: 'monospace' }}>
                      {subtitle(s)}
                    </div>
                  </div>
                  <div style={{ display: 'flex', alignItems: 'center', gap: S.md, flexShrink: 0 }}>
                    {status === null ? (
                      <span style={{ fontSize: 11, color: T.tertiary }}>检测中...</span>
                    ) : status ? (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: T.success }}>
                        <Circle size={6} fill={T.success} stroke={T.success} /> 在线
                      </span>
                    ) : (
                      <span style={{ display: 'flex', alignItems: 'center', gap: 4, fontSize: 11, color: T.danger }}>
                        <Circle size={6} fill={T.danger} stroke={T.danger} /> 离线
                      </span>
                    )}
                    <ChevronRight size={16} color={T.tertiary} />
                  </div>
                </div>
                <button
                  onClick={e => { e.stopPropagation(); removeServer(s.id); }}
                  title="删除"
                  style={{
                    position: 'absolute', top: -6, right: -6,
                    width: 24, height: 24, borderRadius: 12,
                    background: T.danger, border: 'none', color: '#fff',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    cursor: 'pointer', opacity: 0,
                    transition: 'opacity .12s',
                  }}
                  onMouseEnter={e => { e.currentTarget.style.opacity = '1'; }}
                ><Trash2 size={12} /></button>
              </div>
            );
          })}
        </div>
      </div>

      {/* ── Import Modal ── */}
      {showModal && (
        <div onClick={() => setShowModal(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,.45)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            width: 780, background: T.surface, borderRadius: 12, padding: S.xl,
            border: `1px solid ${T.border}`, boxShadow: '0 16px 48px rgba(0,0,0,.25)',
          }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: S.lg }}>
              <span style={{ fontSize: 16, fontWeight: 600 }}>导入 MCP 服务</span>
              <button onClick={() => setShowModal(false)} style={{
                width: 28, height: 28, borderRadius: 6, border: `1px solid ${T.border}`, background: T.bg,
                display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: T.secondary,
              }}><X size={14} /></button>
            </div>

            <div style={{ display: 'flex', gap: S.xl }}>
              {/* ── 左侧：JSON 输入 ── */}
              <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: S.md }}>
                <p style={{ margin: 0, fontSize: 12, color: T.secondary, lineHeight: 1.6 }}>
                  粘贴 Claude Desktop 格式的 MCP 配置 JSON：
                </p>

                <textarea
                  value={importJson}
                  onChange={e => setImportJson(e.target.value)}
                  placeholder={`{\n  "mcpServers": {\n    "server-name": {\n      "command": "npx",\n      "args": ["-y", "package-name"]\n    }\n  }\n}`}
                  style={{
                    ...inputStyle, height: 260, resize: 'vertical', fontFamily: 'monospace',
                    fontSize: 12, lineHeight: 1.5, tabSize: 2,
                  }}
                  spellCheck={false}
                />

                {importError && (
                  <div style={{ fontSize: 12, color: T.danger, padding: '8px 12px', background: '#FF475715', borderRadius: 6, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
                    {importError}
                  </div>
                )}

                <div style={{ display: 'flex', gap: S.sm, justifyContent: 'flex-end' }}>
                  <button onClick={() => setShowModal(false)} style={{
                    padding: '8px 20px', borderRadius: 6, border: `1px solid ${T.border}`, background: T.bg,
                    color: T.text, fontSize: 13, cursor: 'pointer',
                  }}>取消</button>
                  <button onClick={importServers} style={{
                    ...btnPrimary, padding: '8px 20px', fontSize: 13,
                  }}>导入</button>
                </div>
              </div>

              {/* ── 右侧：示例模板 ── */}
              <div style={{ width: 260, flexShrink: 0, display: 'flex', flexDirection: 'column', gap: S.sm }}>
                <span style={{ fontSize: 11, fontWeight: 600, color: T.tertiary, textTransform: 'uppercase', letterSpacing: 0.5 }}>
                  示例模板（点击填入）
                </span>
                {MCP_EXAMPLES.map((ex, i) => (
                  <div
                    key={i}
                    onClick={() => { setImportJson(ex.json); setImportError(''); }}
                    style={{
                      padding: '10px 12px',
                      borderRadius: 8,
                      border: `1px solid ${T.border}`,
                      background: T.bg,
                      cursor: 'pointer',
                      transition: 'border-color .12s, box-shadow .12s',
                    }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = ex.color; e.currentTarget.style.boxShadow = `0 0 0 1px ${ex.color}20`; }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = T.border; e.currentTarget.style.boxShadow = 'none'; }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 3 }}>
                      <span style={{
                        display: 'inline-flex', alignItems: 'center', gap: 3,
                        padding: '1px 6px', borderRadius: 4, fontSize: 10, fontWeight: 600,
                        background: `${ex.color}18`, color: ex.color,
                      }}>
                        {ex.icon} {ex.label}
                      </span>
                    </div>
                    <p style={{ margin: 0, fontSize: 11, color: T.tertiary, lineHeight: 1.5 }}>{ex.desc}</p>
                  </div>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '8px 10px',
  background: '#0F1117', border: '1px solid #2A2D35', borderRadius: 6,
  fontSize: 13, color: '#E5E6EB', outline: 'none', fontFamily: 'monospace',
  boxSizing: 'border-box',
};
