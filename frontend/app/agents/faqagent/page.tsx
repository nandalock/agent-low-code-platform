'use client';

import { useState, useRef, useEffect } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

interface ChatMsg {
  role: 'user' | 'agent';
  content: string;
  faq?: { question: string; answer: string; tags: string[] };
  trace?: {
    pg_trgm_score: number;
    vector_top_n: number;
    rerank_top_k: number;
    rerank_enabled: boolean;
    candidates_count: number;
    chunks: { question: string; answer: string; score: number }[];
  };
}

const DEFAULT_CONFIG = {
  direct_threshold: 0.85,
  vector_top_n: 10,
  rerank_top_k: 3,
  rerank_enabled: true,
  system_prompt: '',
  fallback_reply: '',
  api_key: '',
  base_url: '',
  model: '',
};

export default function FaqAgentPage() {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);
  const [selectedTrace, setSelectedTrace] = useState<ChatMsg['trace'] | null>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  const [config, setConfig] = useState(DEFAULT_CONFIG);
  const [saving, setSaving] = useState(false);
  const [saveMsg, setSaveMsg] = useState('');

  useEffect(() => {
    fetch(`${API}/api/agents/faqagent/config`)
      .then(res => res.json())
      .then(data => {
        if (data.config) setConfig(data.config);
      })
      .catch(() => {});
  }, []);

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages]);

  async function handleSaveConfig() {
    setSaving(true);
    setSaveMsg('');
    try {
      const res = await fetch(`${API}/api/agents/faqagent/config`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(config),
      });
      if (res.ok) {
        setSaveMsg('保存成功');
      } else {
        setSaveMsg('保存失败');
      }
    } catch {
      setSaveMsg('保存失败');
    } finally {
      setSaving(false);
    }
  }

  async function handleSend() {
    if (!input.trim() || sending) return;
    const userMsg: ChatMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setSending(true);

    try {
      const res = await fetch(`${API}/api/agents/faqagent/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Tenant-ID': String(TENANT_ID) },
        body: JSON.stringify({ question: input }),
      });
      const data = await res.json();

      setMessages(prev => [...prev, {
        role: 'agent',
        content: data.answer || '抱歉，暂时无法回答这个问题。',
        faq: data.sources?.[0] || undefined,
        trace: data.trace || undefined,
      }]);
      setSelectedTrace(data.trace || null);
    } catch {
      setMessages(prev => [...prev, { role: 'agent', content: '请求失败，请稍后重试。' }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      {/* ===== 左侧配置面板 ===== */}
      <div style={{
        width: 360, background: '#fff', borderRight: '1px solid #e8e8e8',
        display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'auto',
      }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #f0f0f0' }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>FAQ 智能体</h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#999' }}>基于知识库的多层匹配检索</p>
        </div>

        <div style={{ flex: 1, padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div style={{
            background: '#f5f5f5', borderRadius: 8, padding: 14, fontSize: 13, lineHeight: 1.8,
          }}>
            <div style={{ fontWeight: 500, marginBottom: 6, color: '#333' }}>匹配策略</div>
            <div style={{ color: '#666', fontSize: 12 }}>
              1. pg_trgm 相似度 &gt; 阈值 → 直接回答
            </div>
            <div style={{ color: '#666', fontSize: 12 }}>
              2. pgvector 语义搜索 top-N → reranker 精排 top-K
            </div>
            <div style={{ color: '#666', fontSize: 12 }}>
              3. LLM RAG 润色 → 生成回答
            </div>
            <div style={{ color: '#666', fontSize: 12 }}>
              4. 全不中 → 兜底回复
            </div>
          </div>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            直接回答阈值
            <input
              type="number"
              step="0.01"
              min="0"
              max="1"
              value={config.direct_threshold}
              onChange={e => setConfig(prev => ({ ...prev, direct_threshold: parseFloat(e.target.value) || 0 }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            语义检索 top-N
            <input
              type="number"
              min="1"
              max="50"
              value={config.vector_top_n}
              onChange={e => setConfig(prev => ({ ...prev, vector_top_n: parseInt(e.target.value) || 10 }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            Reranker 精排 top-K
            <input
              type="number"
              min="1"
              max="10"
              value={config.rerank_top_k}
              onChange={e => setConfig(prev => ({ ...prev, rerank_top_k: parseInt(e.target.value) || 3 }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8, cursor: 'pointer' }}>
            <input
              type="checkbox"
              checked={config.rerank_enabled}
              onChange={e => setConfig(prev => ({ ...prev, rerank_enabled: e.target.checked }))}
              style={{ width: 16, height: 16, cursor: 'pointer' }}
            />
            启用 Reranker 精排
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            系统提示词
            <textarea
              rows={4}
              value={config.system_prompt}
              onChange={e => setConfig(prev => ({ ...prev, system_prompt: e.target.value }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 13,
                resize: 'vertical', boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            兜底回复
            <input
              type="text"
              value={config.fallback_reply}
              onChange={e => setConfig(prev => ({ ...prev, fallback_reply: e.target.value }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <div style={{ fontSize: 12, fontWeight: 500, color: '#999', marginTop: 8 }}>
            LLM 配置（RAG 润色用）
          </div>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            API Key
            <input
              type="password"
              value={config.api_key}
              onChange={e => setConfig(prev => ({ ...prev, api_key: e.target.value }))}
              placeholder="sk-xxx"
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            Base URL
            <input
              type="text"
              value={config.base_url}
              onChange={e => setConfig(prev => ({ ...prev, base_url: e.target.value }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>

          <label style={{ fontSize: 13, fontWeight: 500 }}>
            Model
            <input
              type="text"
              value={config.model}
              onChange={e => setConfig(prev => ({ ...prev, model: e.target.value }))}
              style={{
                width: '100%', marginTop: 4, padding: '6px 10px',
                border: '1px solid #d9d9d9', borderRadius: 6, fontSize: 14,
                boxSizing: 'border-box',
              }}
            />
          </label>
        </div>

        <div style={{ padding: '16px 20px', borderTop: '1px solid #f0f0f0' }}>
          {saveMsg && (
            <div style={{
              fontSize: 12, marginBottom: 8,
              color: saveMsg === '保存成功' ? '#52c41a' : '#ff4d4f',
            }}>
              {saveMsg}
            </div>
          )}
          <button
            onClick={handleSaveConfig}
            disabled={saving}
            style={{
              width: '100%', padding: '8px 0', borderRadius: 6, fontSize: 14, cursor: 'pointer',
              background: '#1677ff', color: '#fff', border: 'none',
              opacity: saving ? 0.5 : 1,
            }}
          >
            {saving ? '保存中...' : '保存配置'}
          </button>
        </div>
      </div>

      {/* ===== 右侧聊天界面 ===== */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#fafafa' }}>
        <div style={{
          padding: '12px 20px', background: '#fff', borderBottom: '1px solid #e8e8e8',
          fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#52c41a' }} />
          测试对话
        </div>

        <div style={{ flex: 1, padding: 20, overflow: 'auto' }}>
          {messages.length === 0 && (
            <div style={{ textAlign: 'center', color: '#ccc', marginTop: 80, fontSize: 14 }}>
              在下方输入客户问题，测试 FAQ 智能体的匹配效果
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} style={{ marginBottom: 16, display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <div style={{ maxWidth: '75%' }}>
                <div style={{
                  padding: '10px 16px', borderRadius: 8,
                  background: msg.role === 'user' ? '#1677ff' : '#fff',
                  color: msg.role === 'user' ? '#fff' : '#333',
                  fontSize: 14, lineHeight: 1.6,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                  border: msg.role === 'agent' ? '1px solid #e8e8e8' : 'none',
                }}>
                  {msg.content}
                </div>
                {msg.faq && (
                  <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
                    {msg.faq.tags?.map(t => (
                      <span key={t} style={{
                        display: 'inline-block', padding: '1px 8px', borderRadius: 4,
                        background: '#f0f0f0', fontSize: 11, color: '#999',
                      }}>{t}</span>
                    ))}
                    <span style={{ fontSize: 11, color: '#1677ff' }}>来源: {msg.faq.question}</span>
                  </div>
                )}
              </div>
            </div>
          ))}
          {sending && (
            <div style={{ textAlign: 'left', color: '#999', fontSize: 13, paddingLeft: 4 }}>
              {input ? '检索知识库中...' : 'Agent 回复中...'}
            </div>
          )}
          <div ref={bottomRef} />
        </div>

        <div style={{ padding: '12px 20px', background: '#fff', borderTop: '1px solid #e8e8e8' }}>
          <div style={{ display: 'flex', gap: 10 }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
              placeholder="输入客户问题..."
              style={{
                flex: 1, padding: '8px 14px', border: '1px solid #d9d9d9', borderRadius: 6,
                fontSize: 14, outline: 'none', boxSizing: 'border-box',
              }}
            />
            <button
              onClick={handleSend}
              disabled={!input.trim() || sending}
              style={{
                padding: '8px 20px', borderRadius: 6, fontSize: 14, cursor: 'pointer',
                background: '#1677ff', color: '#fff', border: 'none',
                opacity: !input.trim() || sending ? 0.5 : 1,
              }}
            >
              发送
            </button>
          </div>
        </div>
      </div>

      {/* ===== 右侧检索面板 ===== */}
        {selectedTrace && (
          <div style={{
            width: 320, background: '#fff', borderLeft: '1px solid #e8e8e8',
            display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'auto',
          }}>
            <div style={{ padding: '12px 16px', borderBottom: '1px solid #f0f0f0', fontSize: 13, fontWeight: 500 }}>
              检索过程
            </div>
            <div style={{ flex: 1, padding: 16, overflow: 'auto' }}>
              <div style={{ fontSize: 12, lineHeight: 2, color: '#666' }}>
                <div>pg_trgm 相似度: <b>{selectedTrace.pg_trgm_score.toFixed(4)}</b></div>
                <div>向量检索 top: <b>{selectedTrace.vector_top_n}</b></div>
                <div>
                  Reranker 精排:{' '}
                  {selectedTrace.rerank_enabled
                    ? <b style={{ color: '#52c41a' }}>top-{selectedTrace.rerank_top_k}</b>
                    : <b style={{ color: '#999' }}>未启用</b>}
                </div>
                <div>候选数: <b>{selectedTrace.candidates_count}</b></div>
              </div>
              <div style={{ marginTop: 12, fontSize: 12, fontWeight: 500, color: '#333' }}>
                {selectedTrace.rerank_enabled ? 'Reranker 精排 Chunks' : '向量检索 Chunks（未精排）'}
              </div>
              {selectedTrace.chunks?.length === 0 && (
                <div style={{ fontSize: 12, color: '#999', marginTop: 8 }}>无匹配结果</div>
              )}
              {selectedTrace.chunks?.map((chunk, i) => (
                <div key={i} style={{
                  marginTop: 8, padding: 10, background: '#fafafa',
                  borderRadius: 6, fontSize: 12, border: '1px solid #f0f0f0',
                }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                    <span style={{ fontWeight: 500, color: '#333' }}>#{i + 1}</span>
                    <span style={{ color: '#1677ff' }}>{(chunk.score * 100).toFixed(1)}%</span>
                  </div>
                  <div style={{ color: '#333', marginBottom: 4 }}>{chunk.question}</div>
                  <div style={{ color: '#999', fontSize: 11 }}>{chunk.answer}</div>
                </div>
              ))}
            </div>
          </div>
        )}
    </div>
  );
}
