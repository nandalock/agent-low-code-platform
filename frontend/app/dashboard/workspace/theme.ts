// 工作区深色 tokens —— 对齐 DeepSeek Harness 深色主题（body[data-ds-dark-theme]）。
//
// 与参考实现的对应关系（色板名取自 harness design-platform.css）：
//   表面阶梯  bluish-950 → 900 → 875 → 850 → 800（带蓝调的灰黑，不是纯黑）
//   文字      近白 / 三级冷灰
//   描边      半透明白的发丝线（0.5px），不是实线色
//   主按钮    黑白反转（#F9FAFB 底 + 深色字），品牌蓝只做链接与信息按钮
//   交互态    半透明蒙层，不换边框、不加阴影
//
// W 是**单一事实源**：内联样式直接吃这些值，workspace.css 吃由它生成的
// CSS 变量（见下方 wsThemeCss），两边不会漂移。
export const W = {
  // ── 表面阶梯 ──
  bg:          '#151517',                    // 主区（对话列）底
  panel:       '#1B1B1C',                    // 左右侧栏
  surface:     '#232324',                    // 卡片 / 输入框 / 气泡
  surfaceHi:   '#2C2C2E',                    // 浮层 / 悬浮卡 / 用户气泡
  overlay:     '#353638',                    // 最高层（Toast / HoverCard）

  // ── 文字 ──
  text:        '#F9FAFB',                    // 主
  secondary:   '#CFD3D6',                    // 次
  tertiary:    '#ADB2B8',                    // 三
  dimmed:      '#81858C',                    // 说明 / 占位

  // ── 描边（发丝线，半透明白）──
  borderSoft:  'rgba(255,255,255,0.06)',     // l1：行分隔、内部描边
  border:      'rgba(255,255,255,0.12)',     // l2：卡片描边、面板分隔
  borderStrong:'rgba(255,255,255,0.16)',     // l3：输入框、按钮描边

  // ── 交互蒙层（hover / active 一律用它，不换边框）──
  hover:       'rgba(255,255,255,0.08)',
  active:      'rgba(255,255,255,0.14)',

  // ── 强调 ──
  accent:      '#679EFE',                    // 链接 / 进行中 / 发送键（深色用 deepseek-400）
  accentHover: '#5686FE',
  primary:     '#F9FAFB',                    // 主按钮填充（黑白反转 CTA）
  primaryHover:'#EBEEF2',
  primaryText: '#0F1115',                    // 主按钮文字

  // ── 状态 ──
  success:     '#22C55E',
  danger:      '#F25A5A',
  dangerSoft:  'rgba(242,90,90,0.14)',       // 危险动作的 hover 蒙层（移除项目）
  warning:     '#F59E0B',
  ongoing:     '#5686FE',

  // ── 遮罩（模态弹窗压住整页的那层）──
  scrim:       'rgba(0,0,0,0.5)',

  // ── 阴影（发丝描边画在 box-shadow 首层，配 border:0 不占布局）──
  elevSoft:     '0 0 0 0.5px rgba(255,255,255,0.10), 0 2px 8px 0 rgba(0,0,0,0.35)',
  elevPanel:    '0 0 0 0.5px rgba(255,255,255,0.12), 0 8px 28px 0 rgba(0,0,0,0.50)',
  elevProminent:'0 0 0 0.5px rgba(255,255,255,0.14), 0 12px 40px 0 rgba(0,0,0,0.55)',

  // ── 字体 / 动效 ──
  // 代码栈不加裸 monospace 尾巴：Windows 上 CJK 会回退成宋体
  mono: "'SF Mono', 'JetBrains Mono', 'Fira Code', Consolas, 'Liberation Mono', Menlo, Courier, 'PingFang SC', 'Microsoft YaHei', monospace",
  font: "-apple-system, BlinkMacSystemFont, 'Segoe UI', 'PingFang SC', 'Hiragino Sans GB', 'Microsoft YaHei', 'Helvetica Neue', Helvetica, Arial, sans-serif",
  ease: 'cubic-bezier(0.4, 0, 0.2, 1)',

  // ── 滚动条 ──
  scrollbar:      'rgba(255,255,255,0.14)',
  scrollbarHover: 'rgba(255,255,255,0.24)',
} as const;

/** 圆角：胶囊体系（按钮/胶囊 = 高的一半），卡片 12，输入卡 22 */
export const R = { xs: 6, sm: 8, md: 12, lg: 16, xl: 22, full: 999 } as const;

// 间距沿用平台 S 的节奏（4/8/12/16/20/24...）
export const WS = { xs: 4, sm: 8, md: 12, base: 16, lg: 20, xl: 24, xxl: 32, huge: 64 } as const;

// ── 对话内容轴 ──
// 参考实现：消息列 clamp(680, 列宽×0.64, 920) 居中，输入卡比消息列宽 32。
// 这 32px 的差靠「同轴容器 + transcript 内缩 16」实现，见 WorkspaceChat 的注释。
export const CHAT_COLUMN = 'clamp(680px, 64%, 920px)';

/** W 的 camelCase key → CSS 变量名（--ws-surface-hi 这种） */
function cssVarName(key: string): string {
  return `--ws-${key.replace(/[A-Z]/g, m => '-' + m.toLowerCase())}`;
}

/**
 * 从 W 生成的 CSS 变量块，挂在 .ws-root 上（只作用于工作区子树，不污染
 * dashboard 其余浅色页）。page.tsx 用 dangerouslySetInnerHTML 注入一次——
 * 直接写 <style>{str}</style> 会踩 hydration：SSR 端转义文本子节点里的引号，
 * 客户端不转义，两边文本对不上直接崩页（FilePreviewPanel 有同样注释）。
 */
export const wsThemeCss = `.ws-root{${Object.entries(W)
  .map(([k, v]) => `${cssVarName(k)}: ${v};`)
  .join('')}}`;
