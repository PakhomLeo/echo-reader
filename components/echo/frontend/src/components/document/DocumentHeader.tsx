import { ExternalLink, Pencil } from "lucide-react";
import type { ItemDetail } from "../../api/types";
import "./DocumentPage.css";
interface Props {
  detail: ItemDetail;
  editingArticle: boolean;
  onEditArticle: () => void;
}
const LABELS: Record<string, string> = {
  article: "文章",
  video: "视频",
  audio: "音频",
  mixed: "混合",
};
function formatDate(value: string) {
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat("zh-CN", {
        year: "numeric",
        month: "long",
        day: "numeric",
      }).format(date);
}
export function DocumentHeader({
  detail,
  editingArticle,
  onEditArticle,
}: Props) {
  const { source } = detail;
  return (
    <header className="document-header">
      {(source.type || source.author) && (
        <p className="document-kicker">
          {source.type && LABELS[source.type]}
          {source.type && source.author && " · "}
          {source.author}
        </p>
      )}
      <h1 className="document-title">{source.title || detail.folder_name}</h1>
      {source.description && (
        <p className="document-description">{source.description}</p>
      )}
      {(source.title ||
        source.published_at ||
        source.url ||
        detail.article.available) && (
        <div className="document-meta">
          {source.title && (
            <span className="document-folder">{detail.folder_name}</span>
          )}
          {source.published_at && <time>{formatDate(source.published_at)}</time>}
          <span className="document-actions">
            {source.url && (
              <a href={source.url} target="_blank" rel="noopener noreferrer">
                <ExternalLink size={15} />
                查看来源
              </a>
            )}
            {detail.article.available && (
              <button
                type="button"
                onClick={onEditArticle}
                disabled={editingArticle}
              >
                <Pencil size={15} />
                编辑文章
              </button>
            )}
          </span>
        </div>
      )}
    </header>
  );
}
