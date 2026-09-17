'use client';

// Composer —— 底部输入卡（参考实现 InputBar 的形态）
//
// r22 胶囊、不画实线边框：描边藏在 elevation 阴影的第一层 0.5px 发丝里，
// 这样卡片「浮」在消息上而不是被框住。整个卡片就是拖拽落区。
//
// 三档沙箱权限原本是原生 <select>（系统控件，在深色页里是块白斑），改造为
// 28px 胶囊 chip + 浮层菜单，与参考实现的 PermissionSelect 对齐。
//
// 模型选择：占位（后端无每会话模型切换，禁用态显示当前模型名）。
// 停止生成：发送中显示为可点的方块键 —— 前端 abort SSE fetch → 服务端连接断开 →
// AgentLoop 收到取消信号，在下一个检查点收口（stop_reason=cancelled），
// 已产出的正文保留。不需要额外的取消端点，断开本身就是取消。

import { useRef, useState } from 'react';
import { Plus, ArrowUp, Square, Loader2, ShieldCheck, ChevronDown, Check } from 'lucide-react';
import { W, R, WS } from '../theme';
import { SANDBOX_MODE_LABELS } from './useWorkspace';

interface Props {
  disabled: boolean;          // 无会话 / 发送中
  sending: boolean;
  uploading: boolean;
  uploadError: string | null;
  onSend: (text: string) => void;
  onStop: () => void;
  onUpload: (files: File[]) => void;
  sandboxMode: string;        // effective
  onModeChange: (mode: string) => void;
}

const MODES = ['read-only', 'workspace-write', 'danger-full-access'];

// 每档的一句话说明：光看名字用户没法判断自己要不要放宽权限
const MODE_HINTS: Record<string, string> = {
  'read-only': '只能读文件，任何写入都会被拒',
  'workspace-write': '可在会话工作目录内读写',
  'danger-full-access': '不限制，可读写挂载进来的任意目录',
};

