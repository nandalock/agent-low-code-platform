'use client';

import { useState, useEffect } from 'react';
import { useRouter, usePathname } from 'next/navigation';
import Link from 'next/link';
import dynamic from 'next/dynamic';

const ChatPageInner = dynamic(() => import('./_components/ChatPageInner'), { ssr: false });

const MENU = [
  { label: '聊天', path: '/dashboard/chat' },
  { label: '知识库', path: '/dashboard/knowledge' },
  { label: 'Agent', path: '/dashboard/agents' },
  { label: '集成管理', path: '/dashboard/integrations' },
];

export default function DashboardLayout({ children }: { children: React.ReactNode }) {
  const router = useRouter();
  const pathname = usePathname();
  const [showUserMenu, setShowUserMenu] = useState(false);
  const isChat = pathname === '/dashboard/chat';
  const [chatMounted, setChatMounted] = useState(false);

  useEffect(() => {
    if (isChat && !chatMounted) setChatMounted(true);
  }, [isChat, chatMounted]);

  async function handleLogout() {
    await fetch('/api/logout', { method: 'POST' });
    router.push('/login');
  }

  return (
    <div style={{ display: 'flex', height: '100vh' }}>
      {/* Left Sidebar */}
      <div style={{
        width: 220, background: '#fff', borderRight: '1px solid #e8e8e8',
        display: 'flex', flexDirection: 'column', flexShrink: 0,
      }}>
        {/* Brand */}
        <div style={{
          padding: '16px 20px', fontSize: 20, fontWeight: 'bold',
          color: '#1677ff', borderBottom: '1px solid #f0f0f0',
        }}>
          智能客服
        </div>

        {/* Menu */}
        <nav style={{ flex: 1, padding: '12px 0', overflow: 'auto' }}>
          {MENU.map(item => (
            <Link key={item.path} href={item.path} style={{
              display: 'block', padding: '10px 20px',
              color: pathname.startsWith(item.path) ? '#1677ff' : '#333',
              background: pathname.startsWith(item.path) ? '#e6f4ff' : 'transparent',
              textDecoration: 'none', fontSize: 14,
            }}>
              {item.label}
            </Link>
          ))}
        </nav>

        {/* User */}
        <div style={{ borderTop: '1px solid #f0f0f0', position: 'relative' }}>
          <div onClick={() => setShowUserMenu(!showUserMenu)} style={{
            padding: '12px 20px', fontSize: 14, color: '#666', cursor: 'pointer',
          }}>
            👤 jk
          </div>
          {showUserMenu && (
            <div style={{
              position: 'absolute', bottom: 44, left: 12,
              background: '#fff', border: '1px solid #f0f0f0',
              borderRadius: 6, boxShadow: '0 2px 8px rgba(0,0,0,0.1)',
              width: 160,
            }}>
              <div style={{ padding: '10px 16px', fontSize: 13, color: '#999', borderBottom: '1px solid #f0f0f0' }}>
                管理员
              </div>
              <div onClick={handleLogout} style={{
                padding: '10px 16px', fontSize: 14, cursor: 'pointer', color: '#ff4d4f',
              }}>
                退出登录
              </div>
            </div>
          )}
        </div>
      </div>

      {/* Right Content */}
      {/* 非聊天页：正常渲染 children */}
      {!isChat && (
        <div style={{ flex: 1, background: '#f5f5f5', padding: 24, overflow: 'auto' }}>
          {children}
        </div>
      )}
      {/* 聊天页 KeepAlive：首次访问后始终保持挂载，切换时用 display 控制显隐 */}
      {chatMounted && (
        <div style={{ flex: 1, display: isChat ? undefined : 'none' }}>
          <ChatPageInner />
        </div>
      )}
    </div>
  );
}
