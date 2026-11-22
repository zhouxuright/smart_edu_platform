import { memo, useMemo } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import './MarkdownView.css';

/**
 * 流式输出时，行尾可能停在半个标记上（`*`、`##`、`` ` ``）。
 * 这里把尚未闭合的尾部标记摘掉，等后续 chunk 补齐再显示，
 * 避免用户看到一闪而过的 `**`。
 */
function trimDanglingMarkers(text) {
  let s = text.replace(/[*_`#>|~\-+\s]+$/, '');
  // 加粗标记成对出现；奇数说明最后一个还没闭合，先摘掉
  const bold = (s.match(/\*\*/g) || []).length;
  if (bold % 2 === 1) {
    const idx = s.lastIndexOf('**');
    s = s.slice(0, idx) + s.slice(idx + 2);
  }
  // 单星号同理（斜体 / 列表符号）
  const single = (s.match(/(^|[^*])\*(?!\*)/g) || []).length;
  if (single % 2 === 1) {
    const idx = s.lastIndexOf('*');
    if (idx >= 0) s = s.slice(0, idx) + s.slice(idx + 1);
  }
  return s;
}

const components = {
  a({ href, children, ...rest }) {
    const external = /^https?:\/\//i.test(href || '');
    return (
      <a
        href={href}
        target={external ? '_blank' : undefined}
        rel={external ? 'noopener noreferrer' : undefined}
        {...rest}
      >
        {children}
      </a>
    );
  },
  table({ children, ...rest }) {
    return (
      <div className="md-table-wrap">
        <table {...rest}>{children}</table>
      </div>
    );
  },
};

function MarkdownView({ content, streaming = false, className = '' }) {
  const source = useMemo(() => {
    const text = typeof content === 'string' ? content : '';
    return streaming ? trimDanglingMarkers(text) : text;
  }, [content, streaming]);

  if (!source.trim()) return null;

  return (
    <div className={`md-body${className ? ` ${className}` : ''}`}>
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components}>
        {source}
      </ReactMarkdown>
    </div>
  );
}

export default memo(MarkdownView);
