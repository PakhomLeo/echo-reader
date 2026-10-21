import type { ItemSummary } from "../../api/types";

export type LibraryFilter = "all" | "article" | "video" | "audio" | "mixed";

export function matchesLibraryFilter(
  item: ItemSummary,
  filter: LibraryFilter,
): boolean {
  return (
    filter === "all" ||
    item.source_type === filter ||
    (filter === "article" && item.has_article)
  );
}

export function countLibraryItems(
  items: ItemSummary[],
): Record<LibraryFilter, number> {
  return {
    all: items.length,
    article: items.filter((item) => matchesLibraryFilter(item, "article"))
      .length,
    video: items.filter((item) => matchesLibraryFilter(item, "video")).length,
    audio: items.filter((item) => matchesLibraryFilter(item, "audio")).length,
    mixed: items.filter((item) => matchesLibraryFilter(item, "mixed")).length,
  };
}
