'use client';

import { useState, useEffect } from 'react';
import { useRouter } from 'next/navigation';
import { Play, ChevronRight, MessageSquare, Database, Bug } from 'lucide-react';
import { T, S, btnPrimary } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

// ── Types ──

interface DebugMessage {
  role: string;
  content: string;
}

interface Conversation {
  id: number;
  customer_name?: string;
  channel: string;
  msg_count?: number;
  last_msg?: string;
  updated_at: string;
}

interface ToolCompaction {
  keep_n: number;
  max_chars: number;
  before_total: number;
  after_total: number;
  truncated_count: number;
}

interface ContextCheck {
  to_keep_count: number;
  to_compact_count: number;
  to_keep_roles: string[];
  to_compact_roles: string[];
}

interface Rendering {
  recent_char_count: number;
  recent_message_count: number;
  profile_char_count: number;
  summary_char_count: number;
  full_char_count: number;
}

interface IntermediateState {
  config: Record<string, number>;
  threshold: number;
  system_tokens: number;
  buf_tokens: number;
  budget: number;
  needs_compression: boolean;
  tool_compaction: ToolCompaction;
  context_check: ContextCheck;
  compressed_summary: string;
  profile_facts: string[];
  extraction_raw: string;
  rendering: Rendering;
  processing_time_ms: number;
}

interface DebugResult {
  profile: string;
  summary: string;
  recent: string;
  full: string;
  intermediate: IntermediateState;
}

// ── Tab config ──

const RESULT_TABS = [
  { key: 'compressed', label: '压缩摘要' },
  { key: 'profile',    label: '用户画像' },
  { key: 'context',    label: '上下文窗口' },
  { key: 'full',       label: '完整输出' },
  { key: 'intermediate', label: '中间状态' },
];

// ── Component ──

