import { useState } from "react";
import { parseMarkdown } from "./utils.js";

function Inline({ nodes, sources }) {
  return nodes.map((n, i) => {
    switch (n.t) {
      case "bold":
        return <strong key={i}>{n.v}</strong>;
      case "italic":
        return <em key={i}>{n.v}</em>;
      case "code":
        return (
          <code key={i} className="md-code">
            {n.v}
          </code>
        );
      case "link":
        return (
          <a key={i} href={n.href} target="_blank" rel="noopener noreferrer" className="md-link">
            {n.v}
          </a>
        );
      case "cite": {
        return (
          <span key={i} className="cite-group">
            {n.n.map((num) => {
              const src = sources?.[num - 1];
              const label = src ? src.title : "source";
              return src?.url ? (
                <a key={num} className="cite" href={src.url} target="_blank" rel="noopener noreferrer" title={label}>
                  {num}
                </a>
              ) : (
                <span key={num} className="cite" title={label}>
                  {num}
                </span>
              );
            })}
          </span>
        );
      }
      default:
        return <span key={i}>{n.v}</span>;
    }
  });
}

function CodeBlock({ block }) {
  const [copied, setCopied] = useState(false);
  const copy = async () => {
    try {
      await navigator.clipboard.writeText(block.code);
      setCopied(true);
      setTimeout(() => setCopied(false), 1400);
    } catch {
      /* clipboard unavailable */
    }
  };
  return (
    <div className="code-block">
      <div className="code-bar">
        <span>{block.lang || "code"}</span>
        <button type="button" onClick={copy}>
          {copied ? "COPIED" : "COPY"}
        </button>
      </div>
      <pre>
        <code>{block.code}</code>
        {block.open && <span className="cursor">▌</span>}
      </pre>
    </div>
  );
}

export default function Markdown({ text, sources }) {
  const blocks = parseMarkdown(text);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        switch (b.type) {
          case "code":
            return <CodeBlock key={i} block={b} />;
          case "h": {
            const Tag = `h${Math.min(b.level + 2, 6)}`;
            return (
              <Tag key={i} className="md-h">
                <Inline nodes={b.inline} sources={sources} />
              </Tag>
            );
          }
          case "ul":
            return (
              <ul key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Inline nodes={it} sources={sources} />
                  </li>
                ))}
              </ul>
            );
          case "ol":
            return (
              <ol key={i}>
                {b.items.map((it, j) => (
                  <li key={j}>
                    <Inline nodes={it} sources={sources} />
                  </li>
                ))}
              </ol>
            );
          case "quote":
            return (
              <blockquote key={i}>
                <Inline nodes={b.inline} sources={sources} />
              </blockquote>
            );
          default:
            return (
              <p key={i}>
                <Inline nodes={b.inline} sources={sources} />
              </p>
            );
        }
      })}
    </div>
  );
}
