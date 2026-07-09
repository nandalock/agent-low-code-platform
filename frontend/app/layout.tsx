import type { Metadata } from 'next';
import Providers from './providers';

export const metadata: Metadata = { title: '低代码智能体平台' };

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body style={{ margin:0, fontFamily:"system-ui,-apple-system,'Segoe UI',sans-serif", background:'#F7F8FA', color:'#1D2129' }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}