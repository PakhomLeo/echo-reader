import { memo } from "react";
import { MarkdownRenderer } from "./MarkdownRenderer";

interface ArticleViewProps {
  itemId: string;
  markdown: string;
  articleVersion?: string;
  onImageDeleted?: (result: { markdown: string; version: string }) => void;
}

export const ArticleView = memo(function ArticleView({
  itemId,
  markdown,
  articleVersion,
  onImageDeleted,
}: ArticleViewProps) {
  return (
    <MarkdownRenderer
      itemId={itemId}
      markdown={markdown}
      articleVersion={articleVersion}
      onImageDeleted={onImageDeleted}
    />
  );
});
