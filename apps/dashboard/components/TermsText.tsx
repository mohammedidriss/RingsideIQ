/**
 * The terms and conditions (Markdown from GET /auth/terms) as plain, safe React:
 * headings, quotes, bullet lists, **bold** and paragraphs — enough for the
 * terms, with no HTML ever injected.
 */

function inline(text: string): React.ReactNode[] {
  return text.split(/(\*\*[^*]+\*\*|`[^`]+`)/g).map((part, i) =>
    part.startsWith("**") && part.endsWith("**") ? (
      <strong key={i} className="text-neutral-100">
        {part.slice(2, -2)}
      </strong>
    ) : part.startsWith("`") && part.endsWith("`") ? (
      <code key={i} className="rounded bg-white/10 px-1 text-[0.9em]">
        {part.slice(1, -1)}
      </code>
    ) : (
      part
    ),
  );
}

export function TermsText({ text }: { text: string }) {
  const blocks = text.trim().split(/\n\s*\n/);
  return (
    <div className="space-y-3 text-sm leading-relaxed text-neutral-300">
      {blocks.map((block, i) => {
        const lines = block.split("\n");
        if (block.startsWith("# ")) {
          return (
            <h1 key={i} className="text-xl font-semibold text-neutral-100">
              {inline(block.slice(2))}
            </h1>
          );
        }
        if (block.startsWith("## ")) {
          return (
            <h2 key={i} className="pt-2 text-base font-semibold text-neutral-100">
              {inline(block.slice(3))}
            </h2>
          );
        }
        if (lines.every((l) => l.startsWith(">"))) {
          return (
            <blockquote key={i} className="rounded-lg border border-amber-500/30 bg-amber-950/30 p-3 text-amber-100">
              {inline(lines.map((l) => l.replace(/^>\s?/, "")).join(" "))}
            </blockquote>
          );
        }
        if (lines.every((l) => /^\s*- |^\s{2,}\S/.test(l))) {
          const items = block.split(/\n(?=\s*- )/).map((l) => l.replace(/^\s*- /, "").replace(/\n\s+/g, " "));
          return (
            <ul key={i} className="list-disc space-y-1 pl-5">
              {items.map((item, j) => (
                <li key={j}>{inline(item)}</li>
              ))}
            </ul>
          );
        }
        return <p key={i}>{inline(lines.join(" "))}</p>;
      })}
    </div>
  );
}
