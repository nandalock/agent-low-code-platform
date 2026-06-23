'use client';

import { useState } from 'react';
import { useRouter } from 'next/navigation';

export default function LoginPage() {
  const router = useRouter();
  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(false);

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    setError('');
    setLoading(true);

    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });

    if (res.ok) {
      router.push('/dashboard');
    } else {
      const data = await res.json();
      setError(data.error);
    }
    setLoading(false);
  }

  return (
    <div style={{
      display: 'flex', alignItems: 'center', justifyContent: 'center',
      height: '100vh', background: '#f0f2f5',
    }}>
      <form onSubmit={handleSubmit} style={{
        background: '#fff', padding: 40, borderRadius: 8,
        width: 360, boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
      }}>
        <h2 style={{ textAlign: 'center', marginBottom: 24 }}>智能客服</h2>

        <input
          type="text" placeholder="账号" value={username}
          onChange={e => setUsername(e.target.value)}
          style={inputStyle}
        />
        <input
          type="password" placeholder="密码" value={password}
          onChange={e => setPassword(e.target.value)}
          style={inputStyle}
        />

        {error && <p style={{ color: '#ff4d4f', fontSize: 14, marginBottom: 12 }}>{error}</p>}

        <button type="submit" disabled={loading} style={{
          width: '100%', padding: 10, background: '#1677ff', color: '#fff',
          border: 'none', borderRadius: 6, fontSize: 16, cursor: 'pointer',
        }}>
          {loading ? '登录中...' : '登录'}
        </button>
      </form>
    </div>
  );
}

const inputStyle: React.CSSProperties = {
  display: 'block', width: '100%', padding: '10px 12px',
  marginBottom: 16, border: '1px solid #d9d9d9', borderRadius: 6,
  fontSize: 14, boxSizing: 'border-box',
};
