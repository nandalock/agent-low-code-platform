// DSH 风格深色 tokens —— 工作区页自管一套深色主题，与平台其余浅色页解耦。
// 对齐 DeepSeek Harness web UI 的观感：三栏靠背景色差分栏、无边框分隔、
// 深蓝黑底 + 冷灰文字。
export const W = {
  bg:        '#0B0E14',   // 主区（对话列）背景
  panel:     '#11151D',   // 左右侧栏背景
  surface:   '#161B26',   // 卡片 / 输入框 / 气泡
  hover:     '#1B2230',   // 列表项悬浮
  active:    '#1F2735',   // 选中项
  border:    '#232B3A',   // 细边框（卡片内）
  text:      '#E7EAF0',
  secondary: '#8B93A5',
  tertiary:  '#5B6472',
  accent:    '#4D6BFE',   // DSH 蓝
  accentHover:'#3D5AF0',
  success:   '#3FB950',
  danger:    '#F85149',
  warning:   '#D29922',
  mono:      "'SFMono-Regular', 'Cascadia Code', Consolas, monospace",
  font:      "system-ui, -apple-system, 'Segoe UI', 'PingFang SC', 'Microsoft YaHei', sans-serif",
} as const;

// 间距沿用平台 S 的节奏（4/8/12/16/20/24...）
export const WS = { xs: 4, sm: 8, md: 12, base: 16, lg: 20, xl: 24, xxl: 32, huge: 64 } as const;
