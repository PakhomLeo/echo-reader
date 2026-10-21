import { Search } from "lucide-react";
import type { ItemSummary } from "../../api/types";
import type { LibraryFilter } from "./Sidebar";
import { SidebarItem } from "./SidebarItem";

interface ContentListProps {
  items: ItemSummary[];
  loading: boolean;
  error: string | null;
  query: string;
  filter: LibraryFilter;
  onQuery: (value: string) => void;
  onFilter: (value: LibraryFilter) => void;
  onReload: () => void;
  onAdd: () => void;
}
const FILTERS: [LibraryFilter, string][] = [
  ["all", "全部"],
  ["article", "文章"],
  ["video", "视频"],
  ["audio", "音频"],
  ["mixed", "混合"],
];
export function ContentList({
  items,
  loading,
  error,
  query,
  filter,
  onQuery,
  onFilter,
  onReload,
  onAdd,
}: ContentListProps) {
  return (
    <section className="content-list-panel" aria-label="内容列表">
      <header className="content-list-header">
        <div className="content-list-title">
          <h1>内容</h1>
          <span>{items.length} 条</span>
        </div>
        <div className="content-search">
          <Search size={15} />
          <input
            type="search"
            value={query}
            onChange={(e) => onQuery(e.target.value)}
            placeholder="搜索标题、作者或来源"
            aria-label="搜索内容"
          />
        </div>
        <div className="content-filter-row" aria-label="类型筛选">
          {FILTERS.map(([id, label]) => (
            <button
              type="button"
              key={id}
              className={filter === id ? "is-active" : ""}
              onClick={() => onFilter(id)}
              aria-pressed={filter === id}
            >
              {label}
            </button>
          ))}
        </div>
      </header>
      <nav className="sidebar-list" aria-label="内容列表">
        {loading ? (
          <div className="sidebar-hint">正在加载资料库…</div>
        ) : error ? (
          <div className="sidebar-hint">
            {error}
            <button type="button" onClick={onReload}>
              重新加载
            </button>
          </div>
        ) : items.length === 0 ? (
          <div className="sidebar-hint">
            {query || filter !== "all"
              ? "没有匹配的内容"
              : "资料库中还没有内容"}
            <button type="button" onClick={onAdd}>
              {query || filter !== "all"
                ? "清除筛选并添加内容"
                : "添加第一条内容"}
            </button>
          </div>
        ) : (
          items.map((item) => <SidebarItem key={item.id} item={item} />)
        )}
      </nav>
    </section>
  );
}
