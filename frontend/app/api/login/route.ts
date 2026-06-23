import { NextResponse } from 'next/server';

const API = process.env.BACKEND_URL || 'http://localhost:8000';

export async function POST(request: Request) {
  const body = await request.json();

  const res = await fetch(`${API}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });

  const data = await res.json();

  if (res.ok && data.ok) {
    const response = NextResponse.json({ ok: true });
    response.cookies.set('auth_token', data.token, {
      httpOnly: true,
      path: '/',
      maxAge: 60 * 60 * 24,  // 24 hours, matches JWT expiry
    });
    return response;
  }

  return NextResponse.json({ ok: false, error: data.error || '登录失败' }, { status: res.status });
}
