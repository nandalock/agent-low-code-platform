'use client';

import { useEffect, useState, useRef, useCallback } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface Conversation {
  cid: string;
  buyer_name: string;
  last_msg: string;
  last_time: string;
  count: number;
}

interface Message {
  cid: string;
  sender_id: string;
  sender_name: string;
  content: string;
  time: string;
}

export default function ChatPageInner() {
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [selectedCid, setSelectedCid] = useState<string | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [loading, setLoading] = useState(false);
  const selectedCidRef = useRef<string | null>(null);

  useEffect(() => {
    selectedCidRef.current = selectedCid;
  }, [selectedCid]);

  // HTTP 拉会话列表 — 初始数据
  const loadConversations = useCallback(async () => {
    try {
      const res = await fetch(`${API}/api/xianyu/conversations`);
      if (res.ok) {
        const data = await res.json();
        setConversations(data.conversations || []);
      }
    } catch {}
  }, []);

  // HTTP 拉消息 — 选中会话时
  const loadMessages = useCallback(async (cid: string) => {
    setLoading(true);
    try {
      const res = await fetch(`${API}/api/xianyu/conversations/${cid}`);
      const data = await res.json();
      setMessages(data.messages || []);
    } catch {} finally {
      setLoading(false);
    }
  }, []);

  // 初始加载
  useEffect(() => {
    loadConversations();
  }, [loadConversations]);

  // 选中会话时加载消息
  useEffect(() => {
    if (!selectedCid) return;
    loadMessages(selectedCid);
  }, [selectedCid, loadMessages]);

  // WebSocket — 只负责新消息推送
  useEffect(() => {
    const wsUrl = API.replace('https://', 'wss://').replace('http://', 'ws://') + '/api/ws/chat';
    const ws = new WebSocket(wsUrl);

    ws.onopen = () => console.log('[WS] chat connected');

    ws.onmessage = (event) => {
      const msg = JSON.parse(event.data);
      if (msg.type !== 'new_message') return;

      // 更新会话列表
      if (msg.conversation) {
        setConversations(prev => {
          const idx = prev.findIndex(c => c.cid === msg.conversation.cid);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = msg.conversation;
            return next;
          }
          return [msg.conversation, ...prev];
        });
      }
      // 当前正在看的会话，追加消息
      if (msg.data && msg.data.cid === selectedCidRef.current) {
        setMessages(prev => [...prev, msg.data]);
      }
    };

    ws.onclose = () => console.log('[WS] chat disconnected');

    return () => { ws.close(); };
  }, []);

  return (
    <div style={{ display: 'flex', height: '100%', gap: 16 }}>
      {/* 左侧会话列表 */}
      <div style={{
        width: 260, background: '#fff', borderRadius: 8,
        display: 'flex', flexDirection: 'column', flexShrink: 0,
      }}>
        <div style={{ padding: '14px 16px', borderBottom: '1px solid #f0f0f0', fontWeight: 500, fontSize: 14 }}>
          会话 ({conversations.length})
        </div>
        <div style={{ flex: 1, overflow: 'auto' }}>
          {conversations.length === 0 && (
            <p style={{ textAlign: 'center', color: '#ccc', fontSize: 13, marginTop: 40 }}>
              暂无会话
            </p>
          )}
          {conversations.map(c => (
            <div
              key={c.cid}
              onClick={() => setSelectedCid(c.cid)}
              style={{
                padding: '12px 16px', cursor: 'pointer',
                background: selectedCid === c.cid ? '#e6f4ff' : 'transparent',
                borderBottom: '1px solid #f5f5f5',
              }}
            >
              <div style={{ display: 'flex', justifyContent: 'space-between', marginBottom: 4 }}>
                <span style={{ fontSize: 14, fontWeight: 500 }}>{c.buyer_name || '未知'}</span>
                <span style={{ fontSize: 11, color: '#999' }}>
                  {c.last_time ? new Date(c.last_time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}
                </span>
              </div>
              <div style={{ fontSize: 13, color: '#666', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                {c.last_msg}
              </div>
              {c.count > 0 && (
                <span style={{ fontSize: 11, color: '#999' }}>{c.count} 条消息</span>
              )}
            </div>
          ))}
        </div>
      </div>

      {/* 右侧消息区 */}
      <div style={{ flex: 1, background: '#fff', borderRadius: 8, display: 'flex', flexDirection: 'column' }}>
        {!selectedCid ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999', fontSize: 14 }}>
            选择一个会话
          </div>
        ) : loading ? (
          <div style={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#999' }}>
            加载中...
          </div>
        ) : (
          <div style={{ flex: 1, padding: 16, overflow: 'auto' }}>
            {messages.length === 0 && (
              <p style={{ textAlign: 'center', color: '#ccc', marginTop: 60 }}>暂无消息</p>
            )}
            {messages.map((msg, i) => (
              <div key={i} style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 12, color: '#999', marginBottom: 4 }}>
                  {msg.sender_name} · {msg.time ? new Date(msg.time).toLocaleTimeString('zh-CN', { hour: '2-digit', minute: '2-digit' }) : ''}
                </div>
                <div style={{
                  display: 'inline-block', maxWidth: '80%', padding: '8px 14px', borderRadius: 8,
                  background: '#f0f0f0', fontSize: 14, lineHeight: 1.5,
                  whiteSpace: 'pre-wrap', wordBreak: 'break-word',
                }}>
                  {msg.content}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
