'use client';

import { useState, useEffect } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { T, S, btnPrimary } from '@/app/theme';
import { Wrench, Play, Copy, Check, Circle, Database, ArrowLeft } from 'lucide-react';
import { PanelLeftClose, PanelLeftOpen, PanelRightClose, PanelRightOpen } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const TOOL_META: Record<string, { domain: string; returns: string }> = {
  query_orders:    { domain: '订单',   returns: '{ orders: Order[] }' },
  get_order_detail:{ domain: '订单',   returns: '{ order, items, logistics }' },
  track_logistics: { domain: '订单',   returns: '{ tracking, carrier, events }' },
  search_faqs:     { domain: '知识库', returns: '{ faqs: [{id, question, answer, score}] }' },
  list_faq_tags:   { domain: '知识库', returns: '{ tags: string[] }' },
  get_user_profile:{ domain: '用户',   returns: '{ facts, preferences }' },
};

interface MCPParam {
  name: string; type: string; required: boolean; description: string;
}

interface MCPTool {
  name: string; description: string; domain: string;
  params: MCPParam[]; returns: string;
}

function cleanDesc(d: string): string {
  return d
    .replace(/<\/?[a-zA-Z0-9_-]+[^>]*>/g, '')
    .replace(/([。.!?])\s*(?=[A-Z一-鿿])/g, '$1\n')
    .replace(/([-•]\s)/g, '\n$1')
    .replace(/[ \t]{2,}/g, ' ')
    .replace(/\n{3,}/g, '\n\n')
    .trim();
}

const MAX_LIST_DESC = 60;
function shortDesc(d: string): string {
  const c = cleanDesc(d);
  return c.length > MAX_LIST_DESC ? c.slice(0, MAX_LIST_DESC) + '…' : c;
}

function fromInputSchema(schema: any): MCPParam[] {
  const required = new Set(schema?.required || []);
  const props = schema?.properties || {};
  return Object.entries(props).map(([name, def]: [string, any]) => ({
    name, type: def.type || 'string', required: required.has(name), description: def.description || '',
  }));
}


