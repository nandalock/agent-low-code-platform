'use client';

import { useState } from 'react';
import { useParams } from 'next/navigation';

const AGENTS: Record<string, { name: string; desc: string }> = {
  '1': { name: '客服 Agent 1.1', desc: '通用客服机器人' },
  '2': { name: '售后专家 Agent', desc: '专注售后服务场景' },
  '3': { name: '物流助手 Agent', desc: '查询快递状态' },
};

interface ChatMsg {
  role: 'user' | 'agent';
  content: string;
}

export default function AgentDetailPage() {
  const { id } = useParams<{ id: string }>();
  const agent = AGENTS[id] || { name: '未知 Agent', desc: '' };

  const [systemPrompt, setSystemPrompt] = useState(
    '你是一个专业的客服助手，请用友好、耐心的语气回答用户的问题。',
  );
  const [model, setModel] = useState('deepseek-v4-pro');
  const [temperature, setTemperature] = useState(0.7);

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState('');
  const [sending, setSending] = useState(false);

  async function handleSend() {
    if (!input.trim() || sending) return;
    const userMsg: ChatMsg = { role: 'user', content: input };
    setMessages(prev => [...prev, userMsg]);
    setInput('');
    setSending(true);

    setTimeout(() => {
      setMessages(prev => [...prev, { role: 'agent', content: `你好！我是 ${agent.name}，正在建设中。你的问题：${input}` }]);
      setSending(false);
    }, 800);
  }

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      {/* 左侧设计面板 */}
      <div style={{
        width: 360, background: '#fff', borderRight: '1px solid #e8e8e8',
        display: 'flex', flexDirection: 'column', flexShrink: 0, overflow: 'auto',
      }}>
        <div style={{ padding: '16px 20px', borderBottom: '1px solid #f0f0f0' }}>
          <h3 style={{ margin: 0, fontSize: 16 }}>{agent.name}</h3>
          <p style={{ margin: '4px 0 0', fontSize: 12, color: '#999' }}>{agent.desc}</p>
        </div>

        <div style={{ flex: 1, padding: 20, display: 'flex', flexDirection: 'column', gap: 16 }}>
          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 6, color: '#333' }}>
              系统提示词
            </label>
            <textarea
              value={systemPrompt}
              onChange={e => setSystemPrompt(e.target.value)}
              rows={6}
              style={{
                width: '100%', padding: '10px 12px', border: '1px solid #d9d9d9', borderRadius: 6,
                fontSize: 13, fontFamily: 'monospace', lineHeight: 1.6, resize: 'vertical',
                boxSizing: 'border-box',
              }}
            />
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 6, color: '#333' }}>
              模型
            </label>
            <select
              value={model}
              onChange={e => setModel(e.target.value)}
              style={{
                width: '100%', padding: '8px 12px', border: '1px solid #d9d9d9', borderRadius: 6,
                fontSize: 13, background: '#fff', boxSizing: 'border-box',
              }}
            >
              <option value="deepseek-v4-pro">DeepSeek V4 Pro</option>
              <option value="deepseek-v4-flash">DeepSeek V4 Flash</option>
              <option value="gpt-4o">GPT-4o</option>
              <option value="claude-opus-4-7">Claude Opus 4.7</option>
            </select>
          </div>

          <div>
            <label style={{ display: 'block', fontSize: 13, fontWeight: 500, marginBottom: 6, color: '#333' }}>
              温度: {temperature}
            </label>
            <input
              type="range" min="0" max="2" step="0.1"
              value={temperature}
              onChange={e => setTemperature(parseFloat(e.target.value))}
              style={{ width: '100%' }}
            />
            <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: '#999' }}>
              <span>精准</span><span>平衡</span><span>创意</span>
            </div>
          </div>
        </div>

        <div style={{ padding: '16px 20px', borderTop: '1px solid #f0f0f0' }}>
          <button style={{
            width: '100%', padding: '8px 0', borderRadius: 6, fontSize: 14, cursor: 'pointer',
            background: '#1677ff', color: '#fff', border: 'none',
          }}>
            保存配置
          </button>
        </div>
      </div>

      {/* 右侧聊天界面 */}
      <div style={{ flex: 1, display: 'flex', flexDirection: 'column', background: '#fafafa' }}>
        <div style={{
          padding: '12px 20px', background: '#fff', borderBottom: '1px solid #e8e8e8',
          fontSize: 14, fontWeight: 500, display: 'flex', alignItems: 'center', gap: 8,
        }}>
          <span style={{ display: 'inline-block', width: 8, height: 8, borderRadius: '50%', background: '#faad14' }} />
          测试对话
        </div>

        <div style={{ flex: 1, padding: 20, overflow: 'auto' }}>
          {messages.length === 0 && (
            <div style={{ textAlign: 'center', color: '#ccc', marginTop: 80, fontSize: 14 }}>
              在左侧配置 Agent 后，在此测试对话效果
            </div>
          )}
          {messages.map((msg, i) => (
            <div key={i} style={{ marginBottom: 16, display: 'flex', justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start' }}>
              <div style={{
                maxWidth: '75%', padding: '10px 16px', borderRadius: 8,
                background: msg.role === 'user' ? '#1677ff' : '#fff',
                color: msg.role === 'user' ? '#fff' : '#333',
                fontSize: 14, lineHeight: 1.6,
                whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                border: msg.role === 'agent' ? '1px solid #e8e8e8' : 'none',
              }}>
                {msg.content}
              </div>
            </div>
          ))}
          {sending && <div style={{ textAlign: 'left', color: '#999', fontSize: 13 }}>Agent 回复中...</div>}
        </div>

        <div style={{ padding: '12px 20px', background: '#fff', borderTop: '1px solid #e8e8e8' }}>
          <div style={{ display: 'flex', gap: 10 }}>
            <input
              value={input}
              onChange={e => setInput(e.target.value)}
              onKeyDown={e => e.key === 'Enter' && handleSend()}
              placeholder="输入消息测试 Agent..."
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
    </div>
  );
}
