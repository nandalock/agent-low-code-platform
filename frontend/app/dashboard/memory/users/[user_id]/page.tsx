'use client';

import { useEffect, useState } from 'react';
import { useParams, useRouter } from 'next/navigation';
import { ChevronRight, User, Clock } from 'lucide-react';
import { T, S } from '@/app/theme';

const API = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';
const TENANT_ID = 1;

interface UserDetail {
  user_id: string;
  facts: string[];
  updated_at: string | null;
}

export default function UserDetailPage() {
  const { user_id } = useParams<{ user_id: string }>();
  const router = useRouter();
  const [profile, setProfile] = useState<UserDetail | null>(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/api/memory/users/${user_id}`, { headers: { 'X-Tenant-ID': String(TENANT_ID) } })
      .then(r => r.json())
      .then(d => setProfile(d))
      .finally(() => setLoading(false));
  }, [user_id]);

  return (
    <div>
      {/* Breadcrumb */}
      <div style={{ display:'flex', alignItems:'center', gap:S.sm, marginBottom:S.xl }}>
        <span onClick={() => router.push('/dashboard/memory')} style={{ fontSize:13, color:T.accent, cursor:'pointer' }}>
          智能客服记忆系统
        </span>
        <ChevronRight size={12} color={T.tertiary} />
        <span onClick={() => router.push('/dashboard/memory/users')} style={{ fontSize:13, color:T.accent, cursor:'pointer' }}>
          用户记忆总览
        </span>
        <ChevronRight size={12} color={T.tertiary} />
        <span style={{ fontSize:13, color:T.text, fontWeight:500 }}>{user_id}</span>
      </div>

      {loading && <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge }}>加载中...</div>}

      {!loading && profile && (
        <>
          {/* Header */}
          <div style={{ display:'flex', alignItems:'center', gap:S.md, marginBottom:S.xl }}>
            <div style={{ width:44, height:44, borderRadius:'50%', background:T.accentBg, display:'flex', alignItems:'center', justifyContent:'center' }}>
              <User size={20} color={T.accent} />
            </div>
            <div>
              <h2 style={{ margin:0, fontSize:18, fontWeight:600, color:T.text }}>{user_id}</h2>
              <div style={{ display:'flex', alignItems:'center', gap:S.xs, fontSize:11, color:T.tertiary, marginTop:2 }}>
                <Clock size={11} />
                {profile.updated_at ? new Date(profile.updated_at).toLocaleString('zh-CN') : '未知'}
              </div>
            </div>
            <div style={{ marginLeft:'auto', background:T.accentBg, borderRadius:20, padding:'4px 14px', fontSize:12, color:T.accent, fontWeight:500 }}>
              {profile.facts.length} 条记忆
            </div>
          </div>

          {/* Facts */}
          {profile.facts.length === 0 ? (
            <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge, padding:S.xxxl }}>
              <div style={{ fontSize:32, marginBottom:S.sm }}>📝</div>
              <div style={{ fontSize:14 }}>暂无记忆</div>
              <div style={{ fontSize:12, marginTop:S.xs }}>对话后系统会自动提取</div>
            </div>
          ) : (
            <div style={{ display:'flex', flexDirection:'column', gap:S.sm }}>
              {profile.facts.map((fact, i) => (
                <div key={i} style={{
                  background:T.surface, borderRadius:8, padding:`${S.md}px ${S.base}px`,
                  border:`1px solid ${T.border}`, fontSize:13, color:T.text,
                  lineHeight:1.6, display:'flex', alignItems:'flex-start', gap:S.sm,
                }}>
                  <span style={{ color:T.accent, fontWeight:600, flexShrink:0 }}>{i + 1}.</span>
                  <span>{fact}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {!loading && !profile && (
        <div style={{ textAlign:'center', color:T.tertiary, marginTop:S.huge }}>用户画像不存在</div>
      )}
    </div>
  );
}
