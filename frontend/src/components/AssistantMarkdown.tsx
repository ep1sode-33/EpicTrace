import { Fragment, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";

import { type Citation } from "@/lib/api";
import { cn } from "@/lib/utils";

/** 流式途中可能出现未闭合的 ``` 代码围栏,会把后续正文误当代码、来回闪。
 * 渲染前若 ``` 数为奇数,临时补一个闭合围栏(只影响渲染,不改原始内容)。 */
function balanceFences(md: string): string {
  const fences = (md.match(/^```/gm) ?? []).length;
  return fences % 2 === 1 ? `${md}\n\`\`\`` : md;
}

// 完整的引用标记 [n](n 为 1+ 位数字)。流式途中可能出现「半截」如 `[` 或 `[1`(右括号未到),
// 这类不完整片段不匹配,会以普通文本静默渲染,等右括号到达后整体再被切成 chip——不崩、不闪。
const CITE_RE = /\[(\d+)\]/g;

// hast 节点的极小子集(只用到这几个字段;避免硬依赖 unist/hast 包的运行时,仅按结构访问)。
interface HastText {
  type: "text";
  value: string;
}
interface HastElement {
  type: "element";
  tagName: string;
  properties?: Record<string, unknown>;
  children: HastNode[];
}
type HastNode = HastText | HastElement | { type: string; children?: HastNode[] };

/**
 * rehype(hast)阶段的小插件:把所有文本节点里出现的完整 `[n]` 切出来,替换为 `<data value="n">`
 * 元素;其余文本原样保留。放在 hast 阶段做,意味着它对所有 Markdown 结构(段落 / 列表项 /
 * 表格单元格 / 引用块……)一视同仁地生效,且不与 Markdown 的链接语法(`[text](url)` /
 * `[text][ref]`)冲突——纯 `[n]` 后面没有 `(...)`/`[...]`,remark 本就当普通文本。
 *
 * `<data>` 是合法的行内 HTML 元素,GFM/Markdown 自身从不产出它,故可安全用作引用占位,
 * 在下方 components 里据 tagName 接管为真正的引用 chip。手写遍历(不引 unist-util-visit),
 * 只依赖结构,稳。
 */
function rehypeCitations() {
  const walk = (node: HastNode) => {
    const children = (node as HastElement).children;
    if (!Array.isArray(children)) return;
    for (let i = 0; i < children.length; i++) {
      const child = children[i];
      if (child.type === "text") {
        const value = (child as HastText).value;
        CITE_RE.lastIndex = 0;
        if (!CITE_RE.test(value)) continue;
        CITE_RE.lastIndex = 0;
        const replacement: HastNode[] = [];
        let last = 0;
        for (const m of value.matchAll(CITE_RE)) {
          const idx = m.index ?? 0;
          if (idx > last) replacement.push({ type: "text", value: value.slice(last, idx) });
          replacement.push({
            type: "element",
            tagName: "data",
            properties: { value: m[1] },
            children: [],
          });
          last = idx + m[0].length;
        }
        if (last < value.length) replacement.push({ type: "text", value: value.slice(last) });
        children.splice(i, 1, ...replacement);
        i += replacement.length - 1; // 跳过刚插入的节点
      } else {
        walk(child);
      }
    }
  };
  return (tree: HastNode) => {
    walk(tree);
  };
}

const REMARK_PLUGINS = [remarkGfm];
const REHYPE_PLUGINS = [rehypeCitations];

/**
 * 助手回答的 Markdown 渲染:GFM(粗体 / 列表 / 表格 / 代码 / 标题 / 链接),
 * 内联 `[n]` 仍渲染为可点击的引用 chip(打开来源查看器)。排版克制、贴合全应用的安静语气——
 * 中性前景色、留白合理、行内/块级代码用淡背景、不引入抢眼的颜色。
 *
 * 流式友好:每个 token 触发一次重渲染即可;半截的 `[` / `[1` 在右括号到达前以普通文本呈现。
 */
export function AssistantMarkdown({
  content,
  citations,
  onCitation,
}: {
  content: string;
  citations: Citation[];
  onCitation: (citation: Citation) => void;
}) {
  const byN = new Map(citations.map((c) => [c.n, c]));

  const components: Components = {
    // `<data value="n">` 是 rehype 插件注入的引用占位 → 真正的引用 chip。
    data: ({ value, children }) => {
      const n = Number(value);
      // 非引用占位(理论上不会出现,GFM 不产 <data>)或解析失败:原样回退,不吞内容。
      if (value == null || !Number.isFinite(n)) return <Fragment>{children}</Fragment>;
      return <CitationChip n={n} citation={byN.get(n)} onCitation={onCitation} />;
    },
    p: ({ children }) => <p className="my-2 first:mt-0 last:mb-0">{children}</p>,
    ul: ({ children }) => (
      <ul className="my-2 ml-5 list-disc space-y-1 marker:text-muted-foreground first:mt-0 last:mb-0">
        {children}
      </ul>
    ),
    ol: ({ children }) => (
      <ol className="my-2 ml-5 list-decimal space-y-1 marker:text-muted-foreground first:mt-0 last:mb-0">
        {children}
      </ol>
    ),
    li: ({ children }) => <li className="leading-relaxed [&>ul]:my-1 [&>ol]:my-1">{children}</li>,
    h1: ({ children }) => (
      <h1 className="mt-4 mb-2 text-base font-semibold first:mt-0">{children}</h1>
    ),
    h2: ({ children }) => (
      <h2 className="mt-4 mb-2 text-[0.95rem] font-semibold first:mt-0">{children}</h2>
    ),
    h3: ({ children }) => (
      <h3 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h3>
    ),
    h4: ({ children }) => (
      <h4 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h4>
    ),
    h5: ({ children }) => (
      <h5 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h5>
    ),
    h6: ({ children }) => (
      <h6 className="mt-3 mb-1.5 text-sm font-semibold first:mt-0">{children}</h6>
    ),
    a: ({ children, href }) => (
      <a
        href={href}
        target="_blank"
        rel="noreferrer noopener"
        className="font-medium text-primary underline underline-offset-2 hover:text-primary/80"
      >
        {children}
      </a>
    ),
    strong: ({ children }) => <strong className="font-semibold text-foreground">{children}</strong>,
    em: ({ children }) => <em className="italic">{children}</em>,
    blockquote: ({ children }) => (
      <blockquote className="my-2 border-l-2 border-border pl-3 text-muted-foreground first:mt-0 last:mb-0">
        {children}
      </blockquote>
    ),
    hr: () => <hr className="my-4 border-border" />,
    code: ({ className, children, ...props }) => {
      // 块级代码会被 <pre> 包住,带 language-* 类或含换行;行内代码两者皆无 → 行内徽章样式。
      const text = typeof children === "string" ? children : "";
      const isBlock = /language-/.test(className ?? "") || text.includes("\n");
      if (!isBlock) {
        return (
          <code className="rounded bg-muted px-1 py-0.5 font-mono text-[0.85em] text-foreground" {...props}>
            {children}
          </code>
        );
      }
      return (
        <code className={cn("font-mono text-[0.85em]", className)} {...props}>
          {children}
        </code>
      );
    },
    pre: ({ children }) => (
      <pre className="my-2.5 overflow-x-auto rounded-lg bg-muted p-3 text-[0.85em] leading-relaxed first:mt-0 last:mb-0">
        {children}
      </pre>
    ),
    table: ({ children }) => (
      <div className="my-2.5 overflow-x-auto first:mt-0 last:mb-0">
        <table className="w-full border-collapse text-left text-[0.9em]">{children}</table>
      </div>
    ),
    thead: ({ children }) => <thead className="border-b border-border">{children}</thead>,
    th: ({ children }) => <th className="px-2.5 py-1.5 font-semibold">{children}</th>,
    td: ({ children }) => (
      <td className="border-b border-border/60 px-2.5 py-1.5 align-top">{children}</td>
    ),
  };

  return (
    <ReactMarkdown
      remarkPlugins={REMARK_PLUGINS}
      rehypePlugins={REHYPE_PLUGINS}
      components={components}
    >
      {balanceFences(content)}
    </ReactMarkdown>
  );
}

function CitationChip({
  n,
  citation,
  onCitation,
}: {
  n: number;
  citation: Citation | undefined;
  onCitation: (citation: Citation) => void;
}): ReactNode {
  // 未拿到对应引用元数据(尚在流式)时,渲染为不可点的占位标记。
  if (!citation) {
    return (
      <sup className="mx-0.5 rounded bg-muted px-1 text-[0.7em] font-medium text-muted-foreground tabular-nums">
        {n}
      </sup>
    );
  }
  return (
    <button
      type="button"
      onClick={() => onCitation(citation)}
      title={citation.snippet}
      className={cn(
        "mx-0.5 inline-flex translate-y-[-1px] items-center rounded bg-primary/10 px-1 align-baseline",
        "text-[0.7em] font-semibold text-primary tabular-nums leading-none",
        "outline-none transition-colors hover:bg-primary/20 focus-visible:ring-2 focus-visible:ring-ring/50",
      )}
      aria-label={`查看来源 ${n}`}
    >
      {n}
    </button>
  );
}
