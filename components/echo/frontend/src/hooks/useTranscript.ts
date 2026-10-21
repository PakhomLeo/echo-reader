import { useCallback, useEffect, useState } from "react";
import { getTranscript } from "../api/items";
import type { TranscriptData } from "../api/types";

/**
 * 按需加载转录数据；加载后缓存（切换模式不重新请求）。
 * enabled 变 false 时不清数据；itemId 变化时重置。
 * 注意：不能在 effect 依赖中使用 loading 阻止重入，
 * React StrictMode 的双执行会把第一次请求取消后永久卡死。
 */
export function useTranscript(itemId: string, enabled: boolean) {
  const [data, setData] = useState<TranscriptData | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    setData(null);
    setError(null);
    setLoading(false);
  }, [itemId]);

  useEffect(() => {
    if (!enabled || data !== null) return;
    let cancelled = false;
    setLoading(true);
    setError(null);
    getTranscript(itemId)
      .then((d) => {
        if (!cancelled) setData(d);
      })
      .catch((e) => {
        if (!cancelled)
          setError(e instanceof Error ? e.message : "转录加载失败");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [enabled, itemId, data]);

  const reload = useCallback(() => {
    setData(null);
    setError(null);
  }, []);

  return { data, error, loading, reload };
}
