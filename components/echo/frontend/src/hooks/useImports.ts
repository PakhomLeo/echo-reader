import { useCallback, useEffect, useRef, useState } from "react";
import {
  cancelImportJob,
  createImportBatch,
  getImportBatch,
  getImportCapabilities,
  listImportJobs,
  reindexImportJob,
  retryFailedImportBatch,
  retryImportJob,
} from "../api/imports";
import type {
  CreateImportBatchRequest,
  ImportBatch,
  ImportCapabilities,
  ImportJob,
  ImportJobFilters,
  ImportStatus,
} from "../api/imports";

const ACTIVE: ImportStatus[] = ["queued", "running", "pending_index"];
const EMPTY_COUNTS: Record<ImportStatus, number> = {
  queued: 0,
  running: 0,
  pending_index: 0,
  succeeded: 0,
  failed: 0,
  cancelled: 0,
  interrupted: 0,
};

export function useImports(onSucceeded: (itemId: string) => void) {
  const [jobs, setJobs] = useState<ImportJob[]>([]);
  const [capabilities, setCapabilities] = useState<ImportCapabilities | null>(null);
  const [batch, setBatch] = useState<ImportBatch | null>(null);
  const [page, setPage] = useState(1);
  const [pageSize] = useState(20);
  const [total, setTotal] = useState(0);
  const [counts, setCounts] = useState<Record<ImportStatus, number>>(EMPTY_COUNTS);
  const [activeJobs, setActiveJobs] = useState(0);
  const [pages, setPages] = useState(1);
  const [status, setStatusState] = useState<ImportStatus | "all">("all");
  const [online, setOnline] = useState<boolean | null>(null);
  const [loading, setLoading] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [actionJobId, setActionJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const observedRef = useRef(new Map<string, ImportStatus>());
  const callbackRef = useRef(onSucceeded);
  callbackRef.current = onSucceeded;
  const batchId = batch?.id ?? null;

  const load = useCallback(async (filters: ImportJobFilters = {}) => {
    const result = await listImportJobs(filters);
    setJobs(result.items);
    setTotal(result.total);
    setCounts(result.counts);
    setActiveJobs(result.active_count);
    setPages(Math.max(1, Math.ceil(result.total / result.page_size)));
    for (const job of result.items) {
      const previous = observedRef.current.get(job.id);
      if (
        previous &&
        ACTIVE.includes(previous) &&
        job.status === "succeeded" &&
        job.item_id
      ) {
        callbackRef.current(job.item_id);
      }
      observedRef.current.set(job.id, job.status);
    }
  }, []);

  const reload = useCallback(async () => {
    setLoading(true);
    try {
      const [nextCapabilities] = await Promise.all([
        getImportCapabilities(),
        load({ page, pageSize, status }),
      ]);
      setCapabilities(nextCapabilities);
      if (batchId) setBatch(await getImportBatch(batchId));
      setOnline(true);
      setError(null);
    } catch (cause) {
      setOnline(false);
      setError(cause instanceof Error ? cause.message : "导入服务未连接");
    } finally {
      setLoading(false);
    }
  }, [batchId, load, page, pageSize, status]);

  useEffect(() => {
    void reload();
  }, [reload]);

  useEffect(() => {
    if (activeJobs === 0) return;
    const timer = window.setInterval(() => void reload(), 1800);
    return () => window.clearInterval(timer);
  }, [activeJobs, reload]);

  const submit = useCallback(async (request: CreateImportBatchRequest) => {
    setSubmitting(true);
    setError(null);
    try {
      const created = await createImportBatch(request);
      setBatch(created);
      setActiveJobs((current) => current + created.active_count);
      setOnline(true);
      setPage(1);
      return created;
    } catch (cause) {
      const message = cause instanceof Error ? cause.message : "提交失败";
      setError(message);
      throw cause;
    } finally {
      setSubmitting(false);
    }
  }, []);

  const runJobAction = useCallback(
    async (job: ImportJob, action: "cancel" | "retry" | "reindex") => {
      setActionJobId(job.id);
      setError(null);
      try {
        const updated = await (action === "cancel"
          ? cancelImportJob(job.id)
          : action === "retry"
            ? retryImportJob(job.id)
            : reindexImportJob(job.id));
        setJobs((current) =>
          current.map((entry) => (entry.id === updated.id ? updated : entry)),
        );
        if (job.is_active !== updated.is_active) {
          setActiveJobs((current) => Math.max(0, current + (updated.is_active ? 1 : -1)));
        }
        return updated;
      } catch (cause) {
        setError(cause instanceof Error ? cause.message : "操作失败");
        throw cause;
      } finally {
        setActionJobId(null);
      }
    },
    [],
  );

  const retryFailed = useCallback(async (target: ImportBatch) => {
    setSubmitting(true);
    setError(null);
    try {
      const updated = await retryFailedImportBatch(target.id);
      setBatch(updated);
      setActiveJobs((current) =>
        Math.max(0, current - target.active_count + updated.active_count),
      );
      setJobs((current) => {
        const returnedIds = new Set(updated.items.map((job) => job.id));
        return [...updated.items, ...current.filter((job) => !returnedIds.has(job.id))];
      });
      setCounts((current) => {
        const next = { ...current };
        const previousById = new Map(target.items.map((job) => [job.id, job.status]));
        for (const job of updated.items) {
          const previous = previousById.get(job.id);
          if (previous && previous !== job.status) {
            next[previous] = Math.max(0, next[previous] - 1);
            next[job.status] += 1;
          }
        }
        return next;
      });
      return updated;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "操作失败");
      throw cause;
    } finally {
      setSubmitting(false);
    }
  }, []);

  const setStatus = useCallback((next: ImportStatus | "all") => {
    setStatusState(next);
    setPage(1);
  }, []);

  return {
    jobs,
    capabilities,
    batch,
    page,
    pageSize,
    total,
    counts,
    activeJobs,
    pages,
    status,
    online,
    loading,
    submitting,
    actionJobId,
    error,
    reload,
    submit,
    setPage,
    setStatus,
    cancel: (job: ImportJob) => runJobAction(job, "cancel"),
    retry: (job: ImportJob) => runJobAction(job, "retry"),
    reindex: (job: ImportJob) => runJobAction(job, "reindex"),
    retryFailed,
  };
}

export type ImportsController = ReturnType<typeof useImports>;
