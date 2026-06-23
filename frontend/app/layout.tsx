import type { Metadata } from 'next';
import Providers from './providers';

export const metadata: Metadata = {
  title: '智能客服',
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN">
      <body style={{ margin: 0, fontFamily: 'system-ui, sans-serif' }}>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
