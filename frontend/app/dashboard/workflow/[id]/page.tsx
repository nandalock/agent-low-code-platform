'use client';

import { useParams, useRouter } from 'next/navigation';
import { useRef, useState, useEffect } from 'react';
import Link from 'next/link';
import { Play, X, ChevronDown, ChevronRight } from 'lucide-react';
import { T, S } from '@/app/theme';
import Canvas, { type CanvasRef } from './Canvas';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface PreviewMsg {
  role: 'user' | 'agent';
  content: string;
  time: string;
  sender?: string;
}

interface ThinkStep {
  node_id: string;
  label: string;
  type: string;
  output: string;
  ms: number;
}

export default function WorkflowDetailPage() {
  const params = useParams();
  const router = useRouter();
  const id = params.id as string;
  const isNew = id === 'new';
  const canvasRef = useRef<CanvasRef>(null);
  const [name, setName] = useState('');
  const [showNameDialog, setShowNameDialog] = useState(false);
  const [saving, setSaving] = useState(false);

  // Preview panel
  const [showPreview, setShowPreview] = useState(false);
  const [messages, setMessages] = useState<PreviewMsg[]>([]);
  const [previewInput, setPreviewInput] = useState('');
  const [previewSending, setPreviewSending] = useState(false);
  const [thinkSteps, setThinkSteps] = useState<ThinkStep[]>([]);
  const [thinkOpen, setThinkOpen] = useState(false);
  const chatEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => { chatEndRef.current?.scrollIntoView({ behavior: 'smooth' }); }, [messages]);

  const doSave = async (saveName?: string) => {
    const data = canvasRef.current?.getData();
    if (!data) return;
    setSaving(true);
    try {
      const url = isNew ? `${API}/api/workflows` : `${API}/api/workflows/${id}`;
      const method = isNew ? 'POST' : 'PUT';
      const body = JSON.stringify({
        name: saveName || '未命名工作流',
        nodes: data.nodes,
        edges: data.edges,
        ...(isNew ? {} : { status: 'draft' }),
      });

      const r = await fetch(url, { method, headers: { 'Content-Type': 'application/json' }, body });
      if (r.ok) {
        if (isNew) {
          const d = await r.json();
          router.replace(`/dashboard/workflow/${d.id}`);
        }
        setShowNameDialog(false);
      }
    } catch {}
    setSaving(false);
  };

  const handleSaveClick = () => {
    if (isNew) {
      setShowNameDialog(true);
    } else {
      doSave();
    }
  };

  const handlePreviewSend = async () => {
    if (!previewInput.trim() || previewSending || isNew) return;
    const now = new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' });
    const userMsg: PreviewMsg = { role: 'user', content: previewInput, time: now };
    setMessages(prev => [...prev, userMsg]);
    setPreviewInput('');
    setPreviewSending(true);
    setThinkSteps([]);
    setThinkOpen(false);

    try {
      // 先保存最新节点配置（含 agent_key 等），确保运行读到最新数据
      const data = canvasRef.current?.getData();
      if (data) {
        await fetch(`${API}/api/workflows/${id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ nodes: data.nodes, edges: data.edges }),
        });
      }

      const r = await fetch(`${API}/api/workflows/${id}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ input: previewInput, user_id: 'preview' }),
      });
      const reader = r.body?.getReader();
      if (!reader) { setPreviewSending(false); return; }
      const decoder = new TextDecoder();
      let buf = '';
      while (true) {
        const { done, value } = await reader.read();
        if (done) break;
        buf += decoder.decode(value, { stream: true });
        const lines = buf.split('\n');
        buf = lines.pop() || '';
        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const d = JSON.parse(line.slice(6));
              if (d.type === 'node_done') {
                setThinkSteps(prev => [...prev, {
                  node_id: d.node_id,
                  label: d.label,
                  type: d.node_type || d.type,
                  output: d.output || '',
                  ms: d.ms || 0,
                }]);
              } else if (d.type === 'workflow_done') {
                setThinkOpen(false); // 完成后折叠
                const finalOutput = d.output || '';
                setMessages(prev => [...prev, {
                  role: 'agent',
                  content: finalOutput || '工作流执行完成',
                  time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }),
                }]);
              } else if (d.type === 'error') {
                setMessages(prev => [...prev, { role: 'agent', content: d.message, time: new Date().toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) }]);
              }
            } catch {}
          }
        }
      }
    } catch {}
    setPreviewSending(false);
  };

  return (
    <div style={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      {/* Top bar */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: `${S.sm}px ${S.xl}px`, background: '#fff', borderBottom: `1px solid ${T.border}`,
        flexShrink: 0,
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: S.md }}>
          <Link href="/dashboard/workflow" style={{ fontSize: 12, color: T.accent, textDecoration: 'none' }}>
            ← 返回
          </Link>
          <span style={{ fontSize: 14, fontWeight: 500, color: T.text }}>工作流编辑器</span>
          <span style={{ fontSize: 11, color: T.tertiary, background: T.bg, padding: '2px 6px', borderRadius: 4 }}>
            id: {id}
          </span>
        </div>
        <div style={{ display: 'flex', gap: S.sm }}>
          <button onClick={handleSaveClick} disabled={saving} style={{
            padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
            background: T.surface, border: `1px solid ${T.border}`, color: T.text, fontFamily: 'inherit',
            opacity: saving ? 0.6 : 1,
          }}>{saving ? '保存中...' : '保存草稿'}</button>
          <button onClick={() => setShowPreview(!showPreview)} style={{
            padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
            display: 'flex', alignItems: 'center', gap: 5,
            background: showPreview ? T.accentBg : T.accent,
            color: showPreview ? T.accent : '#fff',
            border: showPreview ? `1px solid ${T.accent}` : 'none',
            fontFamily: 'inherit',
          }}>
            <Play size={12} />{showPreview ? '关闭预览' : '预览'}
          </button>
          <button style={{
            padding: '6px 14px', borderRadius: 6, fontSize: 12, cursor: 'pointer',
            background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit',
          }}>运行</button>
        </div>
      </div>

      {/* Content: Canvas + optional Preview panel */}
      <div style={{ flex: 1, display: 'flex', overflow: 'hidden' }}>
        {/* Canvas */}
        <div style={{ flex: 1, minWidth: 0 }}>
          <Canvas workflowId={id} ref={canvasRef} />
        </div>

        {/* Preview chat panel */}
        {showPreview && (
          <div style={{
            width: 380, flexShrink: 0, background: T.surface,
            borderLeft: `1px solid ${T.border}`, display: 'flex', flexDirection: 'column',
          }}>
            {/* Panel header */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: `${S.sm}px ${S.base}px`, borderBottom: `1px solid ${T.border}`,
            }}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
                <span style={{ width: 7, height: 7, borderRadius: '50%', background: T.success }} />
                <span style={{ fontSize: 13, fontWeight: 500, color: T.text }}>预览对话</span>
              </div>
              <button onClick={() => setShowPreview(false)} style={{
                width: 24, height: 24, borderRadius: 4, border: 'none', cursor: 'pointer',
                background: 'transparent', color: T.secondary, display: 'flex', alignItems: 'center', justifyContent: 'center',
              }}>
                <X size={14} />
              </button>
            </div>

            {/* Messages */}
            <div style={{ flex: 1, padding: S.base, overflow: 'auto' }}>
              {messages.length === 0 && (
                <div style={{ textAlign: 'center', color: T.tertiary, marginTop: S.huge, fontSize: 13 }}>
                  输入消息预览工作流效果
                </div>
              )}
              {messages.map((msg, i) => {
                const isUser = msg.role === 'user';
                return (
                  <div key={i} style={{ marginBottom: S.lg, display: 'flex', flexDirection: 'column', alignItems: isUser ? 'flex-end' : 'flex-start' }}>
                    <div style={{ fontSize: 11, color: T.secondary, marginBottom: 4 }}>
                      {isUser ? '测试用户' : (msg.sender || '工作流')} · {msg.time}
                    </div>
                    <div style={{
                      maxWidth: '85%', padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
                      fontSize: 13, lineHeight: 1.5, whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                      background: isUser ? T.accent : T.bg,
                      color: isUser ? '#fff' : T.text,
                      borderBottomRightRadius: isUser ? 2 : 8,
                      borderBottomLeftRadius: isUser ? 8 : 2,
                    }}>
                      {msg.content}
                    </div>
                  </div>
                );
              })}

              {/* Thinking process — collapsible */}
              {thinkSteps.length > 0 && (
                <div style={{ marginBottom: S.lg }}>
                  <button
                    onClick={() => setThinkOpen(v => !v)}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 4, padding: 0,
                      border: 'none', background: 'transparent', cursor: 'pointer',
                      fontSize: 12, color: T.secondary, fontFamily: 'inherit',
                    }}
                  >
                    {thinkOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                    思考过程 ({thinkSteps.length} 步)
                  </button>
                  {thinkOpen && (
                    <div style={{
                      marginTop: S.xs, padding: `${S.sm}px ${S.md}px`, borderRadius: 8,
                      background: '#F0F1F5', border: `1px solid ${T.border}`,
                    }}>
                      {thinkSteps.map((s, j) => {
                        const isFinal = j === thinkSteps.length - 1;
                        return (
                          <div key={j} style={{
                            display: 'flex', gap: S.sm, padding: `${S.xs}px 0`,
                            borderBottom: isFinal ? 'none' : `1px solid #E5E6EB`,
                            fontSize: 12,
                          }}>
                            <span style={{ color: T.secondary, flexShrink: 0, minWidth: 60, fontWeight: 500 }}>
                              {s.label}
                            </span>
                            <span style={{ color: T.tertiary, flex: 1, whiteSpace: 'pre-wrap', wordBreak: 'break-word', lineHeight: 1.5 }}>
                              {s.output || '(无输出)'}
                            </span>
                            {s.ms > 0 && (
                              <span style={{ color: T.tertiary, flexShrink: 0 }}>{s.ms}ms</span>
                            )}
                          </div>
                        );
                      })}
                    </div>
                  )}
                </div>
              )}

              {previewSending && <div style={{ color: T.secondary, fontSize: 12, paddingLeft: 4 }}>工作流运行中...</div>}
              <div ref={chatEndRef} />
            </div>

            {/* Input */}
            <div style={{ padding: `${S.sm}px ${S.base}px ${S.base}px`, borderTop: `1px solid ${T.border}` }}>
              <div style={{ display: 'flex', gap: S.xs }}>
                <input
                  value={previewInput}
                  onChange={e => setPreviewInput(e.target.value)}
                  onKeyDown={e => e.key === 'Enter' && handlePreviewSend()}
                  placeholder="输入消息预览工作流..."
                  style={{
                    flex: 1, padding: '8px 12px', background: T.bg, border: `1px solid ${T.border}`,
                    borderRadius: 6, fontSize: 13, color: T.text, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit',
                  }}
                />
                <button onClick={handlePreviewSend} disabled={!previewInput.trim() || previewSending} style={{
                  padding: '8px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                  background: T.accent, color: '#fff', border: 'none', fontFamily: 'inherit',
                  opacity: !previewInput.trim() || previewSending ? 0.4 : 1,
                }}>发送</button>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* Name dialog — only for new workflows */}
      {showNameDialog && (
        <div style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.3)', display: 'flex',
          alignItems: 'center', justifyContent: 'center', zIndex: 100,
        }} onClick={() => setShowNameDialog(false)}>
          <div style={{
            background: '#fff', borderRadius: 12, padding: S.xl, width: 360,
            boxShadow: '0 8px 32px rgba(0,0,0,0.12)',
          }} onClick={e => e.stopPropagation()}>
            <h3 style={{ margin: 0, fontSize: 16, fontWeight: 600, color: T.text, marginBottom: S.lg }}>新建工作流</h3>
            <label style={{ fontSize: 13, color: T.secondary, display: 'block', marginBottom: S.xs }}>工作流名称</label>
            <input
              autoFocus
              value={name}
              onChange={e => setName(e.target.value)}
              onKeyDown={e => { if (e.key === 'Enter') doSave(name); if (e.key === 'Escape') setShowNameDialog(false); }}
              placeholder="输入工作流名称"
              style={{
                width: '100%', padding: '8px 12px', borderRadius: 6, border: `1px solid ${T.border}`,
                fontSize: 14, color: T.text, outline: 'none', boxSizing: 'border-box', fontFamily: 'inherit',
              }}
            />
            <div style={{ display: 'flex', gap: S.sm, marginTop: S.lg, justifyContent: 'flex-end' }}>
              <button onClick={() => setShowNameDialog(false)} style={{
                padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                background: T.bg, border: 'none', color: T.secondary, fontFamily: 'inherit',
              }}>取消</button>
              <button onClick={() => doSave(name)} style={{
                padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
                background: T.accent, border: 'none', color: '#fff', fontFamily: 'inherit', fontWeight: 500,
              }}>创建</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
