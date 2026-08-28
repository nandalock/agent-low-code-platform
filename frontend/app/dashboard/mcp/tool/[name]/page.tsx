'use client';

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { T, S, btnPrimary, btnGhost } from '@/app/theme';
import { Box, ShoppingCart, FileText, Truck, BookOpen, Tags, User, Search, Play, ArrowLeft } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface McpTool {
  name: string;
  description: string;
  inputSchema?: Record<string, unknown>;
  server_id: number;
  server_name: string;
  transport: 'http' | 'stdio';
}

// 工具名 → 图标映射（与列表页一致）
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

function argType(v: any): string {
  if (v.anyOf) return v.anyOf.find((o: any) => o.type !== 'null')?.type || 'string';
  return v.type || 'string';
}

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

export default function ToolTestPage() {
  const params = useParams<{ name: string }>();
  const router = useRouter();
  const toolName = decodeURIComponent(params.name || '');

  const [tool, setTool] = useState<McpTool | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [args, setArgs] = useState<Record<string, any>>({});
  const [result, setResult] = useState<{ ok: boolean; data: any } | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    fetch(`${API}/api/mcp/tools/${encodeURIComponent(toolName)}`)
      .then(async r => {
        if (!r.ok) { setNotFound(true); return null; }
        return r.json();
      })
      .then(t => {
        if (t) { setTool(t); setArgs(initialArgs(t.inputSchema)); }
      })
      .catch(() => setNotFound(true));
  }, [toolName]);

  async function runTest() {
    if (!tool) return;
    setLoading(true);
    setResult(null);
    const cleaned: Record<string, any> = {};
    for (const [k, v] of Object.entries(args)) {
      if (v === '' || v === null || v === undefined) continue;
      cleaned[k] = typeof v === 'string' && (v === 'true' || v === 'false') ? v === 'true' : v;
    }
    try {
      const r = await fetch(`${API}/api/mcp/tools/${encodeURIComponent(tool.name)}/call`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(cleaned),
      });
      const data = await r.json();
      setResult({ ok: r.ok, data });
    } catch (e: any) {
      setResult({ ok: false, data: String(e?.message || e) });
    }
    setLoading(false);
  }

  if (notFound) {
    return (
      <div style={{ textAlign: 'center', padding: '80px 0', color: T.tertiary }}>
        <div style={{ fontSize: 14, marginBottom: S.sm }}>工具不存在</div>
        <button onClick={() => router.push('/dashboard/mcp')} style={btnGhost}>返回工具列表</button>
      </div>
    );
  }

  if (!tool) {
    return <div style={{ padding: '60px 0', textAlign: 'center', color: T.tertiary, fontSize: 13 }}>加载中...</div>;
  }

  const { icon, color } = toolIcon(tool.name);

  return (
    <div style={{ maxWidth: 860, margin: '0 auto' }}>
      <button onClick={() => router.push('/dashboard/mcp')} style={{
        ...btnGhost, display: 'flex', alignItems: 'center', gap: 6, marginBottom: S.lg, fontSize: 13,
      }}>
        <ArrowLeft size={14} /> 返回工具列表
      </button>

      {/* ── 工具信息头 ── */}
      <div style={{ display: 'flex', alignItems: 'center', gap: S.md, marginBottom: S.xs }}>
        <div style={{
          width: 44, height: 44, borderRadius: 10, flexShrink: 0,
          background: `${color}14`, display: 'flex', alignItems: 'center', justifyContent: 'center',
        }}>
          {icon}
        </div>
        <div>
          <div style={{ display: 'flex', alignItems: 'center', gap: S.sm }}>
            <span style={{ fontSize: 18, fontWeight: 600, fontFamily: 'monospace' }}>{tool.name}</span>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 10,
              background: T.accentBg, color: T.accent, fontWeight: 500,
            }}>
              {tool.server_name}
            </span>
            <span style={{
              fontSize: 10, padding: '2px 8px', borderRadius: 10,
              background: '#00B42A15', color: T.success, fontWeight: 500,
            }}>
              {tool.transport.toUpperCase()}
            </span>
          </div>
          <p style={{ margin: 0, marginTop: 4, fontSize: 13, color: T.secondary, lineHeight: 1.5 }}>
            {tool.description || '暂无描述'}
          </p>
        </div>
      </div>

      {/* ── 参数与测试 ── */}
      <div style={{ display: 'flex', gap: S.xl, marginTop: S.xl }}>
        {/* 左：参数表单 */}
        <div style={{ flex: 1, display: 'flex', flexDirection: 'column', gap: S.md }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: T.tertiary, textTransform: 'uppercase', letterSpacing: 0.5 }}>
            参数
          </span>
          {Object.entries<any>((tool.inputSchema as any)?.properties || {}).map(([k, v]) => {
            const type = argType(v);
            const required = ((tool.inputSchema as any)?.required || []).includes(k);
            const placeholder = v.default !== undefined && v.default !== null ? String(v.default) : '';
            return (
              <label key={k} style={{ display: 'flex', flexDirection: 'column', gap: 5 }}>
                <span style={{ fontSize: 12, color: T.secondary, fontFamily: 'monospace' }}>
                  {k}
                  {required && <span style={{ color: T.danger }}> *</span>}
                  <span style={{ color: T.tertiary, marginLeft: 6 }}>{type}</span>
                </span>
                <input
                  type={type === 'number' || type === 'integer' ? 'number' : type === 'boolean' ? 'checkbox' : 'text'}
                  checked={type === 'boolean' ? !!args[k] : undefined}
                  onChange={e => setArgs(p => ({ ...p, [k]: type === 'number' || type === 'integer' ? (e.target.value === '' ? '' : Number(e.target.value)) : type === 'boolean' ? e.target.checked : e.target.value }))}
                  value={type === 'boolean' ? undefined : (args[k] ?? '')}
                  placeholder={placeholder || `输入 ${k}`}
                  style={type === 'boolean' ? { width: 16, height: 16, cursor: 'pointer' } : {
                    width: '100%', padding: '7px 10px', borderRadius: 6,
                    border: `1px solid ${T.border}`, background: T.bg, color: T.text,
                    fontSize: 13, outline: 'none', boxSizing: 'border-box', fontFamily: 'monospace',
                  }}
                />
              </label>
            );
          })}
          <button onClick={runTest} disabled={loading} style={{
            ...btnPrimary, marginTop: S.sm, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
            opacity: loading ? 0.6 : 1, cursor: loading ? 'wait' : 'pointer',
          }}>
            <Play size={14} /> {loading ? '测试中...' : '运行测试'}
          </button>
        </div>

        {/* 右：结果 */}
        <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column' }}>
          <span style={{ fontSize: 11, fontWeight: 600, color: T.tertiary, textTransform: 'uppercase', letterSpacing: 0.5, marginBottom: S.sm }}>
            测试结果
          </span>
          <pre style={{
            flex: 1, margin: 0, padding: S.md, borderRadius: 8, minHeight: 240, maxHeight: 480,
            overflow: 'auto', fontSize: 12, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            background: '#0F1117', color: result ? (result.ok ? '#7BE0A0' : '#FF8F8F') : '#6B7280',
            fontFamily: 'monospace', border: '1px solid #2A2D35',
          }}>
            {result
              ? JSON.stringify(result.data, null, 2)
              : '点击"运行测试"查看结果'}
          </pre>
        </div>
      </div>
    </div>
  );
}
