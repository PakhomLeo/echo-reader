import { FileText, Headphones, Layers3, MessageSquareText, Video } from "lucide-react";
import { NavLink } from "react-router-dom";
import type { ItemSummary } from "../../api/types";

const TYPE_LABELS = {
  article: "文章",
  video: "视频",
  audio: "音频",
  mixed: "混合",
};
function relativeTime(value: string) {
  const time = new Date(value).getTime();
  if (!Number.isFinite(time)) return value;
  const days = Math.floor((Date.now() - time) / 86400000);
  if (days <= 0) return "今天";
  if (days === 1) return "昨天";
  if (days < 30) return `${days} 天前`;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "short",
    day: "numeric",
  }).format(time);
}
export function SidebarItem({ item }: { item: ItemSummary }) {
  const Icon =
    item.source_type === "video"
      ? Video
      : item.source_type === "audio"
        ? Headphones
        : item.source_type === "mixed"
          ? Layers3
          : FileText;
  const title = item.source_title || item.folder_name;
  const source = item.source_author || item.folder_name;
  return (
    <NavLink
      to={`/items/${encodeURIComponent(item.id)}`}
      className={({ isActive }) =>
        `sidebar-item${isActive ? " sidebar-item-active" : ""}`
      }
    >
      <div className="item-list-heading">
        <Icon size={15} />
        <strong>{title}</strong>
      </div>
      <p>{source}</p>
      <div className="item-list-meta">
        <span>{item.source_type ? TYPE_LABELS[item.source_type] : "内容"}</span>
        <span>{relativeTime(item.updated_at)}</span>
        <span className="availability" aria-label="可用内容">
          {item.has_article && <FileText size={13} />}{" "}
          {item.has_transcript && <MessageSquareText size={13} />}{" "}
          {item.has_media && <Headphones size={13} />}
        </span>
      </div>
    </NavLink>
  );
}
