import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";

// Agent replies are GitHub-flavored markdown (tables, headings, lists, code).
// Rendered in a scoped .md container; raw HTML is NOT enabled (react-markdown
// ignores it by default), so agent output can't inject markup.
export function Markdown({ children }: { children: string }) {
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{children}</ReactMarkdown>
    </div>
  );
}
