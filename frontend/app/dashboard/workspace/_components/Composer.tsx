'use client';

// Composer —— 底部输入条（对齐 DSH：悬浮圆角条 + 附件 + 模型/模式选择 + 圆形发送）
//
// 模型选择：占位（后端无每会话模型切换，禁用态显示当前模型名）。
// 模式选择：真实接口 —— GET/POST /api/agents/sessions/{sid}/sandbox_mode，
//           read-only / workspace-write / danger-full-access 三档，即 DSH 的三种权限模式。
// 停止生成：占位（后端 run cancel 待补），发送中显示为禁用按钮。

import { useRef, useState } from 'react';
import { Plus, ArrowUp, Square, Loader2 } from 'lucide-react';
import { W, WS } from '../theme';
import { SANDBOX_MODE_LABELS } from './useWorkspace';

interface Props {
  disabled: boolean;          // 无会话 / 发送中
  sending: boolean;
  uploading: boolean;
  uploadError: string | null;
  onSend: (text: string) => void;
  onUpload: (files: File[]) => void;
  sandboxMode: string;        // effective
  onModeChange: (mode: string) => void;
}

const MODES = ['read-only', 'workspace-write', 'danger-full-access'];

export default function Composer({ disabled, sending, uploading, uploadError, onSend, onUpload, sandboxMode, onModeChange }: Props) {
  const [text, setText] = useState('');
  const fileRef = useRef<HTMLInputElement>(null);
  const [dragging, setDragging] = useState(false);

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
    <div style={{ padding: `0 ${WS.xl}px ${WS.base}px`, fontFamily: W.font }}>
      {uploadError && <div style={{ fontSize: 12, color: W.danger, marginBottom: WS.sm }}>上传失败：{uploadError}</div>}
      <div
        onDragOver={e => { e.preventDefault(); setDragging(true); }}
        onDragLeave={() => setDragging(false)}
        onDrop={e => { e.preventDefault(); setDragging(false); pickFiles(e.dataTransfer.files); }}
        style={{
          display: 'flex', alignItems: 'flex-end', gap: WS.sm, padding: `${WS.sm}px ${WS.sm}px ${WS.sm}px ${WS.base}px`,
          borderRadius: 14, background: W.surface, border: dragging ? `1px solid ${W.accent}` : `1px solid ${W.border}`,
          transition: 'border-color .12s',
        }}>
        {/* 附件：文件选择 + 拖拽落区就是整个 composer */}
        <input ref={fileRef} type="file" multiple hidden onChange={e => { pickFiles(e.target.files); e.target.value = ''; }} />
        <button onClick={() => fileRef.current?.click()} title="上传文件（保存到会话工作区 .dsh-drops/）"
          style={{
            width: 30, height: 30, borderRadius: '50%', flexShrink: 0, border: `1px solid ${W.border}`,
            background: 'transparent', color: W.secondary, cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 2,
          }}>
          {uploading ? <Loader2 size={15} className="ws-spin" /> : <Plus size={16} />}
        </button>
        <style>{`@keyframes wsSpin{to{transform:rotate(360deg)}} .ws-spin{animation:wsSpin 1s linear infinite}`}</style>

        <textarea
          value={text}
          onChange={e => setText(e.target.value)}
          onKeyDown={e => {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit(); }
          }}
          placeholder={disabled ? '从左侧选择或新建一个会话开始' : '输入消息，Enter 发送，Shift+Enter 换行'}
          rows={Math.min(6, Math.max(1, text.split('\n').length))}
          style={{
            flex: 1, minWidth: 0, background: 'transparent', border: 'none', outline: 'none',
            color: W.text, fontSize: 14, lineHeight: 1.55, resize: 'none', fontFamily: 'inherit',
            maxHeight: 160, padding: '6px 0',
          }} />

        {/* 发送 / 停止（占位） */}
        <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, marginBottom: 2 }}>
          {sending && (
            <button title="停止生成（后端待支持）" disabled
              style={{ width: 30, height: 30, borderRadius: '50%', border: `1px solid ${W.border}`, background: 'transparent', color: W.tertiary, cursor: 'not-allowed', display: 'flex', alignItems: 'center', justifyContent: 'center' }}>
              <Square size={12} />
            </button>
          )}
          <button onClick={submit} disabled={!canSend}
            style={{
              width: 30, height: 30, borderRadius: '50%', border: 'none', cursor: canSend ? 'pointer' : 'not-allowed',
              background: canSend ? W.accent : W.surface, color: canSend ? '#fff' : W.tertiary,
              display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'background .12s',
            }}>
            <ArrowUp size={16} />
          </button>
        </div>
      </div>

      {/* 底部选项行：模型（占位）+ 沙箱模式（真实） */}
      <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, marginTop: WS.sm, padding: `0 ${WS.xs}px` }}>
        <button title="模型选择（后端待支持）" disabled
          style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 10px', borderRadius: 8, border: 'none', background: 'transparent', color: W.tertiary, fontSize: 12, cursor: 'not-allowed', fontFamily: 'inherit' }}>
          DeepSeek-V4.1-Flash
        </button>
        <select value={sandboxMode} onChange={e => onModeChange(e.target.value)} disabled={disabled}
          title={disabled ? '选择会话后可切换沙箱模式' : '会话沙箱权限模式（只读 / 标准 / 完全访问）'}
          style={{
            padding: '4px 10px', borderRadius: 8, border: `1px solid ${W.border}`, background: W.surface,
            color: W.text, fontSize: 12, cursor: disabled ? 'not-allowed' : 'pointer', outline: 'none',
            fontFamily: 'inherit', opacity: disabled ? 0.6 : 1,
          }}>
          {MODES.map(m => (
            <option key={m} value={m}>{SANDBOX_MODE_LABELS[m] || m}</option>
          ))}
        </select>
        <span style={{ marginLeft: 'auto', fontSize: 11, color: W.tertiary }}>
          上传的文件保存在会话工作区 .dsh-drops/，模型可直接读取
        </span>
      </div>
    </div>
  );
}
