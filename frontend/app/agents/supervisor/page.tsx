'use client';

import { useEffect, useState } from 'react';
import { T, S, btnPrimary } from '@/app/theme';
import { ArrowRight, GitBranch, Zap, Search, Brain } from 'lucide-react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface RoutableAgent {
  key: string;
  name: string;
  desc: string;
}

export default function SupervisorPage() {
  const [config, setConfig] = useState<Record<string, any> | null>(null);

  useEffect(() => {
    fetch(`${API}/api/agents/supervisor/config`)
      .then(r => r.json())
      .then(d => setConfig(d.config))
      .catch(() => {});
  }, []);

  const agents: RoutableAgent[] = config?.routable_agents || [];
  const l1Keywords: Record<string, string[]> = config?.l1_keywords || {};

  const agentStatus = (key: string) => key === 'faqagent' ? '已接入' : '占位';
  const agentColor = (key: string) => key === 'faqagent' ? T.success : T.warning;
  const agentBg = (key: string) => key === 'faqagent' ? '#E8F8EE' : '#FFF7E6';

  return (
    <div style={{ display: 'flex', height: '100vh', background: T.bg, color: T.text, fontFamily: "system-ui,-apple-system,'Segoe UI',sans-serif" }}>
      {/* ── Left panel ── */}
      <div style={{
        width: 360, background: T.surface, borderRight: `1px solid ${T.border}`,
        display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'auto',
      }}>
        <div style={{ padding: `${S.lg}px ${S.xl}px ${S.base}px` }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: S.sm }}>
            <GitBranch size={18} color={T.accent} />
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: T.text }}>Supervisor 编排层</h3>
          </div>
          <p style={{ margin: 0, marginTop: S.xs, fontSize: 13, color: T.secondary }}>
            三级意图路由 — 分析用户意图 → 路由子 Agent → 汇总结果
          </p>
        </div>

        <div style={{ flex: 1, padding: `0 ${S.xl}px`, display: 'flex', flexDirection: 'column', gap: S.base, paddingBottom: S.xl }}>

          {/* ── 路由层级 ── */}
          <div style={{ fontSize: 11, fontWeight: 600, color: T.secondary, letterSpacing: '0.04em', textTransform: 'uppercase', marginTop: S.sm }}>
            路由策略
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: S.xs }}>
            {[
              { tier: 'L1', label: '关键词匹配', icon: <Zap size={13} />, speed: '<1ms', ratio: '60-80%', color: '#10B981' },
              { tier: 'L2', label: '向量语义', icon: <Search size={13} />, speed: '30-100ms', ratio: '15-25%', color: T.accent },
              { tier: 'L3', label: '大模型 FC', icon: <Brain size={13} />, speed: '1-2s', ratio: '5-15%', color: '#8B5CF6' },
            ].map(l => (
              <div key={l.tier} style={{
                display: 'flex', alignItems: 'center', gap: S.sm,
                padding: `${S.sm}px ${S.md}px`, borderRadius: 6,
                background: T.bg, border: `1px solid ${T.border}`, fontSize: 12,
              }}>
                <span style={{ color: l.color, display: 'flex' }}>{l.icon}</span>
                <span style={{ fontWeight: 600, color: l.color, minWidth: 18 }}>{l.tier}</span>
                <span style={{ color: T.text }}>{l.label}</span>
                <span style={{ marginLeft: 'auto', fontSize: 10, color: T.tertiary }}>{l.speed} · {l.ratio}</span>
              </div>
            ))}
          </div>

          {/* ── 工作流图 ── */}
          <div style={{ fontSize: 11, fontWeight: 600, color: T.secondary, letterSpacing: '0.04em', textTransform: 'uppercase', marginTop: S.sm }}>
            编排工作流
          </div>

          <div style={{
            background: T.bg, borderRadius: 10, padding: S.lg,
            border: `1px solid ${T.border}`, display: 'flex', flexDirection: 'column',
            alignItems: 'center', gap: 0,
          }}>
            {/* 入口 */}
            <div style={{ fontSize: 10, color: T.tertiary, marginBottom: S.xs }}>用户消息</div>

            {/* L1/L2/L3 入口 */}
            <div style={{ display: 'flex', gap: S.xs, marginBottom: S.xs }}>
              {['L1', 'L2', 'L3'].map(t => (
                <span key={t} style={{ fontSize: 9, padding: '1px 6px', borderRadius: 8, background: T.accentBg, color: T.accent }}>{t}</span>
              ))}
            </div>

            {/* Supervisor */}
            <div style={{
              padding: '10px 28px', borderRadius: 20, background: T.accent,
              color: '#fff', fontSize: 14, fontWeight: 600,
            }}>
              Supervisor
            </div>

            {/* 分叉线 */}
            <div style={{ position: 'relative', width: '100%', height: 30, display: 'flex', justifyContent: 'center' }}>
              <div style={{ position: 'absolute', top: 0, left: '50%', width: 2, height: 15, background: T.border, transform: 'translateX(-50%)' }} />
              <div style={{ position: 'absolute', top: 15, left: '10%', right: '10%', height: 2, background: T.border }} />
              {agents.map((a, i) => {
                const left = 20 + (i * 60 / (agents.length - 1 || 1)) * (agents.length > 1 ? 0.6 : 0);
                return (
                  <div key={a.key} style={{ position: 'absolute', top: 17, left: `${20 + i * (60 / Math.max(agents.length - 1, 1))}%`, width: 2, height: 13, background: T.border, transform: 'translateX(-50%)' }} />
                );
              })}
            </div>

            {/* 子 Agent 卡片 */}
            <div style={{ display: 'flex', gap: S.sm, flexWrap: 'wrap', justifyContent: 'center' }}>
              {agents.map(a => (
                <div key={a.key} style={{
                  padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
                  background: agentBg(a.key),
                  border: `1px solid ${agentColor(a.key)}20`,
                  fontSize: 12, textAlign: 'center', minWidth: 70,
                }}>
                  <div style={{ fontWeight: 600, color: T.text }}>{a.name}</div>
                  <div style={{ fontSize: 10, color: T.tertiary, marginTop: 2 }}>{agentStatus(a.key)}</div>
                </div>
              ))}
            </div>

            {/* 汇合线 */}
            <div style={{ position: 'relative', width: '100%', height: 24, display: 'flex', justifyContent: 'center' }}>
              <div style={{ position: 'absolute', top: 0, left: '10%', right: '10%', height: 2, background: T.border }} />
              <div style={{ position: 'absolute', top: 2, left: '50%', width: 2, height: 22, background: T.border, transform: 'translateX(-50%)' }} />
            </div>

            {/* 最终回复 */}
            <div style={{
              padding: '6px 20px', borderRadius: 14,
              background: 'rgba(0,200,100,0.08)', border: `1px solid rgba(0,200,100,0.2)`,
              fontSize: 12, color: T.success, fontWeight: 500,
            }}>
              最终回复
            </div>
          </div>

          {/* ── 子 Agent 列表 ── */}
          <div style={{ fontSize: 11, fontWeight: 600, color: T.secondary, letterSpacing: '0.04em', textTransform: 'uppercase', marginTop: S.sm }}>
            子 Agent
          </div>
          <div style={{ display: 'flex', flexDirection: 'column', gap: S.xs }}>
            {agents.map(a => (
              <div key={a.key} style={{
                padding: `${S.md}px`, borderRadius: 8,
                background: T.bg, border: `1px solid ${T.border}`,
              }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: S.sm }}>
                  <span style={{
                    width: 8, height: 8, borderRadius: '50%',
                    background: agentColor(a.key), flexShrink: 0,
                  }} />
                  <span style={{ fontSize: 13, fontWeight: 500, color: T.text }}>{a.name}</span>
                  <span style={{
                    marginLeft: 'auto', fontSize: 10, padding: '1px 8px', borderRadius: 10,
                    background: agentBg(a.key), color: agentColor(a.key),
                  }}>
                    {agentStatus(a.key)}
                  </span>
                </div>
                <div style={{ fontSize: 11, color: T.secondary, marginTop: S.xs, lineHeight: 1.5 }}>
                  {a.desc}
                </div>
                {l1Keywords[a.key] && (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4, marginTop: S.sm }}>
                    {l1Keywords[a.key].slice(0, 6).map((kw: string) => (
                      <span key={kw} style={{
                        fontSize: 10, padding: '1px 6px', borderRadius: 4,
                        background: T.accentBg, color: T.accent,
                      }}>
                        {kw}
                      </span>
                    ))}
                    {l1Keywords[a.key].length > 6 && (
                      <span style={{ fontSize: 10, color: T.tertiary }}>+{l1Keywords[a.key].length - 6}</span>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        </div>

        <div style={{ padding: `${S.base}px ${S.xl}px ${S.lg}px` }}>
          <button style={{ ...btnPrimary, width: '100%', padding: '10px 0', fontSize: 14, opacity: 0.5 }} disabled>
            保存配置
          </button>
          <div style={{ fontSize: 11, color: T.tertiary, textAlign: 'center', marginTop: S.sm }}>
            L2/L3 路由功能开发中
          </div>
        </div>
      </div>

      {/* ── Right: Chat test panel ── */}
      <ChatPanel />
    </div>
  );
}

// ── 测试对话面板 ──

function ChatPanel() {
  const [messages, setMessages] = useState<Array<{role: string; content: string; intent?: string; tier?: string}>>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const bottomRef = { current: null as HTMLDivElement | null };

  async function send() {
    const q = input.trim();
    if (!q || sending) return;
    setMessages(p => [...p, { role: 'user', content: q }]);
    setInput(''); setSending(true);
    try {
      const r = await fetch(`${API}/api/agents/supervisor/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': '1' },
        body: JSON.stringify({ question: q }),
      });
      const d = await r.json();
      const trace = d.trace || {};
      setMessages(p => [...p, {
        role: 'agent',
        content: d.answer || '无回复',
        intent: trace.intent,
        tier: trace.route_tier,
      }]);
    } catch {
      setMessages(p => [...p, { role: 'agent', content: '请求失败' }]);
    } finally {
      setSending(false);
    }
  }

  const tierBadge = (t: string) => {
    if (!t) return null;
    const c = t === 'L1' ? '#10B981' : t === 'L2' ? T.accent : '#8B5CF6';
    return <span style={{ fontSize: 10, padding: '1px 6px', borderRadius: 8, background: c + '18', color: c, fontWeight: 600, marginLeft: 6 }}>{t}</span>;
  };

  return (
    <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: T.bg }}>
      {/* Header */}
      <div style={{ padding: `${S.md}px ${S.xl}px`, borderBottom: `1px solid ${T.border}`, display: 'flex', alignItems: 'center', gap: S.sm }}>
        <span style={{ width: 7, height: 7, borderRadius: '50%', background: T.accent }} />
        <span style={{ fontSize: 14, fontWeight: 500, color: T.text }}>测试对话</span>
        <div style={{ flex: 1 }} />
        <button onClick={() => setMessages([])} style={{
          padding: '4px 12px', borderRadius: 6, border: `1px solid ${T.border}`,
          background: T.surface, color: T.text, fontSize: 11, cursor: 'pointer',
        }}>清空</button>
      </div>

      {/* Messages */}
      <div style={{ flex: 1, padding: S.xl, overflow: 'auto' }}>
        {messages.length === 0 && (
          <div style={{ textAlign: 'center', color: T.secondary, marginTop: S.huge, fontSize: 14 }}>
            输入客户问题，测试 Supervisor 的路由效果
          </div>
        )}
        {messages.map((m, i) => (
          <div key={i} style={{ marginBottom: S.lg, display: 'flex', flexDirection: 'column', alignItems: m.role === 'user' ? 'flex-end' : 'flex-start' }}>
            <div style={{ fontSize: 12, color: T.secondary, marginBottom: 6 }}>
              {m.role === 'user' ? '测试用户' : 'Supervisor'}
              {m.intent && <span style={{ fontSize: 10, color: T.tertiary, marginLeft: 6 }}>→ {m.intent}</span>}
              {m.tier && tierBadge(m.tier)}
            </div>
            <div style={{
              maxWidth: '72%', padding: `${S.md}px ${S.base}px`, borderRadius: 8,
              fontSize: 14, lineHeight: 1.55, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
              background: m.role === 'user' ? T.accent : T.surface,
              color: m.role === 'user' ? '#fff' : T.text,
              border: m.role === 'user' ? 'none' : `1px solid ${T.border}`,
            }}>
              {m.content}
            </div>
          </div>
        ))}
        {sending && <div style={{ color: T.secondary, fontSize: 13 }}>Supervisor 处理中...</div>}
        <div ref={el => { bottomRef.current = el; if (el) el.scrollIntoView({ behavior: 'smooth' }); }} />
      </div>

      {/* Input */}
      <div style={{ padding: `${S.base}px ${S.xl}px ${S.lg}px` }}>
        <div style={{ display: 'flex', gap: S.sm }}>
          <input
            value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && send()}
            placeholder="输入客户问题测试路由..."
            style={{
              flex: 1, padding: '10px 14px', background: T.surface,
              border: `1px solid ${T.border}`, borderRadius: 6,
              fontSize: 14, color: T.text, outline: 'none', fontFamily: 'inherit',
            }}
          />
          <button onClick={send} disabled={!input.trim() || sending} style={{
            ...btnPrimary, padding: '10px 22px', fontSize: 14,
            opacity: !input.trim() || sending ? 0.4 : 1,
          }}>
            发送
          </button>
        </div>
      </div>
    </div>
  );
}
