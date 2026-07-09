'use client';

import { useEffect, useRef, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import Link from 'next/link';
import { ArrowLeft, Brain, Play, Save, Settings, Trash2 } from 'lucide-react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

const MODEL_BASE_URLS: Record<string, string> = {
  'deepseek-chat': 'https://api.deepseek.com',
  'deepseek-reasoner': 'https://api.deepseek.com',
  'deepseek-v4-pro': 'https://api.deepseek.com',
  'gpt-5.5': 'https://api.openai.com',
  'gpt-5.4': 'https://api.openai.com',
  'gpt-5.4-mini': 'https://api.openai.com',
  'gpt-4o': 'https://api.openai.com',
  'claude-opus-4-8': 'https://api.anthropic.com',
  'claude-opus-4-7': 'https://api.anthropic.com',
  'claude-sonnet-4-6': 'https://api.anthropic.com',
  'claude-haiku-4-5': 'https://api.anthropic.com',
  'gemini-3.1-pro': 'https://generativelanguage.googleapis.com',
  'gemini-3.5-flash': 'https://generativelanguage.googleapis.com',
};

const TABS = [
  { key: 'l1', label: 'L1 关键词' },
  { key: 'l2', label: 'L2 向量语义' },
  { key: 'l3', label: 'L3 大模型' },
  { key: 'agents', label: '路由目标' },
];

interface KeywordRule {
  id: number;
  keywords: string;
  target: string;
}

interface RoutableAgent {
  key: string;
  tool_description: string;
}

interface TestResult {
  agent_key: string | null;
  route_level: string;
  confidence: number;
  action?: string;
  message?: string;
}

interface ChatEntry {
  question: string;
  result: TestResult | null;
  trace: any;
}

export default function RouterAgentPage() {
  const params = useParams();
  const router = useRouter();
  const key = params.key as string;

  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState('l1');

  // Config state
  const [l1Enabled, setL1Enabled] = useState(true);
  const [l1Rules, setL1Rules] = useState<KeywordRule[]>([]);
  const [l2, setL2] = useState({ enabled: true, model: 'bge-m3', threshold: 0.85, history_enabled: true, description_enabled: true, strategy: 'cascade', top_k: 3 });
  const [l3, setL3] = useState({ enabled: true, model: '', api_key: '', base_url: '', system_prompt_extra: '', fallback_agent: 'human_handoff', min_confidence: 0.6 });
  const [routableAgents, setRoutableAgents] = useState<RoutableAgent[]>([]);

  // Agent list (for target dropdowns)
  const [agentList, setAgentList] = useState<{ key: string; name: string }[]>([]);

  // Ollama embedding models
  const [ollamaModels, setOllamaModels] = useState<{ name: string; size_mb: number; dim: number; quant: string }[]>([]);

  // L1 modal
  const [l1Modal, setL1Modal] = useState(false);
  const [l1EditId, setL1EditId] = useState<number | null>(null);
  const [l1FormKw, setL1FormKw] = useState('');
  const [l1FormTarget, setL1FormTarget] = useState('');

  // LLM config modal
  const [llmOpen, setLlmOpen] = useState(false);

  // Chat history
  const [chatHistory, setChatHistory] = useState<ChatEntry[]>([]);
  const [testInput, setTestInput] = useState('');
  const [pendingQuestion, setPendingQuestion] = useState('');
  const [testing, setTesting] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const load = async () => {
      try {
        const [agentsR, configR, l1R] = await Promise.all([
          fetch(`${API}/api/agents`).then(r => r.json()),
          fetch(`${API}/api/agents/${key}/config`).then(r => r.json()),
          fetch(`${API}/api/agents/${key}/l1-keywords`).then(r => r.json()),
        ]);

        setAgentList((agentsR as any[]).filter((a: any) => a.agent_type !== 'router').map((a: any) => ({ key: a.key, name: a.name })));

        const cfg = configR.config || {};
        // L1 — from dedicated table
        setL1Enabled(cfg.l1_enabled !== false);
        setL1Rules((l1R as any[]).map((r: any) => ({ id: r.id, keywords: (r.keywords || []).join(', '), target: r.target || '' })));
        // L2
        if (cfg.l2_embedding) setL2(prev => ({ ...prev, ...cfg.l2_embedding }));
        // L3
        if (cfg.l3_llm) setL3(prev => ({ ...prev, ...cfg.l3_llm }));
        // routable_agents
        setRoutableAgents((cfg.routable_agents || []).map((a: any) => ({ key: a.key || '', tool_description: a.tool_description || '' })));
      } catch {}
      // 拉取 Ollama embedding 模型列表
      try {
        const modelsR = await fetch(`${API}/api/agents/ollama-models`);
        setOllamaModels(await modelsR.json());
      } catch {}
      setLoading(false);
    };
    load();
  }, [key]);

  const buildConfig = () => ({
    l1_enabled: l1Enabled,
    l2_embedding: l2,
    l3_llm: l3,
    routable_agents: routableAgents,
  });

  // L1 CRUD
  const l1OpenAdd = () => { setL1EditId(null); setL1FormKw(''); setL1FormTarget(''); setL1Modal(true); };
  const l1OpenEdit = (rule: KeywordRule) => {
    setL1EditId(rule.id); setL1FormKw(rule.keywords); setL1FormTarget(rule.target); setL1Modal(true);
  };
  const l1Save = async () => {
    const keywords = l1FormKw.split(/[,，]/).map(k => k.trim()).filter(Boolean);
    if (!keywords.length || !l1FormTarget) return;
    try {
      if (l1EditId) {
        await fetch(`${API}/api/agents/${key}/l1-keywords/${l1EditId}`, {
          method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keywords, target: l1FormTarget }),
        });
        setL1Rules(prev => prev.map(r => r.id === l1EditId ? { ...r, keywords: l1FormKw, target: l1FormTarget } : r));
      } else {
        const r = await fetch(`${API}/api/agents/${key}/l1-keywords`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ keywords, target: l1FormTarget }),
        });
        if (r.ok) {
          const created = await r.json();
          setL1Rules(prev => [...prev, { id: created.id, keywords: l1FormKw, target: l1FormTarget }]);
        }
      }
    } catch {}
    setL1Modal(false);
  };
  const l1Del = async (id: number) => {
    try {
      await fetch(`${API}/api/agents/${key}/l1-keywords/${id}`, { method: 'DELETE' });
      setL1Rules(prev => prev.filter(r => r.id !== id));
    } catch {}
  };

  const handleSave = async () => {
    setSaving(true);
    try {
      await fetch(`${API}/api/agents/${key}/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(buildConfig()),
      });
    } catch {}
    setSaving(false);
  };

  const handleTest = async () => {
    if (!testInput.trim() || testing) return;
    const question = testInput;
    setTestInput('');
    setPendingQuestion(question);
    setTesting(true);
    try {
      const r = await fetch(`${API}/api/agents/${key}/route-test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question }),
      });
      if (r.ok) {
        const d = await r.json();
        setChatHistory(prev => [...prev, { question, result: d.routing_result, trace: d.trace }]);
      } else {
        setChatHistory(prev => [...prev, { question, result: null, trace: null }]);
      }
    } catch {
      setChatHistory(prev => [...prev, { question, result: null, trace: null }]);
    }
    setTesting(false);
    setPendingQuestion('');
  };

  useEffect(() => {
    chatEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [chatHistory]);

  if (loading) {
    return <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: T.secondary, fontSize: 14 }}>加载中...</div>;
  }

  const levelColor = (level: string) => {
    const map: Record<string, string> = { L1: '#00B42A', L2: '#3370FF', L3: '#9333EA' };
    return map[level] || T.secondary;
  };
  const levelBg = (level: string) => {
    const map: Record<string, string> = { L1: '#E8FFEA', L2: '#E8F0FF', L3: '#F3E8FF' };
    return map[level] || T.bg;
  };

  return (
    <div style={{ display: 'flex', height: '100vh', background: T.bg }}>
      {/* ── Left: Config panel ── */}
      <div style={{
        width: 420, background: T.surface, borderRight: `1px solid ${T.border}`,
        display: 'flex', flexDirection: 'column', flexShrink: 0,
      }}>
        {/* Header */}
        <div style={{ padding: `${S.lg}px ${S.xl}px ${S.sm}px` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: S.sm, marginBottom: S.xs }}>
            <Link href="/dashboard/agents" style={{ color: T.secondary, display: 'flex' }}>
              <ArrowLeft size={16} />
            </Link>
            <Brain size={20} color="#9333EA" />
            <h2 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: T.text }}>{key}</h2>
            <span style={{ fontSize: 10, padding: '2px 8px', borderRadius: 8, background: '#F3E8FF', color: '#9333EA', fontWeight: 500 }}>Router</span>
          </div>
          <p style={{ margin: 0, fontSize: 12, color: T.secondary }}>
            L1 关键词 → L2 向量语义 → L3 大模型 FC，级联意图路由
          </p>
        </div>

        {/* Tab bar */}
        <div style={{ display: 'flex', borderBottom: `1px solid ${T.border}`, padding: `0 ${S.xl}px` }}>
          {TABS.map(t => (
            <button key={t.key} onClick={() => setTab(t.key)} style={{
              padding: '8px 14px', border: 'none', background: 'transparent', cursor: 'pointer',
              fontSize: 13, fontWeight: tab === t.key ? 600 : 400,
              color: tab === t.key ? T.accent : T.secondary,
              borderBottom: tab === t.key ? `2px solid ${T.accent}` : '2px solid transparent',
              fontFamily: 'inherit', marginBottom: -1,
            }}>{t.label}</button>
          ))}
        </div>

        {/* Tab content */}
        <div style={{ flex: 1, overflow: 'auto', padding: `${S.base}px ${S.xl}px ${S.xl}px` }}>
          {/* L1 Tab */}
          {tab === 'l1' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: S.md }}>
                  <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: T.text, cursor: 'pointer', fontWeight: 500 }}>
                    <input type="checkbox" checked={l1Enabled} onChange={e => setL1Enabled(e.target.checked)} />
                    启用 L1
                  </label>
                  <p style={{ margin: 0, fontSize: 12, color: T.secondary }}>共 {l1Rules.length} 条规则 · 命中则直接路由，跳过 L2/L3</p>
                </div>
                <button onClick={l1OpenAdd} disabled={!l1Enabled} style={{
                  padding: `${S.xs}px ${S.md}px`, borderRadius: 6, fontSize: 12, cursor: l1Enabled ? 'pointer' : 'not-allowed',
                  background: l1Enabled ? T.accent : T.border, color: l1Enabled ? '#fff' : T.tertiary, border: 'none', fontFamily: 'inherit',
                }}>+ 新增</button>
              </div>

              {l1Enabled ? (
                <div style={{ background: T.surface, borderRadius: 10, border: `1px solid ${T.border}`, overflow: 'hidden' }}>
                  <table style={{ width: '100%', borderCollapse: 'collapse' }}>
                    <thead><tr>
                      <th style={th}>关键词</th>
                      <th style={{ ...th, width: 120 }}>路由目标</th>
                      <th style={{ ...th, width: 100 }}>操作</th>
                    </tr></thead>
                    <tbody>
                      {l1Rules.map(rule => {
                        const kws = rule.keywords.split(/[,，]/).map(k => k.trim()).filter(Boolean);
                        const targetName = agentList.find(a => a.key === rule.target)?.name || rule.target;
                        return (
                          <tr key={rule.id}>
                            <td style={td}>
                              {kws.map(kw => (
                                <span key={kw} style={{ display: 'inline-block', padding: '2px 6px', margin: '2px 4px 2px 0', background: T.accentBg, borderRadius: 4, fontSize: 11, color: T.accent }}>{kw}</span>
                              ))}
                            </td>
                            <td style={{ ...td }}>{targetName}</td>
                            <td style={td}>
                              <button onClick={() => l1OpenEdit(rule)} style={{ ...linkBtn, color: T.accent, marginRight: S.md }}>编辑</button>
                              <button onClick={() => l1Del(rule.id)} style={{ ...linkBtn, color: T.danger }}>删除</button>
                            </td>
                          </tr>
                        );
                      })}
                      {l1Rules.length === 0 && (
                        <tr><td colSpan={3} style={{ textAlign: 'center', padding: S.xxxl, color: T.secondary }}>暂无数据</td></tr>
                      )}
                    </tbody>
                  </table>
                </div>
              ) : (
                <div style={{ padding: S.xxxl, textAlign: 'center', borderRadius: 10, background: T.bg, border: `1px dashed ${T.border}` }}>
                  <p style={{ margin: 0, fontSize: 13, color: T.tertiary }}>L1 关键词匹配已关闭，请求将跳过此层直接进入 L2</p>
                </div>
              )}
            </div>
          )}

          {/* L1 Modal */}
          {l1Modal && (
            <div onClick={() => setL1Modal(false)} style={{
              position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
            }}>
              <div onClick={e => e.stopPropagation()} style={{
                background: T.surface, borderRadius: 12, padding: S.xl, width: 440, boxShadow: '0 8px 32px rgba(0,0,0,0.10)',
              }}>
                <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: T.text, marginBottom: S.lg }}>
                  {l1EditId ? '编辑关键字规则' : '新增关键字规则'}
                </h3>
                <label style={{ ...labelStyle, marginBottom: 3 }}>关键词（逗号分隔）</label>
                <input value={l1FormKw} onChange={e => setL1FormKw(e.target.value)}
                  placeholder="快递, 物流, 发货" style={{ ...inputStyle, marginBottom: S.md }} />
                <label style={{ ...labelStyle, marginBottom: 3 }}>路由到</label>
                <select value={l1FormTarget} onChange={e => setL1FormTarget(e.target.value)}
                  style={{ ...inputStyle, background: T.surface, marginBottom: S.lg }}>
                  <option value="">选择目标 Agent</option>
                  {agentList.map(a => <option key={a.key} value={a.key}>{a.name} ({a.key})</option>)}
                </select>
                <div style={{ display: 'flex', gap: S.md, justifyContent: 'flex-end' }}>
                  <button onClick={() => setL1Modal(false)} style={{
                    padding: `${S.sm}px ${S.lg}px`, borderRadius: 6, fontSize: 13, cursor: 'pointer',
                    background: T.surface, border: `1px solid ${T.border}`, color: T.text,
                  }}>取消</button>
                  <button onClick={l1Save} style={{
                    padding: `${S.sm}px ${S.lg}px`, borderRadius: 6, fontSize: 13, cursor: 'pointer',
                    background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit',
                  }}>保存</button>
                </div>
              </div>
            </div>
          )}

          {/* L2 Tab */}
          {tab === 'l2' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: S.md }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: T.text, cursor: 'pointer', fontWeight: 500 }}>
                  <input type="checkbox" checked={l2.enabled} onChange={e => setL2(p => ({ ...p, enabled: e.target.checked }))} />
                  启用 L2
                </label>
                <p style={{ fontSize: 12, color: T.tertiary, margin: 0 }}>L1 未命中时进入向量语义匹配。使用 Ollama embedding 模型。</p>
              </div>

              {!l2.enabled && (
                <div style={{ padding: S.xxxl, textAlign: 'center', borderRadius: 10, background: T.bg, border: `1px dashed ${T.border}` }}>
                  <p style={{ margin: 0, fontSize: 13, color: T.tertiary }}>L2 向量语义匹配已关闭，请求将跳过此层</p>
                </div>
              )}

              {l2.enabled && <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div>
                <label style={labelStyle}>Embedding 模型</label>
                <select value={l2.model} onChange={e => setL2(p => ({ ...p, model: e.target.value }))}
                  style={{ ...inputStyle, background: T.surface, padding: '8px 10px' }}>
                  {ollamaModels.length === 0 && (
                    <option value={l2.model || 'bge-m3'}>{l2.model || 'bge-m3'}</option>
                  )}
                  {ollamaModels.map(m => (
                    <option key={m.name} value={m.name}>
                      {m.name}（{m.dim || '?'}维{m.quant ? `, ${m.quant.toUpperCase()}` : ''}, {m.size_mb >= 1000 ? `${(m.size_mb/1000).toFixed(1)}GB` : `${m.size_mb}MB`}）
                    </option>
                  ))}
                </select>
                <div style={{ fontSize: 11, color: T.tertiary, marginTop: 4 }}>自动检测 Ollama 中已安装的 embedding 模型，ollama pull 后刷新页面即可</div>
              </div>

              <div style={{ display: 'flex', gap: S.md }}>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>相似度阈值</label>
                  <input type="number" min={0} max={1} step={0.01} value={l2.threshold}
                    onChange={e => setL2(p => ({ ...p, threshold: parseFloat(e.target.value) || 0 }))} style={inputStyle} />
                </div>
                <div style={{ flex: 1 }}>
                  <label style={labelStyle}>Top-K</label>
                  <input type="number" min={1} max={20} value={l2.top_k}
                    onChange={e => setL2(p => ({ ...p, top_k: parseInt(e.target.value) || 3 }))} style={inputStyle} />
                </div>
              </div>

              <div>
                <label style={labelStyle}>匹配策略</label>
                <select value={l2.strategy} onChange={e => setL2(p => ({ ...p, strategy: e.target.value }))}
                  style={{ ...inputStyle, background: T.surface }}>
                  <option value="cascade">级联 (B未命中→C)</option>
                  <option value="weighted">加权综合 (B+C 得分加权)</option>
                </select>
              </div>

              <div style={{ display: 'flex', gap: S.md, padding: `${S.sm}px ${S.md}px`, borderRadius: 8, background: T.bg, border: `1px solid ${T.border}` }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: T.text, cursor: 'pointer' }}>
                  <input type="checkbox" checked={l2.history_enabled} onChange={e => setL2(p => ({ ...p, history_enabled: e.target.checked }))} />
                  L2-B 历史匹配
                </label>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: T.text, cursor: 'pointer' }}>
                  <input type="checkbox" checked={l2.description_enabled} onChange={e => setL2(p => ({ ...p, description_enabled: e.target.checked }))} />
                  L2-C 描述匹配
                </label>
              </div>

              <div style={{ fontSize: 11, color: T.tertiary, lineHeight: 1.6, padding: S.md, borderRadius: 6, background: '#F7F8FA' }}>
                <strong>L2-B</strong>：embed 用户问题 → pgvector 检索历史路由记录 → 相似度≥阈值则复用历史分类
                <br />
                <strong>L2-C</strong>：embed 用户问题 → 与每个路由目标 agent 的 tool_description 做余弦相似度 → 最高分≥阈值命中
              </div>
              </div>}
            </div>
          )}

          {/* L3 Tab */}
          {tab === 'l3' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: S.md }}>
                <label style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 13, color: T.text, cursor: 'pointer', fontWeight: 500 }}>
                  <input type="checkbox" checked={l3.enabled} onChange={e => setL3(p => ({ ...p, enabled: e.target.checked }))} />
                  启用 L3
                </label>
                <p style={{ fontSize: 12, color: T.tertiary, margin: 0 }}>L1、L2 均未命中时，调用 LLM Function Calling 做最终路由。</p>
              </div>

              {!l3.enabled && (
                <div style={{ padding: S.xxxl, textAlign: 'center', borderRadius: 10, background: T.bg, border: `1px dashed ${T.border}` }}>
                  <p style={{ margin: 0, fontSize: 13, color: T.tertiary }}>L3 大模型路由已关闭，请求将直接走兜底 Agent</p>
                </div>
              )}

              {l3.enabled && <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>

              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 600, color: T.text, marginBottom: 2 }}>LLM 配置</div>
                  <div style={{ fontSize: 12, color: T.secondary }}>
                    {l3.model ? `${l3.model} · ${l3.base_url || '未配置 Base URL'}` : '未配置'}
                  </div>
                </div>
                <button onClick={() => setLlmOpen(true)} style={{
                  width: 32, height: 32, borderRadius: 6, border: `1px solid ${T.border}`, background: T.bg,
                  display: 'flex', alignItems: 'center', justifyContent: 'center', cursor: 'pointer', color: T.secondary,
                }}><Settings size={16} /></button>
              </div>

              <div>
                <label style={labelStyle}>最小置信度 (min_confidence)</label>
                <input type="number" min={0} max={1} step={0.05} value={l3.min_confidence ?? 0.6}
                  onChange={e => setL3(p => ({ ...p, min_confidence: parseFloat(e.target.value) || 0 }))} style={inputStyle} />
                <div style={{ fontSize: 11, color: T.tertiary, marginTop: 4 }}>L3 置信度低于此值则拦截（very_low=15%, low=40%, medium=70%, high=85%, very_high=95%）</div>
              </div>

              <div>
                <label style={labelStyle}>兜底 Agent</label>
                <select value={l3.fallback_agent} onChange={e => setL3(p => ({ ...p, fallback_agent: e.target.value }))}
                  style={{ ...inputStyle, background: T.surface }}>
                  <option value="human_handoff">human_handoff（转人工）</option>
                  <option value="faqagent">faqagent（FAQ）</option>
                  {agentList.filter(a => a.key !== 'faqagent').map(a => <option key={a.key} value={a.key}>{a.name} ({a.key})</option>)}
                </select>
              </div>

              <div>
                <label style={labelStyle}>追加提示词（可选）</label>
                <textarea rows={3} value={l3.system_prompt_extra} onChange={e => setL3(p => ({ ...p, system_prompt_extra: e.target.value }))}
                  placeholder="在自动生成的 prompt 基础上追加的自定义指令..."
                  style={{ ...inputStyle, resize: 'vertical', minHeight: 60 }} />
              </div>

              <div style={{ fontSize: 11, color: T.tertiary, lineHeight: 1.6, padding: S.md, borderRadius: 6, background: '#F7F8FA' }}>
                L3 会根据「路由目标」Tab 中配置的 Agent 列表自动生成 Function Calling 工具，每个 Agent 对应一个 function。LLM 自评置信度（high/medium/low），低于 min_confidence 则拦截请求不路由。
              </div>
              </div>}
            </div>
          )}

          {/* Routable Agents Tab */}
          {tab === 'agents' && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.md }}>
              <p style={{ fontSize: 12, color: T.tertiary, margin: 0 }}>Router 可以路由到的目标 Agent 列表。tool_description 用于 L2-C 描述匹配和 L3 FC 工具描述。</p>
              {routableAgents.map((a, i) => (
                <div key={i} style={{ padding: S.md, borderRadius: 8, border: `1px solid ${T.border}`, background: T.bg }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: S.xs }}>
                    <span style={{ fontSize: 12, fontWeight: 600, color: T.text }}>{a.key}</span>
                    <button onClick={() => setRoutableAgents(prev => prev.filter((_, j) => j !== i))} style={{
                      width: 24, height: 24, borderRadius: 4, border: 'none', cursor: 'pointer',
                      background: 'transparent', color: T.danger, display: 'flex', alignItems: 'center', justifyContent: 'center',
                    }}><Trash2 size={12} /></button>
                  </div>
                  <label style={labelStyle}>Tool Description</label>
                  <input value={a.tool_description} onChange={e => {
                    const v = e.target.value;
                    setRoutableAgents(prev => prev.map((r, j) => j === i ? { ...r, tool_description: v } : r));
                  }} placeholder="描述这个 Agent 的用途" style={inputStyle} />
                </div>
              ))}
              <div style={{ display: 'flex', gap: S.xs, flexWrap: 'wrap' }}>
                {agentList.filter(a => !routableAgents.find(r => r.key === a.key)).map(a => (
                  <button key={a.key} onClick={() => setRoutableAgents(prev => [...prev, { key: a.key, tool_description: '' }])} style={{
                    padding: '4px 10px', borderRadius: 6, cursor: 'pointer', fontSize: 12,
                    background: T.accentBg, color: T.accent, border: 'none', fontFamily: 'inherit',
                  }}>+ {a.name}</button>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Save button */}
        <div style={{ padding: `${S.sm}px ${S.xl}px ${S.base}px`, borderTop: `1px solid ${T.border}` }}>
          <button onClick={handleSave} disabled={saving} style={{
            width: '100%', padding: '10px 0', borderRadius: 8, cursor: 'pointer', fontSize: 14, fontWeight: 600,
            background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit',
            opacity: saving ? 0.6 : 1, display: 'flex', alignItems: 'center', justifyContent: 'center', gap: 6,
          }}>
            <Save size={16} />{saving ? '保存中...' : '保存配置'}
          </button>
        </div>
      </div>

      {/* ── Right: Chat Panel ── */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.bg }}>
        {/* Header */}
        <div style={{ padding: `${S.md}px ${S.xl}px`, borderBottom: `1px solid ${T.border}`, background: T.surface, display: 'flex', alignItems: 'center', gap: 8 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: T.success }} />
          <span style={{ fontSize: 14, fontWeight: 600, color: T.text }}>路由测试</span>
          {chatHistory.length > 0 && (
            <button onClick={() => setChatHistory([])} style={{
              marginLeft: 'auto', padding: '3px 10px', borderRadius: 4, border: `1px solid ${T.border}`,
              background: T.surface, color: T.secondary, fontSize: 11, cursor: 'pointer',
            }}>清空</button>
          )}
        </div>

        {/* Messages */}
        <div style={{ flex: 1, padding: S.xl, overflow: 'auto' }}>
          {chatHistory.length === 0 && !testing && (
            <div style={{ textAlign: 'center', color: T.tertiary, marginTop: S.huge, fontSize: 13 }}>
              输入消息测试路由效果
            </div>
          )}

          {chatHistory.map((entry, i) => {
            const isBlocked = entry.result?.action === 'clarify';
            return (
              <div key={i} style={{ marginBottom: S.xl }}>
                {/* User bubble */}
                <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: S.sm }}>
                  <div style={{
                    maxWidth: '70%', padding: `${S.md}px ${S.base}px`, borderRadius: 8,
                    background: T.accent, color: '#fff', fontSize: 14, lineHeight: 1.5,
                    borderBottomRightRadius: 2,
                  }}>{entry.question}</div>
                </div>

                {/* Agent bubble */}
                <div style={{ display: 'flex', justifyContent: 'flex-start' }}>
                  <div style={{ maxWidth: '80%' }}>
                    {entry.result ? (
                      <>
                        <div style={{
                          padding: `${S.md}px ${S.base}px`, borderRadius: 8, fontSize: 14, lineHeight: 1.5,
                          background: T.surface, border: `1px solid ${T.border}`,
                          borderBottomLeftRadius: 2,
                        }}>
                          {isBlocked ? (
                            <div>
                              <div style={{ fontWeight: 600, marginBottom: 4, color: T.text }}>路由拦截</div>
                              <div style={{ fontSize: 13, color: T.secondary }}>{entry.result.message}</div>
                            </div>
                          ) : (
                            <div style={{ display: 'flex', alignItems: 'center', gap: S.md, marginBottom: S.sm }}>
                                <div style={{ width: 48, height: 48, borderRadius: 10, background: '#F3E8FF', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
                                  <Brain size={24} color="#9333EA" />
                                </div>
                                <div>
                                  <div style={{ fontSize: 18, fontWeight: 700, color: T.text }}>{entry.result.agent_key || '—'}</div>
                                  <div style={{ display: 'flex', gap: S.xs, marginTop: 2 }}>
                                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 600, background: levelBg(entry.result.route_level), color: levelColor(entry.result.route_level) }}>{entry.result.route_level}</span>
                                    <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 600, background: '#E8F8EE', color: T.success }}>
                                      {(entry.result.confidence * 100).toFixed(0)}% 置信度
                                    </span>
                                    {entry.trace?.total_ms && (
                                      <span style={{ fontSize: 11, padding: '2px 8px', borderRadius: 10, fontWeight: 500, background: T.bg, color: T.secondary }}>{entry.trace.total_ms}ms</span>
                                    )}
                                  </div>
                                </div>
                              </div>
                            )}
                        </div>

                        {/* 原始返回 JSON */}
                        <details style={{ marginTop: S.sm, fontSize: 11 }}>
                          <summary style={{ cursor: 'pointer', color: T.tertiary, userSelect: 'none' }}>路由返回 JSON</summary>
                          <pre style={{
                            marginTop: S.xs, padding: S.md, borderRadius: 6,
                            background: '#F7F8FA', border: `1px solid ${T.border}`,
                            fontSize: 11, lineHeight: 1.5, overflow: 'auto', maxHeight: 200,
                          }}>{JSON.stringify({ routing_result: entry.result, trace: { route_level: entry.trace?.route_level, confidence: entry.trace?.confidence, total_ms: entry.trace?.total_ms } }, null, 2)}</pre>
                        </details>

                        {/* Trace steps */}
                        {entry.trace?.steps && (
                          <div style={{ marginTop: S.sm }}>
                            {entry.trace.steps.map((s: any, j: number) => (
                              <div key={j} style={{
                                padding: `${S.xs}px ${S.sm}px`, borderRadius: 4,
                                marginBottom: 2, fontSize: 11,
                                background: s.action === 'blocked' ? '#FFF7E6' :
                                  s.agent_key ? '#E8FFEA' :
                                  !s.agent_key && s.level !== 'L1' ? '#F7F8FA' : 'transparent',
                              }}>
                                <strong>{s.level}</strong>
                                <span style={{ color: T.tertiary }}> ({s.method || s.action})</span>
                                {s.agent_key && (
                                  <span style={{ color: T.accent, marginLeft: 6 }}>→ {s.agent_key}</span>
                                )}
                                {s.confidence != null && (
                                  <span style={{ color: T.tertiary, marginLeft: 6 }}>{(s.confidence * 100).toFixed(0)}%</span>
                                )}
                                {!s.agent_key && s.level !== 'L1' && s.action !== 'blocked' && (
                                  <span style={{ color: T.tertiary }}> 未命中</span>
                                )}
                                {s.action === 'blocked' && (
                                  <span style={{ color: T.warning }}> 已拦截</span>
                                )}
                                {/* L2-C 分数详情 */}
                                {s.scores && s.level === 'L2-C' && (
                                  <div style={{ marginTop: 4, fontSize: 10 }}>
                                    <div style={{ color: T.tertiary, marginBottom: 2 }}>
                                      阈值: {s.threshold} · 模型: {s.model || '?'}
                                    </div>
                                    {s.scores.map((sc: any, si: number) => (
                                      <div key={si} style={{
                                        display: 'flex', alignItems: 'center', gap: 6,
                                        padding: '2px 6px', marginBottom: 1, borderRadius: 3,
                                        background: sc.score >= (s.threshold || 0.85) ? '#E8FFEA' : '#FAFAFA',
                                      }}>
                                        <span style={{
                                          width: 50, textAlign: 'right',
                                          fontWeight: sc.score >= (s.threshold || 0.85) ? 700 : 400,
                                          color: sc.score >= (s.threshold || 0.85) ? '#2E7D32' : T.tertiary,
                                        }}>{(sc.score * 100).toFixed(1)}%</span>
                                        <span style={{ color: T.text, fontWeight: 500 }}>{sc.agent_key}</span>
                                        <span style={{ color: T.tertiary, flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{sc.description}</span>
                                      </div>
                                    ))}
                                  </div>
                                )}
                                {s.fc_steps && s.level === 'L3' && !s.action && (
                                  <div style={{ marginTop: 2, fontSize: 10, color: T.tertiary }}>
                                    FC: {JSON.stringify(s.fc_steps)}
                                  </div>
                                )}
                              </div>
                            ))}
                          </div>
                        )}
                      </>
                    ) : (
                      <div style={{
                        padding: `${S.md}px ${S.base}px`, borderRadius: 8, fontSize: 13,
                        background: T.surface, border: `1px solid ${T.border}`, color: T.danger,
                      }}>请求失败</div>
                    )}
                  </div>
                </div>
              </div>
            );
          })}

          {testing && (
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: S.xl }}>
              <div style={{
                maxWidth: '70%', padding: `${S.md}px ${S.base}px`, borderRadius: 8,
                background: T.accent, color: '#fff', fontSize: 14, borderBottomRightRadius: 2,
              }}>{pendingQuestion}</div>
            </div>
          )}

          {testing && <div style={{ color: T.secondary, fontSize: 13, marginBottom: S.xl }}>路由分析中...</div>}
          <div ref={chatEndRef} />
        </div>

        {/* Input */}
        <div style={{ padding: `${S.sm}px ${S.xl}px ${S.xl}px`, borderTop: `1px solid ${T.border}`, background: T.surface }}>
          <div style={{ display: 'flex', gap: S.xs }}>
            <input
              value={testInput}
              onChange={e => setTestInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleTest()}
              placeholder="输入消息测试路由..."
              style={{
                flex: 1, padding: '10px 14px', background: T.bg, border: `1px solid ${T.border}`,
                borderRadius: 8, fontSize: 14, color: T.text, outline: 'none', fontFamily: 'inherit', boxSizing: 'border-box',
              }}
            />
            <button onClick={handleTest} disabled={!testInput.trim() || testing} style={{
              padding: '10px 20px', borderRadius: 8, fontSize: 14, cursor: 'pointer',
              background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit', fontWeight: 600,
              opacity: !testInput.trim() || testing ? 0.4 : 1, display: 'flex', alignItems: 'center', gap: 6,
            }}>
              <Play size={16} />发送
            </button>
          </div>
        </div>
      </div>

      {/* ── LLM Config Modal ── */}
      {llmOpen && (
        <div onClick={() => setLlmOpen(false)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: T.surface, borderRadius: 12, padding: S.xl, width: 440, boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
          }}>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: S.lg }}>
              <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: T.text }}>L3 LLM 配置</h3>
              <button onClick={() => setLlmOpen(false)} style={{
                background: 'none', border: 'none', cursor: 'pointer', color: T.secondary, fontSize: 20, padding: 0, lineHeight: 1,
              }}>×</button>
            </div>
            <div style={{ display: 'flex', flexDirection: 'column', gap: S.base }}>
              <label style={labelStyle}>API Key
                <input type="password" value={l3.api_key} onChange={e => setL3(p => ({ ...p, api_key: e.target.value.trim() }))}
                  placeholder="sk-xxx" style={{ ...inputStyle, marginTop: S.xs, fontFamily: 'monospace' }} />
              </label>
              <label style={labelStyle}>Base URL
                <input type="text" value={l3.base_url} onChange={e => setL3(p => ({ ...p, base_url: e.target.value.trim() }))}
                  placeholder="https://api.deepseek.com" style={{ ...inputStyle, marginTop: S.xs, fontFamily: 'monospace' }} />
              </label>
              <label style={labelStyle}>Model
                <select value={l3.model} onChange={e => {
                  const m = e.target.value;
                  setL3(p => ({ ...p, model: m, base_url: (!p.base_url || Object.values(MODEL_BASE_URLS).includes(p.base_url)) ? (MODEL_BASE_URLS[m] || '') : p.base_url }));
                }} style={{ ...inputStyle, marginTop: S.xs, padding: '8px 10px' }}>
                  <option value="">未选择</option>
                  <optgroup label="DeepSeek">
                    <option value="deepseek-chat">DeepSeek V4 Flash (chat)</option>
                    <option value="deepseek-reasoner">DeepSeek V4 Flash (reasoner)</option>
                    <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
                  </optgroup>
                  <optgroup label="OpenAI">
                    <option value="gpt-5.5">GPT-5.5</option>
                    <option value="gpt-5.4">GPT-5.4</option>
                    <option value="gpt-5.4-mini">GPT-5.4 Mini</option>
                    <option value="gpt-4o">GPT-4o</option>
                  </optgroup>
                  <optgroup label="Anthropic">
                    <option value="claude-opus-4-8">Claude Opus 4.8</option>
                    <option value="claude-opus-4-7">Claude Opus 4.7</option>
                    <option value="claude-sonnet-4-6">Claude Sonnet 4.6</option>
                    <option value="claude-haiku-4-5">Claude Haiku 4.5</option>
                  </optgroup>
                  <optgroup label="Google">
                    <option value="gemini-3.1-pro">Gemini 3.1 Pro</option>
                    <option value="gemini-3.5-flash">Gemini 3.5 Flash</option>
                  </optgroup>
                </select>
              </label>
            </div>
            <div style={{ display: 'flex', gap: S.md, justifyContent: 'flex-end', marginTop: S.lg }}>
              <button onClick={() => setLlmOpen(false)} style={{
                padding: `${S.sm}px ${S.lg}px`, borderRadius: 6, fontSize: 13, cursor: 'pointer',
                background: T.surface, border: `1px solid ${T.border}`, color: T.text,
              }}>取消</button>
              <button onClick={() => setLlmOpen(false)} style={{
                padding: `${S.sm}px ${S.lg}px`, borderRadius: 6, fontSize: 13, cursor: 'pointer',
                background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit',
              }}>确定</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

const th: React.CSSProperties = { textAlign:'left', padding:`${S.md}px ${S.base}px`, fontWeight:500, color:T.secondary, fontSize:12, borderBottom:`1px solid ${T.border}` };
const td: React.CSSProperties = { padding:`${S.md}px ${S.base}px`, fontSize:13, color:T.text, borderBottom:`1px solid ${T.border}`, verticalAlign:'middle' };
const linkBtn: React.CSSProperties = { background:'none', border:'none', cursor:'pointer', fontSize:12, padding:0 };

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '7px 10px',
  background: T.bg, border: `1px solid ${T.border}`, borderRadius: 6,
  fontSize: 13, color: T.text, outline: 'none', fontFamily: 'inherit',
  boxSizing: 'border-box',
};

const labelStyle: React.CSSProperties = {
  fontSize: 12, fontWeight: 500, color: T.text, display: 'block', marginBottom: 3,
};
