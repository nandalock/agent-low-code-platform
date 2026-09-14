'use client';

// Markdown —— 助手正文渲染（react-markdown + GFM + highlight.js）
//
// 改造前聊天区是 whiteSpace:pre-wrap 的纯文本，模型输出的标题/列表/表格/代码块
// 全都糊成一坨。排版规则统一收在 workspace.css 的 .ws-md（与右栏文件预览共用）。
//
// 代码块的取数走 pre 覆写而不是 code 覆写：react-markdown v10 起 code 组件不再
// 收到 inline 标志，靠 className 里有没有 language-* 判断块级会把无语言围栏判错；
// 而 pre 一定只包块级代码，从它的 code 子节点读 language 与原文最稳。

import { memo, useDeferredValue, useMemo, type ReactNode } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import hljs from 'highlight.js';

interface Props {
  text: string;
}

/** 代码块：hljs 高亮后注入。hljs 自身转义输入，dangerouslySetInnerHTML 安全。 */
function CodeBlock({ lang, raw }: { lang: string | null; raw: string }) {
  const html = useMemo(() => {
    try {
      if (lang && hljs.getLanguage(lang)) return hljs.highlight(raw, { language: lang }).value;
      return hljs.highlightAuto(raw).value;
    } catch {
      return raw.replace(/[&<>]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;' }[c] as string));
    }
  }, [lang, raw]);
  return <code dangerouslySetInnerHTML={{ __html: html }} />;
}

const COMPONENTS = {
  pre({ children }: { children?: ReactNode }) {
    const child = (Array.isArray(children) ? children[0] : children) as any;
    const className: string = child?.props?.className || '';
    const lang = /language-([\w-]+)/.exec(className)?.[1] ?? null;
    const raw = String(child?.props?.children ?? '').replace(/\n$/, '');
    return (
      <pre>
        <CodeBlock lang={lang} raw={raw} />
      </pre>
    );
  },
  // 行内代码：不加语言、不启用高亮
  code({ children, className, ...rest }: any) {
    if (/language-/.test(className || '')) return <code className={className} {...rest}>{children}</code>;
    return <code {...rest}>{children}</code>;
  },
  // 外链一律新窗口，避免把工作区页面顶掉
  a({ children, href, ...rest }: any) {
    return <a href={href} target="_blank" rel="noreferrer noopener" {...rest}>{children}</a>;
  },
};

function Markdown({ text }: Props) {
  // 流式时 text 每个 rAF 批次都变，而解析整篇 markdown 是实打实的开销：
  // useDeferredValue 让解析跑在低优先级上——解析跟不上时先维持上一帧的内容，
  // 而不是把每个 delta 都卡在渲染主线程上。
  const deferred = useDeferredValue(text);
  return (
    <div className="ws-md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={COMPONENTS as any}>{deferred}</ReactMarkdown>
    </div>
  );
}

// 历史消息正文恒不变，memo 掉可免去每次 token 到达时的全量重解析
export default memo(Markdown);