export default function MemoryDebugPage() {
  const router = useRouter();

  const [mode, setMode] = useState<'paste' | 'db'>('paste');
  const [rawText, setRawText] = useState('');
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [convLoading, setConvLoading] = useState(false);
  const [selectedConvId, setSelectedConvId] = useState<number | null>(null);
  const [systemPrompt, setSystemPrompt] = useState('');
  const [userOverride, setUserOverride] = useState('debug_user');
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<DebugResult | null>(null);
  const [activeTab, setActiveTab] = useState('compressed');

  // Load conversations for DB mode
  useEffect(() => {
    if (mode !== 'db') return;
    setConvLoading(true);
    fetch(`${API}/api/chat/conversations?page=1&size=100`, {
      headers: { 'X-Tenant-ID': String(TENANT_ID) },
    })
      .then(r => r.json())
      .then(d => setConversations(d.items || []))
      .catch(() => setConversations([]))
      .finally(() => setConvLoading(false));
  }, [mode]);

  // ── Parser: raw text → DebugMessage[] ──
  const parseRawText = (text: string): DebugMessage[] => {
    const lines = text.split('\n').filter(l => l.trim());
    const messages: DebugMessage[] = [];
    for (const rawLine of lines) {
      const line = rawLine.trim();
      const colonMatch = line.match(/^(customer|agent|system|tool)\s*:\s*(.*)$/i);
      const bracketMatch = line.match(/^\[(customer|agent|system|tool)\]\s*(.*)$/i);
      const match = colonMatch || bracketMatch;
      if (match) {
        messages.push({ role: match[1].toLowerCase(), content: match[2].trim() });
      } else if (messages.length > 0) {
        messages[messages.length - 1].content += '\n' + line;
      }
    }
    return messages;
  };

  // ── Run debug ──
  const handleRun = async () => {
    setRunning(true);
    setError(null);
    setResult(null);

    let messages: DebugMessage[] = [];
    let conversationId: number | null = null;

    if (mode === 'paste') {
      messages = parseRawText(rawText);
      if (messages.length === 0) {
        setError('请粘贴对话内容或切换为"从数据库选择"模式');
        setRunning(false);
        return;
      }
    } else {
      if (!selectedConvId) {
        setError('请选择一个会话');
        setRunning(false);
        return;
      }
      conversationId = selectedConvId;
    }

    try {
      const body: Record<string, unknown> = {
        messages: messages.map(m => ({ role: m.role, content: m.content })),
        conversation_id: conversationId,
        system_prompt: systemPrompt,
        user_id: userOverride,
      };
      const r = await fetch(`${API}/api/memory/debug/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': String(TENANT_ID) },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (data.ok) {
        setResult(data.result);
        setActiveTab('compressed');
      } else {
        setError(data.error || '运行失败');
      }
    } catch (e: unknown) {
      setError(`网络错误: ${e instanceof Error ? e.message : String(e)}`);
    } finally {
      setRunning(false);
    }
  };

  // ── Render helpers ──

  const renderJson = (obj: unknown) => (
    <pre style={{
      background: T.bg, padding: S.base, borderRadius: 8, fontSize: 12,
      color: T.text, overflow: 'auto', maxHeight: '60vh', lineHeight: 1.5,
      fontFamily: 'Consolas, monospace', whiteSpace: 'pre-wrap', wordBreak: 'break-all',
    }}>
      {JSON.stringify(obj, null, 2)}
    </pre>
  );

  const renderTextContent = (text: string) => (
    <div style={{
      background: T.bg, padding: S.base, borderRadius: 8, fontSize: 13,
      color: T.text, whiteSpace: 'pre-wrap', lineHeight: 1.6, maxHeight: '60vh', overflow: 'auto',
    }}>
      {text || '(empty)'}
    </div>
  );

  const renderTabPanel = () => {
    if (!result) return null;
    const { intermediate } = result;
    const tabContent: Record<string, React.ReactNode> = {
      compressed: (
        <div>
          <div style={{ fontSize:13, fontWeight:500, color:T.secondary, marginBottom:S.sm }}>
            LLM 压缩输出（{intermediate.compressed_summary.length} 字符）
          </div>
          {renderTextContent(result.summary)}
        </div>
      ),
      profile: (
        <div>
          {intermediate.profile_facts.length > 0 ? (
            <>
              <div style={{ fontSize:13, fontWeight:500, color:T.secondary, marginBottom:S.sm }}>
                提取到 {intermediate.profile_facts.length} 条画像事实
              </div>
              {intermediate.profile_facts.map((fact, i) => (
                <div key={i} style={{
                  background: T.surface, borderRadius:8, padding:`${S.md}px ${S.base}px`,
                  border:`1px solid ${T.border}`, fontSize:13, color:T.text, marginBottom:S.sm,
                  display:'flex', alignItems:'flex-start', gap:S.sm,
                }}>
                  <span style={{ color:T.accent, fontWeight:600, flexShrink:0 }}>{i+1}.</span>
                  <span>{fact}</span>
                </div>
              ))}
              {intermediate.extraction_raw && (
                <details style={{ marginTop:S.base }}>
                  <summary style={{ fontSize:12, color:T.secondary, cursor:'pointer' }}>查看 LLM 原始回复</summary>
                  {renderTextContent(intermediate.extraction_raw)}
                </details>
              )}
            </>
          ) : (
            <div style={{ color:T.tertiary, textAlign:'center', padding:S.huge, fontSize:14 }}>
              {intermediate.extraction_raw && intermediate.extraction_raw !== '提取失败'
                ? '未提取到新的画像事实（LLM 返回 SKIP 或事实为空）'
                : intermediate.extraction_raw
                  ? `提取失败: ${intermediate.extraction_raw}`
                  : '无画像数据（压缩未触发，无需提取）'}
            </div>
          )}
          {result.profile && (
            <div style={{ marginTop:S.base }}>
              <div style={{ fontSize:13, fontWeight:500, color:T.secondary, marginBottom:S.sm }}>
                已有画像（ExperienceMemory）
              </div>
              {renderTextContent(result.profile)}
            </div>
          )}
        </div>
      ),
      context: (
        <div>
          <div style={{ fontSize:13, fontWeight:500, color:T.secondary, marginBottom:S.sm }}>
            滑动窗口 — {intermediate.rendering.recent_message_count} 条消息，{intermediate.rendering.recent_char_count} 字符
          </div>
          {renderTextContent(result.recent)}
        </div>
      ),
      full: (
        <div>
          <div style={{ fontSize:13, fontWeight:500, color:T.secondary, marginBottom:S.sm }}>
            完整上下文（{intermediate.rendering.full_char_count} 字符）— profile + summary + recent 拼接
          </div>
          {renderTextContent(result.full)}
        </div>
      ),
      intermediate: (
        <div style={{ display:'flex', flexDirection:'column', gap:S.base }}>
          <div style={{ display:'flex', gap:S.base, flexWrap:'wrap' }}>
            <InfoCard label="处理耗时" value={`${intermediate.processing_time_ms}ms`} />
            <InfoCard label="消息总数" value={`${intermediate.rendering.recent_message_count + intermediate.context_check.to_compact_count}`} />
            <InfoCard label="触发压缩" value={intermediate.needs_compression ? '是' : '否'} color={intermediate.needs_compression ? T.warning : T.success} />
          </div>

          <SectionCard title="Token 预算计算">
            {renderJson({
              max_tokens: intermediate.config.max_tokens,
              compact_ratio: intermediate.config.compact_ratio,
              reserve_tokens: intermediate.config.reserve_tokens,
              threshold: intermediate.threshold,
              system_tokens: intermediate.system_tokens,
              buf_tokens: intermediate.buf_tokens,
              budget: intermediate.budget,
              needs_compression: intermediate.needs_compression,
            })}
          </SectionCard>

          <SectionCard title="Tool 输出截断">
            {renderJson(intermediate.tool_compaction)}
          </SectionCard>

          <SectionCard title="上下文检查 (check_context)">
            {renderJson(intermediate.context_check)}
          </SectionCard>

          <SectionCard title="渲染统计">
            {renderJson(intermediate.rendering)}
          </SectionCard>

          <SectionCard title="记忆模块配置">
            {renderJson(intermediate.config)}
          </SectionCard>
        </div>
      ),
    };
    return tabContent[activeTab] || null;
  };

  return (
    <div>
      {/* Breadcrumb */}
      <div style={{ display:'flex', alignItems:'center', gap:S.sm, marginBottom:S.xl }}>
        <span onClick={() => router.push('/dashboard/memory')} style={{ fontSize:13, color:T.accent, cursor:'pointer' }}>
          智能客服记忆系统
        </span>
        <ChevronRight size={12} color={T.tertiary} />
        <span style={{ fontSize:13, color:T.text, fontWeight:500 }}>调试工具</span>
      </div>

      <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>Memory Pipeline 调试</h2>
      <p style={{ fontSize:13, color:T.secondary, marginTop:S.xs, marginBottom:S.xl }}>
        输入对话消息，运行 MemoryContext 流水线，查看各阶段结果
      </p>

      {/* ── Input Section ── */}
      <div style={{
        background: T.surface, borderRadius: 10, border: `1px solid ${T.border}`,
        padding: S.xl, marginBottom: S.xl,
      }}>
        {/* Mode switcher */}
        <div style={{ display:'flex', gap:0, marginBottom:S.base, borderBottom:`1px solid ${T.border}` }}>
          <button onClick={() => setMode('paste')} style={{
            padding:`${S.sm}px ${S.base}px`, fontSize:13, fontWeight:mode==='paste'?600:400,
            color:mode==='paste'?T.text:T.secondary, background:'none', border:'none', cursor:'pointer',
            borderBottom:mode==='paste'?`2px solid ${T.accent}`:'2px solid transparent',
            marginBottom:-1, display:'flex', alignItems:'center', gap:S.sm,
          }}><MessageSquare size={14} /> 粘贴对话</button>
          <button onClick={() => setMode('db')} style={{
            padding:`${S.sm}px ${S.base}px`, fontSize:13, fontWeight:mode==='db'?600:400,
            color:mode==='db'?T.text:T.secondary, background:'none', border:'none', cursor:'pointer',
            borderBottom:mode==='db'?`2px solid ${T.accent}`:'2px solid transparent',
            marginBottom:-1, display:'flex', alignItems:'center', gap:S.sm,
          }}><Database size={14} /> 从数据库选择</button>
        </div>

        {/* Paste mode */}
        {mode === 'paste' && (
          <div style={{ marginTop:S.base }}>
            <div style={{ fontSize:13, fontWeight:500, color:T.text, marginBottom:4 }}>对话内容</div>
            <div style={{ fontSize:11, color:T.tertiary, marginBottom:S.xs }}>
              每行格式: role: content（支持 customer / agent / system / tool）
            </div>
            <textarea
              value={rawText}
              onChange={e => setRawText(e.target.value)}
              placeholder={
                'customer: 你好，我想查一下最近的订单\nagent: 您好！请提供您的手机号，我帮您查询\ntool: [订单查询结果] 找到3笔订单\nagent: 您的最近订单有：...'
              }
              style={{
                width:'100%', minHeight:180, padding:'10px 12px',
                background:T.bg, border:`1px solid ${T.border}`, borderRadius:6,
                fontSize:13, color:T.text, fontFamily:'Consolas, monospace', lineHeight:1.6,
                resize:'vertical', outline:'none', boxSizing:'border-box',
              }}
            />
          </div>
        )}

        {/* DB mode */}
        {mode === 'db' && (
          <div style={{ marginTop:S.base }}>
            <div style={{ fontSize:13, fontWeight:500, color:T.text, marginBottom:4 }}>选择会话</div>
            {convLoading ? (
              <div style={{ color:T.tertiary, fontSize:13 }}>加载中...</div>
            ) : conversations.length === 0 ? (
              <div style={{ color:T.tertiary, fontSize:13 }}>暂无会话</div>
            ) : (
              <select
                value={selectedConvId ?? ''}
                onChange={e => setSelectedConvId(Number(e.target.value) || null)}
                style={{
                  width:'100%', padding:'8px 10px', background:T.bg,
                  border:`1px solid ${T.border}`, borderRadius:6, fontSize:13, color:T.text,
                  outline:'none', fontFamily:'inherit',
                }}
              >
                <option value="">-- 请选择 --</option>
                {conversations.map(c => (
                  <option key={c.id} value={c.id}>
                    [{c.channel}] {c.customer_name || `会话 #${c.id}`} ({c.msg_count ?? '?'} 条消息)
                  </option>
                ))}
              </select>
            )}
          </div>
        )}

        {/* Optional settings */}
        <div style={{ display:'flex', gap:S.base, marginTop:S.base, flexWrap:'wrap' }}>
          <div style={{ flex:1, minWidth:200 }}>
            <div style={{ fontSize:12, color:T.secondary, marginBottom:4 }}>System Prompt（可选）</div>
            <input
              value={systemPrompt}
              onChange={e => setSystemPrompt(e.target.value)}
              placeholder="留空使用默认"
              style={{
                width:'100%', padding:'7px 10px', background:T.bg,
                border:`1px solid ${T.border}`, borderRadius:6, fontSize:13, color:T.text,
                outline:'none', boxSizing:'border-box', fontFamily:'inherit',
              }}
            />
          </div>
          <div style={{ flex:1, minWidth:120 }}>
            <div style={{ fontSize:12, color:T.secondary, marginBottom:4 }}>User ID</div>
            <input
              value={userOverride}
              onChange={e => setUserOverride(e.target.value)}
              style={{
                width:'100%', padding:'7px 10px', background:T.bg,
                border:`1px solid ${T.border}`, borderRadius:6, fontSize:13, color:T.text,
                outline:'none', boxSizing:'border-box', fontFamily:'inherit',
              }}
            />
          </div>
        </div>

        {/* Run button */}
        <div style={{ marginTop:S.base, display:'flex', alignItems:'center', gap:S.sm }}>
          <button onClick={handleRun} disabled={running} style={{
            ...btnPrimary, display:'flex', alignItems:'center', gap:6,
            opacity: running ? 0.6 : 1, cursor: running ? 'not-allowed' : 'pointer',
          }}>
            <Play size={14} /> {running ? '运行中...' : '运行调试'}
          </button>
        </div>

        {/* Error display */}
        {error && (
          <div style={{
            marginTop:S.base, padding:`${S.sm}px ${S.base}px`, background:'#FFF0F0',
            borderRadius:6, fontSize:13, color:T.danger, border:`1px solid ${T.danger}30`,
          }}>
            {error}
          </div>
        )}
      </div>

      {/* ── Results Section ── */}
      {result && (
        <div style={{
          background: T.surface, borderRadius: 10, border: `1px solid ${T.border}`,
          padding: S.xl,
        }}>
          {/* Summary bar */}
          <div style={{
            display:'flex', alignItems:'center', gap:S.base, marginBottom:S.base,
            padding:`${S.sm}px ${S.base}px`, background:T.bg, borderRadius:6,
            fontSize:12, color:T.secondary, flexWrap:'wrap',
          }}>
            <Bug size={14} />
            <span>处理耗时: <strong style={{color:T.text}}>{result.intermediate.processing_time_ms}ms</strong></span>
            <span style={{color:T.border}}>|</span>
            <span>消息: <strong style={{color:T.text}}>{result.intermediate.rendering.recent_message_count + result.intermediate.context_check.to_compact_count} 条</strong></span>
            <span style={{color:T.border}}>|</span>
            <span>压缩: <strong style={{color:result.intermediate.needs_compression ? T.warning : T.success}}>{result.intermediate.needs_compression ? '已触发' : '未触发'}</strong></span>
            <span style={{color:T.border}}>|</span>
            <span>画像: <strong style={{color:T.text}}>{result.intermediate.profile_facts.length} 条</strong></span>
          </div>

          {/* Tabs */}
          <div style={{ display:'flex', alignItems:'center', gap:0, borderBottom:`1px solid ${T.border}`, marginBottom:S.base }}>
            {RESULT_TABS.map(tab => (
              <button key={tab.key} onClick={() => setActiveTab(tab.key)} style={{
                padding:`${S.sm}px ${S.base}px`, fontSize:13, fontWeight:activeTab===tab.key?600:400,
                color:activeTab===tab.key?T.text:T.secondary, background:'none', border:'none', cursor:'pointer',
                borderBottom:activeTab===tab.key?`2px solid ${T.accent}`:'2px solid transparent',
                marginBottom:-1, transition:'color .12s',
              }}>
                {tab.label}
              </button>
            ))}
          </div>

          {/* Tab panel */}
          <div style={{ minHeight:200 }}>
            {renderTabPanel()}
          </div>
        </div>
      )}
    </div>
  );
}

// ── Helper components ──

function InfoCard({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <div style={{
      background: T.bg, borderRadius: 8, padding: `${S.md}px ${S.base}px`,
      minWidth: 120, flex: 1,
    }}>
      <div style={{ fontSize: 11, color: T.tertiary, marginBottom: 4 }}>{label}</div>
      <div style={{ fontSize: 18, fontWeight: 600, color: color || T.text }}>{value}</div>
    </div>
  );
}

function SectionCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{
      background: T.bg, borderRadius: 8, padding: S.base,
      border: `1px solid ${T.border}`,
    }}>
      <div style={{ fontSize: 12, fontWeight: 600, color: T.secondary, marginBottom: S.sm }}>{title}</div>
      {children}
    </div>
  );
}
