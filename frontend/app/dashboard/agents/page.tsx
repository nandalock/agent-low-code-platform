'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface AgentInfo {
  key: string;
  name: string;
  desc: string;
  status: string;
}

export default function AgentsPage() {
  const router = useRouter();
  const [agents, setAgents] = useState<AgentInfo[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/agents`)
      .then(r => r.json())
      .then(data => setAgents(data))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 20 }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Agent 管理</h2>
        <button style={{ padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: 'pointer', background: '#1677ff', color: '#fff', border: 'none' }}>
          + 新建 Agent
        </button>
      </div>

      {loading && <p style={{ textAlign: 'center', color: '#ccc', marginTop: 60 }}>加载中...</p>}

      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(300px, 1fr))', gap: 16 }}>
        {agents.map(agent => (
          <div
            key={agent.key}
            onClick={() => router.push(`/agents/${agent.key}`)}
            style={{
              background: '#fff', borderRadius: 8, padding: 20, cursor: 'pointer',
              border: '1px solid #f0f0f0', transition: 'box-shadow 0.2s',
            }}
            onMouseEnter={e => (e.currentTarget.style.boxShadow = '0 4px 12px rgba(0,0,0,0.08)')}
            onMouseLeave={e => (e.currentTarget.style.boxShadow = 'none')}
          >
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 8 }}>
              <span style={{ fontSize: 15, fontWeight: 500 }}>{agent.name}</span>
              <span style={{
                fontSize: 11, padding: '2px 8px', borderRadius: 10,
                background: agent.status === 'active' ? '#e6f7e9' : '#f5f5f5',
                color: agent.status === 'active' ? '#52c41a' : '#999',
              }}>
                {agent.status === 'active' ? '运行中' : '草稿'}
              </span>
            </div>
            <p style={{ fontSize: 13, color: '#666', margin: 0, lineHeight: 1.5 }}>{agent.desc}</p>
          </div>
        ))}
      </div>
    </div>
  );
}
