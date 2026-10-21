import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { ExternalLink } from "./ExternalLink";
import { ImageBlock } from "./ImageBlock";
import { rehypeImageIndex } from "./rehypeImageIndex";

interface MarkdownRendererProps {
  itemId: string;
  markdown: string;
  articleVersion?: string;
  onImageDeleted?: (result: { markdown: string; version: string }) => void;
}

export function MarkdownRenderer({
  itemId,
  markdown,
  articleVersion,
  onImageDeleted,
}: MarkdownRendererProps) {
  const components: Components = {
    a: (props) => <ExternalLink {...props} />,
    img: (props) => {
      const occurrence = Number(
        (props as Record<string, unknown>)["data-occurrence"] ?? 0,
      );
      return (
        <ImageBlock
          itemId={itemId}
          src={props.src ?? ""}
          alt={props.alt ?? ""}
          title={props.title}
          occurrence={occurrence}
          articleVersion={articleVersion}
          onDeleted={onImageDeleted}
        />
      );
    },
  };

  return (
    <div className="markdown-body">
      <ReactMarkdown
        rehypePlugins={[rehypeImageIndex]}
        remarkPlugins={[remarkGfm]}
        components={components}
      >
        {markdown}
      </ReactMarkdown>
    </div>
  );
}
