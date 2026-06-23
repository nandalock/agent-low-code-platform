'use client';

import { useEffect, useState } from 'react';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

interface PlatformCard {
  key: string;
  name: string;
  logo: string;
  connected: boolean;
}

const PLATFORMS: PlatformCard[] = [
  { key: 'xianyu', name: '闲鱼', logo: '/xianyu-logo.ico', connected: false },
];

export default function IntegrationsPage() {
  const [platforms, setPlatforms] = useState(PLATFORMS);
  const [activeManage, setActiveManage] = useState<string | null>(null);
  const [cookie, setCookie] = useState('');
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState('');

  // Load status & saved cookie on mount
  useEffect(() => {
    fetch(`${API}/api/xianyu/status`)
      .then(r => r.json())
      .then(data => {
        setPlatforms(prev => prev.map(p =>
          p.key === 'xianyu' ? { ...p, connected: data.is_online } : p
        ));
      })
      .catch(() => {});
    fetch(`${API}/api/xianyu/cookie`)
      .then(r => r.json())
      .then(data => {
        if (data.cookie) setCookie(data.cookie);
      })
      .catch(() => {});
  }, []);

  function openManage(key: string) {
    setActiveManage(key);
    setError('');
  }

  async function handleSaveCookie() {
    setSaving(true);
    setError('');
    try {
      const res = await fetch(`${API}/api/xianyu/connect`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ cookie }),
      });
      const data = await res.json();
      if (res.ok) {
        setPlatforms(prev =>
          prev.map(p => p.key === activeManage ? { ...p, connected: true } : p)
        );
        setActiveManage(null);
      } else {
        setError(data.detail || '连接失败');
      }
    } catch (err) {
      setError('网络错误');
    } finally {
      setSaving(false);
    }
  }

  async function handleDisconnect() {
    await fetch(`${API}/api/xianyu/disconnect`, { method: 'POST' });
    setPlatforms(prev =>
      prev.map(p => p.key === activeManage ? { ...p, connected: false } : p)
    );
    setActiveManage(null);
  }

  return (
    <div>
      <h2 style={{ margin: 0, fontSize: 18, marginBottom: 20 }}>集成管理</h2>

      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(4, 1fr)',
        gap: 16,
      }}>
        {platforms.map(p => (
          <div key={p.key} style={{
            background: '#fff', borderRadius: 10, padding: 24,
            display: 'flex', flexDirection: 'column', alignItems: 'center',
            border: '1px solid #f0f0f0',
          }}>
            {/* Logo */}
            <div style={{
              width: 80, height: 80, borderRadius: 20,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
              marginBottom: 12, overflow: 'hidden',
            }}>
              <img
                src={p.logo}
                alt={p.name}
                style={{ width: '100%', height: '100%', objectFit: 'contain' }}
                onError={e => {
                  (e.target as HTMLImageElement).style.display = 'none';
                }}
              />
            </div>

            {/* Name */}
            <span style={{ fontSize: 15, fontWeight: 500, marginBottom: 8 }}>{p.name}</span>

            {/* Status */}
            <div style={{ display: 'flex', alignItems: 'center', gap: 6, marginBottom: 16 }}>
              <span style={{
                width: 8, height: 8, borderRadius: '50%',
                background: p.connected ? '#52c41a' : '#d9d9d9',
                display: 'inline-block',
              }} />
              <span style={{ fontSize: 13, color: p.connected ? '#52c41a' : '#999' }}>
                {p.connected ? '已连接' : '未连接'}
              </span>
            </div>

            {/* Manage Button */}
            <button onClick={() => openManage(p.key)} style={{
              padding: '6px 20px', borderRadius: 6, fontSize: 13, cursor: 'pointer',
              background: '#fff', border: '1px solid #d9d9d9', color: '#333',
            }}>
              管理
            </button>
          </div>
        ))}
      </div>

      {/* Manage Modal */}
      {activeManage && (
        <div onClick={() => setActiveManage(null)} style={{
          position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)',
          display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000,
        }}>
          <div onClick={e => e.stopPropagation()} style={{
            background: '#fff', borderRadius: 12, padding: 24, width: 480,
          }}>
            <h3 style={{ margin: '0 0 20px', fontSize: 16 }}>
              {platforms.find(p => p.key === activeManage)?.name} — 连接方式
            </h3>

            {/* Method tabs */}
            <div style={{ display: 'flex', gap: 8, marginBottom: 20 }}>
              {[
                { key: 'cookie', label: 'Cookie', active: true },
                { key: 'qrcode', label: '二维码', active: false, disabled: true },
                { key: 'oauth', label: 'OAuth', active: false, disabled: true },
              ].map(tab => (
                <button key={tab.key} disabled={tab.disabled} style={{
                  padding: '6px 16px', borderRadius: 6, fontSize: 13, cursor: tab.disabled ? 'not-allowed' : 'pointer',
                  border: tab.active ? '1px solid #1677ff' : '1px solid #d9d9d9',
                  background: tab.active ? '#e6f4ff' : '#fff',
                  color: tab.active ? '#1677ff' : tab.disabled ? '#ccc' : '#666',
                }}>
                  {tab.label}
                </button>
              ))}
            </div>

            {/* Cookie input */}
            {error && (
              <p style={{ color: '#ff4d4f', fontSize: 13, margin: '0 0 12px' }}>{error}</p>
            )}
            <textarea
              value={cookie}
              onChange={e => { setCookie(e.target.value); setError(''); }}
              placeholder="粘贴闲鱼 Cookie..."
              rows={5}
              style={{
                width: '100%', padding: '10px 14px', border: `1px solid ${error ? '#ff4d4f' : '#d9d9d9'}`,
                borderRadius: 6, fontSize: 13, resize: 'vertical',
                fontFamily: 'monospace', boxSizing: 'border-box',
              }}
            />

            <div style={{ display: 'flex', gap: 12, justifyContent: 'flex-end', marginTop: 16 }}>
              {platforms.find(p => p.key === activeManage)?.connected && (
                <button onClick={handleDisconnect} style={{
                  padding: '8px 20px', borderRadius: 6, fontSize: 14, cursor: 'pointer',
                  background: '#fff', border: '1px solid #ff4d4f', color: '#ff4d4f',
                }}>
                  断开
                </button>
              )}
              <button onClick={() => setActiveManage(null)} style={{
                padding: '8px 20px', borderRadius: 6, fontSize: 14, cursor: 'pointer',
                background: '#fff', border: '1px solid #d9d9d9',
              }}>
                取消
              </button>
              <button onClick={handleSaveCookie} disabled={saving || !cookie.trim()} style={{
                padding: '8px 20px', borderRadius: 6, fontSize: 14, cursor: 'pointer',
                background: cookie.trim() ? '#1677ff' : '#d9d9d9', color: '#fff', border: 'none',
              }}>
                {saving ? '保存中...' : '保存'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}
