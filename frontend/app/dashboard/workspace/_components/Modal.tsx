'use client';

// Modal —— 工作区页的模态弹窗外壳（遮罩 + 面板 + 标题 + 关闭键 + footer）。
//
// 存在的理由不是「好看」，而是**生命周期独立于触发它的那一行**。项目行右键 →
// 移除 → 请求成功后那一行会卸载（本地立即摘掉，见 page.tsx），弹窗要是挂在行
// 组件里，就会跟着一起没了 —— 用户看到的是「点了确认，弹窗和行同时消失」，
// 失败时更糟：错误还没显示出来，承载它的组件已经卸载。所以弹窗一律由**行之上**
// 的那一层（WorkspaceSidebar）持有，见那里的 deleteTarget / renameTarget。
//
// 交互契约（第 5 步验收的第 3、7 条）：
//   - 未提交前 Cancel / Escape / 点遮罩 / 点 Close 都只是关掉，不执行任何动作。
//   - 提交中（busy）：确认与取消都 disabled，重复点击无效，**Escape 也关不掉**
//     —— 请求已经发出去了，这时候允许关窗只会让用户以为「撤销了」。

import { useEffect } from 'react';
import { X } from 'lucide-react';
import { W, R, WS } from '../theme';

interface Props {
  open: boolean;
  title: string;
  /** 标题下的一行说明（可省）。 */
  description?: string;
  /** 请求进行中：取消 / 关闭 / Escape 全部失效。 */
  busy?: boolean;
  onClose: () => void;
  children?: React.ReactNode;
  footer: React.ReactNode;
}

export default function Modal({
  open, title, description, busy = false, onClose, children, footer,
}: Props) {
  useEffect(() => {
    if (!open) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' && !busy) onClose();
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open, busy, onClose]);

  if (!open) return null;
  // busy 期间的关闭一律走这里：按钮 disabled 只挡鼠标，遮罩和关闭键还得自己挡
  const close = () => { if (!busy) onClose(); };

  return (
    <>
      {/* 遮罩层与面板是兄弟：面板因此不会吃到遮罩的点击 */}
      <div onClick={close} style={{ position: 'fixed', inset: 0, zIndex: 100, background: W.scrim }} />
      <div
        role="dialog" aria-modal="true" aria-label={title}
        style={{
          position: 'fixed', left: '50%', top: '50%', transform: 'translate(-50%, -50%)',
          zIndex: 101, width: 420, maxWidth: 'calc(100vw - 32px)',
          background: W.panel, borderRadius: R.md, boxShadow: W.elevProminent,
          padding: WS.lg, fontFamily: W.font, color: W.text,
        }}>
        <div style={{ display: 'flex', alignItems: 'flex-start', gap: WS.sm }}>
          <div style={{ flex: 1, minWidth: 0, fontSize: 15, lineHeight: '22px', fontWeight: 600 }}>
            {title}
          </div>
          <button className="ws-btn ws-round" onClick={close} disabled={busy} title="关闭"
            style={{ padding: 3, color: W.dimmed, display: 'flex', flexShrink: 0, borderRadius: R.xs }}>
            <X size={15} />
          </button>
        </div>

        {description && (
          <div style={{ marginTop: WS.sm, fontSize: 13, lineHeight: '20px', color: W.tertiary }}>
            {description}
          </div>
        )}
        {children}

        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: WS.sm, marginTop: WS.lg }}>
          {footer}
        </div>
      </div>
    </>
  );
}