export default function Composer({ disabled, sending, uploading, uploadError, onSend, onStop, onUpload, sandboxMode, onModeChange }: Props) {
  const [text, setText] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);
  const [modeOpen, setModeOpen] = useState(false);

  function submit() {
    const t = text.trim();
    if (!t || disabled) return;
    onSend(t);
    setText('');
  }

  function pickFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    onUpload(Array.from(files));
  }

  const canSend = !!text.trim() && !disabled;

  return (
    <div style={{ fontFamily: W.font }}>
      {uploadError && (
        <div style={{ fontSize: 12, color: W.danger, marginBottom: WS.sm }}>上传失败：{uploadError}</div>
      )}

      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); pickFiles(e.dataTransfer.files); }}
        style={{
          display: 'flex', flexDirection: 'column',
          borderRadius: R.xl, background: W.surfaceHi,
          // dragging 是唯一打破「无边框」的时刻：落区需要明确反馈，这时用真边框
          boxShadow: dragging ? `0 0 0 1.5px ${W.accent}` : W.elevSoft,
          transition: 'box-shadow .12s ease',
          paddingTop: WS.sm,
        }}>

        {/* 文本区 */}
        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
          placeholder={disabled ? '从左侧选择或新建一个会话开始' : '输入消息，Enter 发送，Shift+Enter 换行'}
          rows={Math.min(14, Math.max(1, text.split('\n').length))}
          className="ws-round"
          style={{
            background: 'transparent', border: 'none', outline: 'none', resize: 'none',
            color: W.text, fontSize: 14, lineHeight: '24px', fontFamily: 'inherit',
            maxHeight: 336, padding: '4px 12px 0 14px',
          }} />

        {/* 工具栏 */}
        <div style={{ display: 'flex', alignItems: 'center', gap: WS.md, padding: '2px 8px 6px 8px' }}>
          {/* 附件：文件选择 + 拖拽落区就是整张卡 */}
          <input ref={fileRef} type="file" multiple hidden onChange={e => { pickFiles(e.target.files); e.target.value = ''; }} />
          <button onClick={() => fileRef.current?.click()} title="上传文件（保存到会话工作区 .dsh-drops/）"
            className="ws-btn-solid ws-round"
            style={circleBtn}>
            {uploading ? <Loader2 size={15} className="ws-spin" /> : <Plus size={16} />}
          </button>

          {/* 沙箱权限：胶囊 chip + 浮层菜单 */}
          <div style={{ position: 'relative' }}>
            <button onClick={() => !disabled && setModeOpen(v => !v)} disabled={disabled}
              title={disabled ? '选择会话后可切换沙箱模式' : '会话沙箱权限模式'}
              className="ws-btn ws-round"
              style={{
                height: 28, padding: '0 10px', borderRadius: 24,
                color: disabled ? W.dimmed : W.secondary,
                fontSize: 13, fontFamily: 'inherit', display: 'flex', alignItems: 'center', gap: 6,
              }}>
              <ShieldCheck size={13} />
              {SANDBOX_MODE_LABELS[sandboxMode] || sandboxMode}
              <ChevronDown size={12} style={{ opacity: 0.7 }} />
            </button>
            {modeOpen && (
              <>
                <div onClick={() => setModeOpen(false)} style={{ position: 'fixed', inset: 0, zIndex: 40 }} />
                <div style={{
                  position: 'absolute', bottom: 'calc(100% + 8px)', left: 0, zIndex: 50, width: 300,
                  // 浮层用 overlay（比输入卡的 surfaceHi 再亮一档），否则「浮」不起来
                  background: W.overlay, borderRadius: R.md, boxShadow: W.elevProminent, padding: 4,
                }}>
                  {MODES.map(m => (
                    <button key={m} onClick={() => { onModeChange(m); setModeOpen(false); }}
                      className="ws-btn"
                      style={{
                        width: '100%', display: 'flex', alignItems: 'flex-start', gap: 8,
                        padding: '8px 10px', borderRadius: R.sm,
                        color: W.text, fontSize: 13, fontFamily: 'inherit', textAlign: 'left',
                      }}>
                      <span style={{ width: 14, flexShrink: 0, paddingTop: 3, color: W.accent }}>
                        {m === sandboxMode && <Check size={13} />}
                      </span>
                      <span style={{ flex: 1, minWidth: 0 }}>
                        <span style={{ display: 'block', lineHeight: '20px' }}>{SANDBOX_MODE_LABELS[m] || m}</span>
                        <span style={{ display: 'block', fontSize: 12, lineHeight: '18px', color: W.dimmed }}>{MODE_HINTS[m]}</span>
                      </span>
                    </button>
                  ))}
                </div>
              </>
            )}
          </div>

          <div style={{ flex: 1 }} />

          {/* 模型：后端待支持，只报当前值 */}
          <span title="模型选择（后端待支持）" style={{ fontSize: 12, color: W.dimmed, whiteSpace: 'nowrap' }}>
            DeepSeek-V4.1-Flash
          </span>

          {sending && (
            /* 危险动作配色（方块 = 停止），与发送键同形以便原地切换不跳动 */
            <button onClick={onStop} title="停止生成"
              className="ws-btn-solid ws-round"
              style={{ ...circleBtn, color: W.danger }}>
              <Square size={12} />
            </button>
          )}

          {/* 背景/禁用态交给 .ws-send：行内 background 会压死 :hover */}
          <button onClick={submit} disabled={!canSend}
            title="发送（Enter）"
            className="ws-send ws-round"
            style={{
              width: 34, height: 34, borderRadius: 999, flexShrink: 0,
              color: canSend ? '#fff' : W.dimmed,
              display: 'flex', alignItems: 'center', justifyContent: 'center',
            }}>
            <ArrowUp size={17} />
          </button>
        </div>
      </div>

      <div style={{ fontSize: 12, lineHeight: '18px', color: W.dimmed, marginTop: WS.sm, paddingLeft: WS.xs }}>
        上传的文件保存在会话工作区 .dsh-drops/，模型可直接读取
      </div>
    </div>
  );
}

// 背景由 .ws-btn-solid 持有（见 workspace.css 的说明）
const circleBtn: React.CSSProperties = {
  width: 28, height: 28, borderRadius: 999, flexShrink: 0,
  color: W.secondary,
  display: 'flex', alignItems: 'center', justifyContent: 'center',
};
