'use client';

// FilePreviewPanel —— 右栏「预览」：按内容类型分发渲染
//
// 预览接口需要 X-Tenant-ID 头，<iframe>/<img> 直接指 URL 发不出自定义头，
// 所以一律 fetch → Blob → objectURL。分发规则（对应后端魔数判型）：
//   application/pdf → iframe（浏览器内置阅读器）
//   image/*         → img
//   text/*          → .md → react-markdown；代码扩展名 → highlight.js；其余 pre
// 二进制 / 超限（413/415）→ 错误提示 + 下载兜底。SVG 后端刻意不作为图片
// 内联（XSS），这里同样不特殊对待 —— 它只会落到文本分支当源码显示。

import { useEffect, useState } from 'react';
import { Download, FileQuestion } from 'lucide-react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import hljs from 'highlight.js';
import { W, WS } from '../theme';
import { type FileEntry, fetchPreview } from './useWorkspace';

interface Props {
  sessionId: string | null;
  path: string | null;
  entry: FileEntry | null;
}

const CODE_EXTS = ['js', 'ts', 'tsx', 'jsx', 'py', 'java', 'go', 'rs', 'c', 'cpp', 'css', 'html', 'sh', 'sql', 'json', 'yaml', 'yml', 'toml', 'xml'];
const MD_EXTS = ['md', 'markdown'];

function hljsLang(name: string): string {
  const ext = name.split('.').pop()?.toLowerCase() || '';
  const map: Record<string, string> = {
    js: 'javascript', jsx: 'javascript', ts: 'typescript', tsx: 'typescript', py: 'python',
    sh: 'bash', yml: 'yaml', cpp: 'cpp', rs: 'rust', html: 'xml',
  };
  return map[ext] || ext;
}

