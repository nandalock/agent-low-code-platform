'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { T, S, btnPrimary } from '@/app/theme';
import { Wrench, Plus, X, Box, ShoppingCart, FileText, Truck, BookOpen, Tags, User, Search } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface McpTool {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  server_id: number;
  server_name: string;
  transport: 'http' | 'stdio';
}

// 工具名 → 图标映射（内置工具精确匹配，其余按类别兜底）
const TOOL_ICONS: Record<string, { icon: React.ReactNode; color: string }> = {
  query_orders: { icon: <ShoppingCart size={18} />, color: '#3370FF' },
  get_order_detail: { icon: <FileText size={18} />, color: '#00B42A' },
  track_logistics: { icon: <Truck size={18} />, color: '#FF7D00' },
  search_faqs: { icon: <BookOpen size={18} />, color: '#8B5CF6' },
  list_faq_tags: { icon: <Tags size={18} />, color: '#F59E0B' },
  get_user_profile: { icon: <User size={18} />, color: '#13C2C2' },
};

function toolIcon(name: string): { icon: React.ReactNode; color: string } {
  if (TOOL_ICONS[name]) return TOOL_ICONS[name];
  if (/search|query|find/.test(name)) return { icon: <Search size={18} />, color: '#3370FF' };
  return { icon: <Box size={18} />, color: '#86909C' };
}

// 取 schema 属性类型（处理 anyOf）
function argType(v: any): string {
  if (v.anyOf) return v.anyOf.find((o: any) => o.type !== 'null')?.type || 'string';
  return v.type || 'string';
}

// 初始化参数默认值（tenant_id 默认 1，其余取 schema default）
function initialArgs(schema: any): Record<string, any> {
  const props = schema?.properties || {};
  const init: Record<string, any> = {};
  for (const [k, v] of Object.entries<any>(props)) {
    if (k === 'tenant_id') { init[k] = 1; continue; }
    if (v.default !== undefined && v.default !== null) init[k] = v.default;
    else if (argType(v) === 'boolean') init[k] = false;
    else init[k] = '';
  }
  return init;
}

export default function McpListPage() {
  const router = useRouter();
  const [tools, setTools] = useState<McpTool[]>([]);
  const [showModal, setShowModal] = useState(false);

  const [importJson, setImportJson] = useState('');
  const [importError, setImportError] = useState('');

  const MCP_EXAMPLES = [
    {
      type: 'npx', label: 'Node.js 包',
      icon: <Box size={14} />,
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
      icon: <Search size={14} />,
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
      icon: <Wrench size={14} />,
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

  async function fetchTools() {
    try {
      const r = await fetch(`${API}/api/mcp/tools/all`);
      const list: McpTool[] = await r.json();
      setTools(list);
    } catch {}
  }

  useEffect(() => { fetchTools(); }, []);

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
    fetchTools();
  }

  // 按 server 分组
  const groups = new Map<string, McpTool[]>();
  for (const t of tools) {
    const key = t.server_name;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key)!.push(t);
  }

  return (
    <div style={{ height: '100vh', background: T.bg, color: T.text, fontFamily: "system-ui,-apple-system,'Segoe UI',sans-serif", overflow: 'auto' }}>
      <div style={{ maxWidth: 1080, margin: '0 auto', padding: `${S.huge}px ${S.xl}px` }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.xs }}>
          <Wrench size={22} color={T.accent} />
          <h2 style={{ margin: 0, fontSize: 20, fontWeight: 600 }}>工具</h2>
          <div style={{ flex: 1 }} />
          <button onClick={() => setShowModal(true)} style={{
            ...btnPrimary, display: 'flex', alignItems: 'center', gap: 5, padding: '8px 16px', fontSize: 13,
          }}>
            <Plus size={15} /> 导入 MCP
          </button>
        </div>
        <p style={{ margin: 0, marginBottom: S.xl, fontSize: 13, color: T.secondary }}>
          平台所有可用的 MCP 工具，共 {tools.length} 个
        </p>

        {[...groups.entries()].map(([serverName, serverTools]) => (
          <div key={serverName} style={{ marginBottom: S.xxl }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.md }}>
              <span style={{ fontSize: 13, fontWeight: 600, color: T.text }}>{serverName}</span>
              <span style={{
                fontSize: 10, padding: '1px 8px', borderRadius: 10,
                background: T.accentBg, color: T.accent, fontWeight: 500,
              }}>
                {serverTools.length} 个工具
              </span>
            </div>
            <div style={{
              display: 'grid',
              gridTemplateColumns: 'repeat(3, 1fr)',
              gap: S.base,
            }}>
              {serverTools.map(t => {
                const { icon, color } = toolIcon(t.name);
                return (
                  <div
                    key={t.name}
                    onClick={() => router.push(`/dashboard/mcp/tool/${encodeURIComponent(t.name)}`)}
                    style={{
                      background: T.surface,
                      border: `1px solid ${T.border}`,
                      borderRadius: 10,
                      padding: `${S.lg}px ${S.base}px`,
                      cursor: 'pointer',
                      transition: 'border-color .15s, box-shadow .15s, transform .15s',
                    }}
                    onMouseEnter={e => {
                      e.currentTarget.style.borderColor = color;
                      e.currentTarget.style.boxShadow = `0 2px 12px ${color}15`;
                      e.currentTarget.style.transform = 'translateY(-1px)';
                    }}
                    onMouseLeave={e => {
                      e.currentTarget.style.borderColor = T.border;
                      e.currentTarget.style.boxShadow = 'none';
                      e.currentTarget.style.transform = 'none';
                    }}
                  >
                    <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.sm }}>
                      <div style={{
                        width: 32, height: 32, borderRadius: 8, flexShrink: 0,
                        background: `${color}14`, display: 'flex', alignItems: 'center', justifyContent: 'center',
                      }}>
                        {icon}
                      </div>
                      <span style={{ fontSize: 14, fontWeight: 600, fontFamily: 'monospace', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{t.name}</span>
                    </div>
                    <p style={{
                      margin: 0, fontSize: 12, color: T.secondary, lineHeight: 1.5,
                      display: '-webkit-box', WebkitLineClamp: 2, WebkitBoxOrient: 'vertical', overflow: 'hidden',
                    }}>
                      {t.description || '暂无描述'}
                    </p>
                  </div>
                );
              })}
            </div>
          </div>
        ))}

        {tools.length === 0 && (
          <div style={{ textAlign: 'center', padding: '80px 0', color: T.tertiary }}>
            <Box size={36} style={{ marginBottom: S.md, opacity: 0.4 }} />
            <div style={{ fontSize: 14, marginBottom: S.sm }}>暂无工具</div>
            <div style={{ fontSize: 12 }}>点击右上角"导入 MCP"添加工具</div>
          </div>
        )}
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
