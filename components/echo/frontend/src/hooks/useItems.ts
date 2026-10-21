import { useCallback, useEffect, useRef, useState } from "react";
import { listItems, rescanLibrary, switchLibrary } from "../api/items";
import type { ItemSummary } from "../api/types";

export interface ItemsState {
  items: ItemSummary[];
  loading: boolean;
  error: string | null;
  refreshing: boolean;
  refresh: () => Promise<void>;
  switchRoot: (
    path: string,
  ) => Promise<{ library_root: string; item_count: number; invalid_item_count: number }>;
}

export function useItems(): ItemsState {
  const [items, setItems] = useState<ItemSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [refreshing, setRefreshing] = useState(false);
  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    try {
      const data = await listItems();
      if (generation === loadGeneration.current) {
        setItems(data.items);
        setError(null);
      }
    } catch (e) {
      if (generation === loadGeneration.current) {
        setError(e instanceof Error ? e.message : "加载失败");
      }
    } finally {
      if (generation === loadGeneration.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
    };
  }, [load]);

  const refresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await rescanLibrary();
      await load();
    } catch {
      setError("刷新失败，请确认后端已启动");
    } finally {
      setRefreshing(false);
    }
  }, [load]);

  const switchRoot = useCallback(
    async (path: string) => {
      loadGeneration.current += 1;
      setItems([]);
      setError(null);
      setLoading(true);
      try {
        const result = await switchLibrary(path);
        await load();
        return result;
      } catch (switchError) {
        await load();
        throw switchError;
      }
    },
    [load],
  );

  return { items, loading, error, refreshing, refresh, switchRoot };
}