export default function FilePreviewPanel({ sessionId, path, entry }: Props) {
  const name = entry?.name ?? '';   // TS 收窄入口：kind 非空 ⇒ entry 非空，但编译器不认这条推理
  const entrySize = entry?.size ?? 0;
  const [url, setUrl] = useState<string | null>(null);
  const [kind, setKind] = useState<'pdf' | 'image' | 'text' | 'markdown' | 'code' | null>(null);
  const [text, setText] = useState('');
  const [html, setHtml] = useState('');
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState('');

  // 依赖只取 entry?.name：文件树每轮对话后整体重载，entry 对象每次都是新的，
  // 按对象比较会让预览在「同一文件没变」时也重新拉取、闪一下。
  useEffect(() => {
    if (!sessionId || !path || !entry || entry.is_dir) { setUrl(null); setKind(null); setText(''); setErr(''); return; }
    let cancelled = false;
    setLoading(true); setErr(''); setUrl(null); setKind(null); setText(''); setHtml('');
    (async () => {
      try {
        const blob = await fetchPreview(sessionId, path);
        if (cancelled) return;
        if (blob.type === 'application/pdf') {
          setKind('pdf'); setUrl(URL.createObjectURL(blob));
        } else if (blob.type.startsWith('image/')) {
          setKind('image'); setUrl(URL.createObjectURL(blob));
        } else {
          const t = await blob.text();
          if (cancelled) return;
          const lower = entry.name.toLowerCase();
          if (MD_EXTS.some(e => lower.endsWith('.' + e))) { setKind('markdown'); setText(t); }
          else if (CODE_EXTS.some(e => lower.endsWith('.' + e))) {
            setKind('code');
            setHtml(hljs.highlight(t, { language: hljsLang(entry.name) }).value);
          } else { setKind('text'); setText(t); }
        }
      } catch (e: any) {
        if (!cancelled) setErr(e?.message || String(e));
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => { cancelled = true; };
  }, [sessionId, path, entry?.name]);   // eslint-disable-line react-hooks/exhaustive-deps

  // 卸载/切换时回收 objectURL
  useEffect(() => () => { if (url) URL.revokeObjectURL(url); }, [url]);

  async function handleDownload() {
    if (!sessionId || !path) return;
    try {
      const blob = await fetchPreview(sessionId, path);
      const a = document.createElement('a');
      a.href = URL.createObjectURL(blob);
      a.download = name || 'file';
      a.click();
      setTimeout(() => URL.revokeObjectURL(a.href), 10_000);
    } catch { /* 下载失败静默（预览区已有错误提示路径） */ }
  }

  const empty = !entry;
  const isDir = !!entry?.is_dir;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100%', minHeight: 0, fontFamily: W.font }}>
      {/* 文件头：名称 + 大小 + 下载 */}
      <div style={{ display: 'flex', alignItems: 'center', gap: WS.sm, padding: `${WS.sm}px ${WS.md}px`, borderBottom: `0.5px solid ${W.borderSoft}`, minHeight: 44 }}>
        <span style={{ flex: 1, minWidth: 0, fontSize: 13, color: W.text, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {empty ? '预览' : entry.name}
        </span>
        {!empty && !isDir && entrySize > 0 && <span style={{ fontSize: 11, color: W.tertiary, flexShrink: 0 }}>{entrySize > 1024 ? `${(entrySize / 1024).toFixed(1)} KB` : `${entrySize} B`}</span>}
        {!empty && !isDir && (
          <button onClick={handleDownload} title="下载"
            className="ws-btn ws-round"
            style={{ color: W.secondary, padding: 2, display: 'flex' }}>
            <Download size={14} />
          </button>
        )}
      </div>

      <div style={{ flex: 1, minHeight: 0, overflow: 'auto', position: 'relative' }}>
        {loading && <div style={{ padding: WS.xl, textAlign: 'center', color: W.dimmed, fontSize: 12 }}>加载预览...</div>}
        {!loading && err && (
          <div style={{ padding: WS.xl, textAlign: 'center' }}>
            <FileQuestion size={28} color={W.dimmed} style={{ marginBottom: WS.sm }} />
            <div style={{ color: W.secondary, fontSize: 13, marginBottom: WS.sm }}>无法预览：{err}</div>
            {!isDir && <button onClick={handleDownload} className="ws-btn-primary ws-round" style={{ height: 32, padding: '0 16px', borderRadius: 999, fontSize: 13, fontWeight: 500, fontFamily: 'inherit' }}>下载查看</button>}
          </div>
        )}
        {!loading && !err && kind === 'pdf' && url && (
          <iframe src={url} title={name} style={{ width: '100%', height: '100%', border: 'none', background: W.surface }} />
        )}
        {!loading && !err && kind === 'image' && url && (
          <img src={url} alt={name} style={{ maxWidth: '100%', display: 'block' }} />
        )}
        {/* markdown 与代码的排版/配色统一由 workspace.css 的 .ws-md / .ws-code 提供 */}
        {!loading && !err && kind === 'markdown' && (
          <div className="ws-md" style={{ padding: WS.base }}>
            <ReactMarkdown remarkPlugins={[remarkGfm]}>{text}</ReactMarkdown>
          </div>
        )}
        {!loading && !err && kind === 'code' && (
          <pre className="ws-code" style={{ margin: 0, padding: WS.base, fontSize: 12, lineHeight: '19px', fontFamily: W.mono, color: W.secondary, background: W.bg, whiteSpace: 'pre', overflowX: 'auto' }}
            dangerouslySetInnerHTML={{ __html: html }} />
        )}
        {!loading && !err && kind === 'text' && (
          <pre style={{ margin: 0, padding: WS.base, fontSize: 12.5, lineHeight: '20px', fontFamily: W.font, color: W.text, whiteSpace: 'pre-wrap', wordBreak: 'break-word' }}>{text}</pre>
        )}
        {!loading && !err && empty && (
          <div style={{ padding: WS.xl, textAlign: 'center', color: W.dimmed, fontSize: 13, whiteSpace: 'pre-line' }}>
            {'在左侧文件树选择文件进行预览\n支持 PDF / 图片 / Markdown / 代码 / 文本'}
          </div>
        )}
        {!loading && !err && isDir && (
          <div style={{ padding: WS.xl, textAlign: 'center', color: W.dimmed, fontSize: 13 }}>这是一个目录</div>
        )}
      </div>
    </div>
  );
}
