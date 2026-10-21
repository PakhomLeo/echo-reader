import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { RefreshCw, Search } from "lucide-react";
import {
  getSearchStatus,
  rebuildSearchIndex,
  searchLibrary,
  type SearchHit,
  type SearchIndexStatus,
  type SearchMode,
} from "../../api/search";
import { formatTimestamp } from "../transcript/format";
import "./SearchPage.css";

const KIND_LABELS: Record<SearchHit["kind"], string> = {
  title: "标题",
  article: "文章",
  transcript: "转录",
  ocr: "图片文字",
};

interface ItemGroup {
  itemId: string;
  title: string;
  hits: SearchHit[];
}

function groupHits(hits: SearchHit[]): ItemGroup[] {
  const groups = new Map<string, ItemGroup>();
  for (const hit of hits) {
    const group = groups.get(hit.item_id) ?? {
      itemId: hit.item_id,
      title: hit.title,
      hits: [],
    };
    group.hits.push(hit);
    groups.set(hit.item_id, group);
  }
  return [...groups.values()];
}

export function SearchPage() {
  const [params, setParams] = useSearchParams();
  const query = params.get("q") ?? "";
  const mode: SearchMode = params.get("mode") === "question" ? "question" : "keyword";
  const [draft, setDraft] = useState(query);
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [status, setStatus] = useState<SearchIndexStatus | null>(null);
  const [rebuilding, setRebuilding] = useState(false);

  useEffect(() => setDraft(query), [query]);

  // Status is read on open and after every search (each search syncs the index first).
  const refreshStatus = useCallback(() => {
    getSearchStatus()
      .then(setStatus)
      .catch(() => undefined);
  }, []);
  useEffect(refreshStatus, [refreshStatus]);


  useEffect(() => {
    if (!query.trim()) {
      setHits([]);
      setError(null);
      return;
    }
    let current = true;
    setLoading(true);
    setError(null);
    searchLibrary(query, mode)
      .then((result) => {
        if (!current) return;
        setHits(result.hits);
        refreshStatus();
      })
      .catch((e: unknown) => {
        if (current) setError(e instanceof Error ? e.message : "搜索失败");
      })
      .finally(() => {
        if (current) setLoading(false);
      });
    return () => {
      current = false;
    };
  }, [query, mode, refreshStatus]);

  const groups = useMemo(() => groupHits(hits), [hits]);

  const submit = (event: FormEvent) => {
    event.preventDefault();
    const next = new URLSearchParams();
    if (draft.trim()) next.set("q", draft.trim());
    if (mode === "question") next.set("mode", "question");
    setParams(next);
  };

  const toggleMode = () => {
    const next = new URLSearchParams(params);
    if (mode === "question") next.delete("mode");
    else next.set("mode", "question");
    setParams(next);
  };

  const rebuild = async () => {
    setRebuilding(true);
    try {
      const result = await rebuildSearchIndex();
      setStatus(result.status);
      if (query.trim()) setHits((await searchLibrary(query, mode)).hits);
    } catch (e) {
      setError(e instanceof Error ? e.message : "重建索引失败");
    } finally {
      setRebuilding(false);
    }
  };

  return (
    <section className="search-page" aria-label="全文搜索">
      <header className="search-header">
        <h1>全文搜索</h1>
        <p>在文章、转录和图片文字中查找。索引可随时从资料库文件重建。</p>
      </header>
      <form className="search-form" role="search" onSubmit={submit}>
        <Search size={17} aria-hidden="true" />
        <input
          type="search"
          value={draft}
          onChange={(event) => setDraft(event.target.value)}
          placeholder={mode === "question" ? "用一句话提问，例如：哪些文章讲到播放器？" : "输入关键词"}
          aria-label="搜索内容"
          maxLength={500}
        />
        <button type="submit">搜索</button>
      </form>
      <div className="search-toolbar">
        <label>
          <input type="checkbox" checked={mode === "question"} onChange={toggleMode} />
          问句模式
        </label>
        {status && (
          <span className="search-status" data-up-to-date={status.up_to_date}>
            已索引 {status.indexed_items} / {status.library_items} 条
            {!status.up_to_date && "（有更新，下次搜索时同步）"}
          </span>
        )}
        <button type="button" onClick={() => void rebuild()} disabled={rebuilding}>
          <RefreshCw size={14} className={rebuilding ? "is-spinning" : ""} />
          {rebuilding ? "正在重建" : "重建索引"}
        </button>
      </div>
      {error && <p className="search-error" role="alert">{error}</p>}
      {loading && <p className="search-empty">搜索中…</p>}
      {!loading && query.trim() && !error && groups.length === 0 && (
        <p className="search-empty">没有找到匹配的内容</p>
      )}
      <ol className="search-results">
        {groups.map((group) => (
          <li key={group.itemId} className="search-result">
            <Link to={`/items/${encodeURIComponent(group.itemId)}`} className="search-result-title">
              {group.title}
            </Link>
            <ul>
              {group.hits.map((hit) => (
                <li key={`${hit.kind}:${hit.locator}`}>
                  <span className="search-kind">
                    {KIND_LABELS[hit.kind]}
                    {hit.kind === "transcript" && hit.start_ms !== null && ` ${formatTimestamp(hit.start_ms)}`}
                  </span>
                  <span className="search-snippet">{hit.snippet}</span>
                </li>
              ))}
            </ul>
          </li>
        ))}
      </ol>
    </section>
  );
}