export default function McpServerPage() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const [tools, setTools] = useState<MCPTool[]>([]);
  const [loading, setLoading] = useState(true);
  const [serverOnline, setServerOnline] = useState(false);
  const [leftOpen, setLeftOpen] = useState(true);
  const [rightOpen, setRightOpen] = useState(true);
  const [selectedTool, setSelectedTool] = useState<MCPTool | null>(null);

  const [paramValues, setParamValues] = useState<Record<string, string>>({});
  const [result, setResult] = useState<string | null>(null);
  const [executing, setExecuting] = useState(false);
  const [elapsed, setElapsed] = useState<number | null>(null);
  const [copied, setCopied] = useState(false);


  async function fetchTools() {
    try {
      const r = await fetch(`${API}/api/mcp/servers/${id}/tools`, { method: 'POST' });
      const list = await r.json();
      const mapped: MCPTool[] = list.map((t: any) => ({
        name: t.name,
        description: t.description,
        domain: TOOL_META[t.name]?.domain || '',
        params: fromInputSchema(t.inputSchema),
        returns: TOOL_META[t.name]?.returns || '',
      }));
      setTools(mapped);
      setServerOnline(true);
    } catch {
      setServerOnline(false);
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { fetchTools(); }, [id]);

  function selectTool(tool: MCPTool) {
    setSelectedTool(tool);
    setResult(null); setElapsed(null);
    const vals: Record<string, string> = {};
    for (const p of tool.params) vals[p.name] = '';
    setParamValues(vals);
  }

  async function executeTool() {
    if (!selectedTool) return;
    setExecuting(true); setResult(null); setElapsed(null);
    const args: Record<string, any> = {};
    for (const p of selectedTool.params) {
      const v = paramValues[p.name]?.trim();
      if (v === '' || v === undefined) {
        if (p.required) { setResult(`缺少必填参数: ${p.name}`); setExecuting(false); return; }
        continue;
      }
      args[p.name] = p.type === 'integer' ? parseInt(v) || 0 : v;
    }
    const t0 = performance.now();
    try {
      const controller = new AbortController();
      const timer = setTimeout(() => controller.abort(), 15000);
      const r = await fetch(`${API}/api/mcp/servers/${id}/call`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ name: selectedTool.name, args }), signal: controller.signal,
      });
      clearTimeout(timer);
      if (!r.ok) {
        const err = await r.json().catch(() => ({ detail: r.statusText }));
        setResult(`错误 (${r.status}): ${err.detail || r.statusText}`);
      } else {
        setResult(JSON.stringify(await r.json(), null, 2));
      }
    } catch (e: any) {
      if (e.name === 'AbortError') {
        setResult('请求超时 (15s)，MCP 服务未响应');
      } else {
        setResult(`请求失败: ${e.message || '网络错误 — 检查后端是否启动'}`);
      }
    } finally {
      setElapsed(Math.round(performance.now() - t0));
      setExecuting(false);
    }
  }

  async function copyResult() {
    if (!result) return;
    try { await navigator.clipboard.writeText(result); setCopied(true); setTimeout(() => setCopied(false), 1500); } catch {}
  }

  return (
    <div style={{ display:'flex', height:'100vh', background:T.bg, color:T.text, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif" }}>
      {/* ═══ Left: Tool list ═══ */}
      <div style={{
        width: leftOpen ? 300 : 0, minWidth: leftOpen ? 300 : 0,
        background:T.surface, borderRight: leftOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease', overflow:'hidden',
      }}>
        <div style={{ padding:`${S.lg}px ${S.xl}px ${S.xs}px` }}>
          <button onClick={() => router.push('/dashboard/mcp')} style={{
            display:'flex', alignItems:'center', gap:4, padding:0, background:'none', border:'none',
            color:T.secondary, fontSize:12, cursor:'pointer', marginBottom:S.md,
          }}>
            <ArrowLeft size={13} /> MCP 服务列表
          </button>
          <div style={{ display:'flex', alignItems:'center', gap:S.sm }}>
            <Wrench size={18} color={T.accent} />
            <h3 style={{ margin:0, fontSize:16, fontWeight:600, color:T.text }}>MCP 工具</h3>
          </div>
          <p style={{ margin:0, marginTop:S.xs, fontSize:13, color:T.secondary }}>
            服务 #{id} · @mcp.tool() — JSON-RPC 2.0
          </p>
        </div>

        <div style={{ margin:`0 ${S.xl}px`, padding:S.md, background:T.bg, borderRadius:8, border:`1px solid ${T.border}`, display:'flex', alignItems:'center', gap:S.sm }}>
          <Circle size={8} fill={serverOnline ? T.success : T.danger} stroke={serverOnline ? T.success : T.danger} />
          <span style={{ fontSize:12, color:T.text }}>MCP 服务</span>
          <span style={{ marginLeft:'auto', fontSize:11, color:serverOnline ? T.success : T.danger, fontWeight:500 }}>
            {loading ? '检测中...' : serverOnline ? '运行中' : '未连接'}
          </span>
        </div>

        <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', padding:`${S.lg}px ${S.xl}px ${S.sm}px` }}>
          工具列表 ({tools.length})
        </div>

        <div style={{ flex:1, padding:`0 ${S.xl}px`, overflow:'auto', paddingBottom:S.xl }}>
          {loading && <div style={{ fontSize:12, color:T.tertiary, padding:S.md }}>加载中...</div>}
          {!loading && tools.map(tool => (
            <div key={tool.name} onClick={() => selectTool(tool)} style={{
              padding:`${S.sm}px ${S.md}px`, borderRadius:6,
              cursor:'pointer', fontSize:12, color:T.text,
              background: selectedTool?.name === tool.name ? T.accentBg : 'transparent',
              border: selectedTool?.name === tool.name ? `1px solid ${T.accent}30` : '1px solid transparent',
              marginBottom:2,
            }}>
              <div style={{ fontWeight: selectedTool?.name === tool.name ? 600 : 400 }}>{tool.name}</div>
              <div style={{ fontSize:11, color:T.secondary, marginTop:1, lineHeight:1.4, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                {shortDesc(tool.description)}
              </div>
            </div>
          ))}
        </div>
      </div>

      {/* ═══ Center: Execute ═══ */}
      <div style={{ flex:1, display:'flex', flexDirection:'column', background:T.bg, minWidth:0 }}>
        <div style={{ padding:`${S.md}px ${S.xl}px`, display:'flex', alignItems:'center', gap:S.sm, borderBottom:`1px solid ${T.border}` }}>
          <button onClick={() => setLeftOpen(!leftOpen)} title={leftOpen?'收起工具列表':'展开工具列表'} style={{
            width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
          }}>{leftOpen ? <PanelLeftClose size={14} /> : <PanelLeftOpen size={14} />}</button>
          <button onClick={() => setRightOpen(!rightOpen)} title={rightOpen?'收起Schema':'展开Schema'} style={{
            width:28, height:28, borderRadius:6, border:`1px solid ${T.border}`, background:T.surface,
            display:'flex', alignItems:'center', justifyContent:'center', cursor:'pointer', color:T.secondary,
          }}>{rightOpen ? <PanelRightClose size={14} /> : <PanelRightOpen size={14} />}</button>
          <div style={{ flex:1 }} />
          {selectedTool && (
            <span style={{ fontSize:12, padding:'3px 10px', borderRadius:10, background:T.accentBg, color:T.accent, fontWeight:500 }}>
              {selectedTool.name}
            </span>
          )}
        </div>

        <div style={{ flex:1, overflow:'auto', padding:S.xl }}>
          {!selectedTool ? (
            <div style={{ textAlign:'center', color:T.secondary, marginTop:S.huge, fontSize:14 }}>从左侧列表选择一个工具</div>
          ) : (
            <div style={{ maxWidth:720, display:'flex', flexDirection:'column', gap:S.lg }}>
              <div style={{ padding:S.base, background:T.accentBg, borderRadius:8, border:`1px solid ${T.accent}20` }}>
                <div style={{ display:'flex', alignItems:'center', gap:S.sm }}>
                  <Database size={16} color={T.accent} />
                  <span style={{ fontSize:14, fontWeight:600 }}>{selectedTool.name}</span>
                  <span style={{ fontSize:11, color:T.tertiary }}>{selectedTool.returns}</span>
                </div>
                <div style={{ fontSize:13, color:T.secondary, marginTop:S.xs }}>{selectedTool.description}</div>
              </div>

              <div>
                <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>参数</div>
                <div style={{ display:'flex', flexDirection:'column', gap:S.sm }}>
                  {selectedTool.params.map(p => (
                    <div key={p.name} style={{ display:'flex', alignItems:'flex-start', gap:S.sm }}>
                      <div style={{ minWidth:140, paddingTop:10 }}>
                        <span style={{ fontSize:12, fontWeight:500, color:T.text, fontFamily:'monospace' }}>{p.name}</span>
                        {p.required && <span style={{ fontSize:10, color:T.danger, marginLeft:4 }}>*</span>}
                        <span style={{ fontSize:10, color:T.tertiary, marginLeft:6 }}>{p.type}</span>
                      </div>
                      <input value={paramValues[p.name] ?? ''} onChange={e => setParamValues(pv => ({ ...pv, [p.name]: e.target.value }))}
                        placeholder={p.description}
                        style={{ flex:1, padding:'8px 10px', background:T.surface, border:`1px solid ${T.border}`, borderRadius:6, fontSize:12, fontFamily:'monospace', color:T.text, outline:'none' }} />
                    </div>
                  ))}
                  {selectedTool.params.length === 0 && <div style={{ fontSize:12, color:T.tertiary, fontStyle:'italic' }}>无参数</div>}
                </div>
              </div>

              <div style={{ display:'flex', alignItems:'center', gap:S.sm }}>
                <button onClick={executeTool} disabled={executing} style={{ ...btnPrimary, display:'flex', alignItems:'center', gap:S.xs, padding:'10px 24px', fontSize:14, opacity: executing ? 0.5 : 1 }}>
                  <Play size={14} /> {executing ? '执行中...' : '执行'}
                </button>
                {elapsed !== null && <span style={{ fontSize:12, color:T.secondary }}>{elapsed}ms</span>}
              </div>

              {result !== null && (
                <div>
                  <div style={{ display:'flex', alignItems:'center', justifyContent:'space-between', marginBottom:S.sm }}>
                    <span style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase' }}>返回结果</span>
                    <button onClick={copyResult} style={{ display:'flex', alignItems:'center', gap:4, padding:'4px 10px', borderRadius:6, border:`1px solid ${T.border}`, background:T.surface, color:T.text, fontSize:11, cursor:'pointer' }}>
                      {copied ? <Check size={12} color={T.success} /> : <Copy size={12} />}{copied ? '已复制' : '复制'}
                    </button>
                  </div>
                  <pre style={{ margin:0, padding:S.base, background:'#1C2333', borderRadius:8, color:'#E5E6EB', fontSize:12, lineHeight:1.6, overflow:'auto', maxHeight:400, whiteSpace:'pre-wrap', wordBreak:'break-all', fontFamily:"'JetBrains Mono','Fira Code','Cascadia Code',monospace" }}>
                    {result}
                  </pre>
                </div>
              )}
            </div>
          )}
        </div>
      </div>

      {/* ═══ Right: Schema ═══ */}
      <div style={{
        width: rightOpen ? 340 : 0, minWidth: rightOpen ? 340 : 0,
        background:T.surface, borderLeft: rightOpen ? `1px solid ${T.border}` : 'none',
        display:'flex', flexDirection:'column', flexShrink:0,
        transition:'width .18s ease, min-width .18s ease', overflow:'hidden',
      }}>
        {selectedTool ? (
          <>
            <div style={{ padding:`${S.base}px`, borderBottom:`1px solid ${T.border}`, minWidth:340 }}>
              <span style={{ fontSize:13, fontWeight:500, color:T.text }}>Schema</span>
              <span style={{ fontSize:11, color:T.tertiary, marginLeft:S.sm, fontFamily:'monospace' }}>{selectedTool.name}</span>
            </div>
            <div style={{ flex:1, padding:S.base, overflow:'auto', display:'flex', flexDirection:'column', gap:S.lg, minWidth:340 }}>
              <div>
                <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>输入参数</div>
                <div style={{ display:'flex', flexDirection:'column', gap:1 }}>
                  {selectedTool.params.map(p => (
                    <div key={p.name} style={{ padding:`${S.sm}px ${S.md}px`, background:T.bg, borderRadius:4, border:`1px solid ${T.border}`, fontSize:12 }}>
                      <div style={{ display:'flex', alignItems:'center', gap:S.sm }}>
                        <span style={{ fontWeight:600, color:T.text, fontFamily:'monospace' }}>{p.name}</span>
                        {p.required && <span style={{ fontSize:9, padding:'1px 5px', borderRadius:4, background:T.danger+'18', color:T.danger }}>必填</span>}
                        <span style={{ marginLeft:'auto', fontSize:10, color:T.accent, fontFamily:'monospace' }}>{p.type}</span>
                        <span style={{ marginLeft:'auto', fontSize:10, color:T.accent, fontFamily:'monospace' }}>{p.type}</span>
                      </div>
                      <div style={{ fontSize:11, color:T.secondary, marginTop:2 }}>{p.description}</div>
                    </div>
                  ))}
                  {selectedTool.params.length === 0 && <div style={{ fontSize:12, color:T.tertiary, fontStyle:'italic' }}>无参数</div>}
                </div>
              </div>
              <div>
                <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>返回值</div>
                <div style={{ padding:`${S.md}px`, background:T.bg, borderRadius:6, border:`1px solid ${T.border}`, fontSize:12, fontFamily:'monospace', color:T.text, lineHeight:1.6, whiteSpace:'pre-wrap' }}>{selectedTool.returns}</div>
              </div>
              <div>
                <div style={{ fontSize:11, fontWeight:600, color:T.secondary, letterSpacing:'0.04em', textTransform:'uppercase', marginBottom:S.sm }}>LLM 描述</div>
                <div style={{ padding:`${S.md}px`, background:'#FFF7E6', borderRadius:6, border:`1px solid ${T.warning}20`, fontSize:12, color:T.text, lineHeight:1.6 }}>{selectedTool.description}</div>
              </div>
            </div>
          </>
        ) : (
          <div style={{ flex:1, display:'flex', alignItems:'center', justifyContent:'center', color:T.tertiary, fontSize:13, minWidth:340 }}>选择工具以查看 Schema</div>
        )}
      </div>
    </div>
  );
}
