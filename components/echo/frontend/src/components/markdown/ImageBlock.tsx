import { useState } from "react";
import { deleteImageReference } from "../../api/items";
import { resolveImageUrl } from "./urls";

interface ImageBlockProps {
  itemId: string;
  src: string;
  alt: string;
  title?: string;
  occurrence: number;
  articleVersion?: string;
  onDeleted?: (result: { markdown: string; version: string }) => void;
}

export function ImageBlock({
  itemId,
  src,
  alt,
  title,
  occurrence,
  articleVersion,
  onDeleted,
}: ImageBlockProps) {
  const url = resolveImageUrl(itemId, src);
  const [failed, setFailed] = useState(false);
  const [deleting, setDeleting] = useState(false);

  const handleDelete = async () => {
    if (!articleVersion || !onDeleted) return;
    const ok = window.confirm(
      "从文章中删除这张图片？\n\n图片引用会从文章中移除，文件会移动到回收目录。",
    );
    if (!ok) return;
    setDeleting(true);
    try {
      const result = await deleteImageReference(
        itemId,
        src,
        occurrence,
        articleVersion,
      );
      onDeleted(result.article);
    } catch (e) {
      window.alert(e instanceof Error ? e.message : "删除图片失败");
    } finally {
      setDeleting(false);
    }
  };

  if (failed) {
    return (
      <span className="markdown-image-error">
        图片无法加载
        <span className="markdown-image-error-path">{src}</span>
      </span>
    );
  }

  if (!url) return null;

  return (
    <span className="markdown-image">
      <img
        src={url}
        alt={alt}
        title={title}
        loading="lazy"
        onError={() => setFailed(true)}
      />
      {onDeleted && articleVersion && (
        <button
          type="button"
          className="markdown-image-delete"
          onClick={() => void handleDelete()}
          disabled={deleting}
          aria-label={`删除图片 ${src}`}
          title="删除这张图片"
        >
          {deleting ? "…" : "🗑"}
        </button>
      )}
    </span>
  );
}
