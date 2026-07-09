'use client';

import { useEffect, useState } from 'react';
import { useRouter } from 'next/navigation';
import { ChevronRight, User } from 'lucide-react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

interface UserProfile {
  user_id: string;
  customer_name: string;
  facts_count: number;
  updated_at: string | null;
}

export default function UserListPage() {
  const router = useRouter();
  const [users, setUsers] = useState<UserProfile[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/memory/users`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } })
      .then(r => r.json())
      .then(d => setUsers(d))
      .finally(() => setLoading(false));
  }, []);

  return (
    <div>
      <div style={{ display:'flex', alignItems:'center', gap:S.sm, marginBottom:S.xl }}>
        <span onClick={() => router.push('/dashboard/memory')} style={{ fontSize:13, color:T.accent, cursor:'pointer' }}>
          智能客服记忆系统
        </span>
        <ChevronRight size={12} color={T.tertiary} />
        <span style={{ fontSize:13, color:T.text, fontWeight:500 }}>用户记忆总览</span>
      </div>

      <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>用户记忆总览</h2>
      <p style={{ fontSize:13, color:T.secondary, marginTop:S.xs, marginBottom:S.xl }}>
        {users.length} 位用户已自动提取画像
      </p>

      {loading && <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge }}>加载中...</div>}

      {!loading && users.length === 0 && (
        <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge, padding:S.xxxl }}>
          <div style={{ fontSize:40, marginBottom:S.base }}>🧠</div>
          <div style={{ fontSize:14 }}>暂无用户画像</div>
          <div style={{ fontSize:12, marginTop:S.xs }}>对话后系统会自动提取用户事实</div>
        </div>
      )}

      <div style={{ display:'grid', gridTemplateColumns:'repeat(auto-fill, minmax(300px, 1fr))', gap:S.base }}>
        {users.map(u => (
          <div key={u.user_id} onClick={() => router.push(`/dashboard/memory/users/${u.user_id}`)} style={{
            background:T.surface, borderRadius:10, padding:S.xl, cursor:'pointer',
            border:`1px solid ${T.border}`, transition:'box-shadow .15s',
          }} onMouseEnter={e => e.currentTarget.style.boxShadow='0 2px 12px rgba(0,0,0,0.06)'}
             onMouseLeave={e => e.currentTarget.style.boxShadow='none'}>
            <div style={{ display:'flex', alignItems:'center', gap:S.md }}>
              <div style={{ width:36, height:36, borderRadius:'50%', background:T.accentBg, display:'flex', alignItems:'center', justifyContent:'center' }}>
                <User size={16} color={T.accent} />
              </div>
              <div style={{ flex:1 }}>
                <div style={{ fontSize:14, fontWeight:500, color:T.text }}>{u.customer_name}</div>
                <div style={{ fontSize:11, color:T.tertiary, marginTop:2 }}>{u.user_id}</div>
              </div>
              <ChevronRight size={14} color={T.tertiary} />
            </div>
            <div style={{ display:'flex', gap:S.lg, marginTop:S.base }}>
              <div>
                <div style={{ fontSize:20, fontWeight:600, color:T.accent }}>{u.facts_count}</div>
                <div style={{ fontSize:11, color:T.tertiary }}>条记忆</div>
              </div>
              <div>
                <div style={{ fontSize:12, color:T.secondary }}>
                  {u.updated_at ? new Date(u.updated_at).toLocaleDateString('zh-CN') : '-'}
                </div>
                <div style={{ fontSize:11, color:T.tertiary }}>最近更新</div>
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
