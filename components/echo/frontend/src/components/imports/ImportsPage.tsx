import { ExternalLink, RefreshCw } from "lucide-react";
import { useNavigate, useOutletContext } from "react-router-dom";
import type { ImportJob, ImportStatus } from "../../api/imports";
import type { ImportsController } from "../../hooks/useImports";
import "./ImportsPage.css";

const STATUS_LABEL: Record<ImportStatus, string> = {
  queued: "排队中",
  running: "处理中",
  pending_index: "等待索引",
  succeeded: "已完成",
  failed: "失败",
  cancelled: "已取消",
  interrupted: "已中断",
};

export interface AppOutletContext {
  openImport: () => void;
  imports: ImportsController;
}

function duration(seconds: number) {
  if (seconds < 60) return `约 ${Math.ceil(seconds)} 秒`;
  return `约 ${Math.ceil(seconds / 60)} 分钟`;
}

function JobRow({ job, controller }: { job: ImportJob; controller: ImportsController }) {
  const navigate = useNavigate();
  const busy = controller.actionJobId === job.id;
  const showProgress = job.status === "running" && job.progress !== null;
  const cancel = () => {
    if (job.status === "queued" && !window.confirm(`确认取消排队任务“${job.url}”？`)) return;
    void controller.cancel(job).catch(() => undefined);
  };
  return <article className="task-row">
    <div className="task-heading"><span className={`task-status task-status-${job.status}`}>{STATUS_LABEL[job.status]}</span><strong title={job.url}>{job.url}</strong><small>第 {job.attempt} 次尝试</small></div>
    <div className="task-detail">
      {job.stage && <span>阶段：{job.stage}</span>}
      {job.message && <span>{job.message}</span>}
      {job.status === "queued" && job.queue_position !== null && <span>队列位置：{job.queue_position}</span>}
      {job.eta_seconds !== null && <span>预计等待：{duration(job.eta_seconds)}</span>}
    </div>
    {showProgress && <progress max="100" value={job.progress!} aria-label={`${job.url} 的导入进度`} />}
    {job.warning && <p className="task-warning">警告：{job.warning}</p>}
    {job.duplicate && <p className="task-warning">检测到重复内容，默认保留已有内容{job.duplicate.score !== null ? `（相似度 ${Math.round(job.duplicate.score * 100)}%）` : ""}。</p>}
    {job.quality && <details><summary>质量信息</summary><pre>{JSON.stringify(job.quality, null, 2)}</pre></details>}
    {job.error && <details className="task-error"><summary>{job.error.message}</summary><code>{[job.error.source, job.error.code].filter(Boolean).join(" / ")}</code></details>}
    <div className="task-actions">
      {(job.error || job.warning || job.quality) && <a href={`/api/v1/import-jobs/${encodeURIComponent(job.id)}`} target="_blank" rel="noreferrer">查看诊断数据</a>}
      {job.can_cancel && <button type="button" disabled={busy} onClick={cancel}>取消</button>}
      {job.can_retry && <button type="button" disabled={busy} onClick={() => void controller.retry(job).catch(() => undefined)}>按原配置重试</button>}
      {job.can_reindex && <button type="button" disabled={busy} onClick={() => void controller.reindex(job).catch(() => undefined)}>仅重新索引</button>}
      {job.status === "succeeded" && job.item_id && <button type="button" onClick={() => navigate(`/items/${encodeURIComponent(job.item_id!)}`)}>打开内容 <ExternalLink size={13} /></button>}
    </div>
  </article>;
}

export function ImportsPage() {
  const { openImport, imports } = useOutletContext<AppOutletContext>();
  const failedCount = imports.batch?.counts.failed ?? 0;
  return <section className="tasks-page" aria-labelledby="tasks-title">
    <header className="tasks-header"><div><p className="import-eyebrow">后台任务</p><h1 id="tasks-title">导入任务</h1><p>显示服务提供的实际队列、阶段和索引状态。</p></div><div><button type="button" onClick={() => void imports.reload()} aria-label="刷新导入任务"><RefreshCw size={16} /> 刷新</button><button className="primary-button" type="button" onClick={openImport}>添加链接</button></div></header>
    {imports.batch && <aside className="batch-summary" aria-live="polite"><strong>最近批次：{imports.batch.total} 个任务</strong><span>{Object.entries(imports.batch.counts).map(([key, count]) => `${STATUS_LABEL[key as ImportStatus]} ${count}`).join(" · ")}</span>{failedCount > 0 && <button type="button" disabled={imports.submitting} onClick={() => void imports.retryFailed(imports.batch!).catch(() => undefined)}>重试该批次失败项</button>}</aside>}
    <div className="tasks-toolbar"><label>状态<select value={imports.status} onChange={(e) => imports.setStatus(e.target.value as ImportStatus | "all")}><option value="all">全部</option>{Object.entries(STATUS_LABEL).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label><span>共 {imports.total} 个任务</span></div>
    {imports.error && <p className="import-error" role="alert" aria-live="assertive">{imports.error}</p>}
    {imports.loading && imports.jobs.length === 0 ? <p role="status">正在读取任务…</p> : imports.jobs.length === 0 ? <p>没有符合筛选条件的任务。</p> : <div className="tasks-list">{imports.jobs.map((job) => <JobRow key={job.id} job={job} controller={imports} />)}</div>}
    <nav className="tasks-pagination" aria-label="任务分页"><button type="button" disabled={imports.page <= 1} onClick={() => imports.setPage(imports.page - 1)}>上一页</button><span>第 {imports.page} / {imports.pages} 页</span><button type="button" disabled={imports.page >= imports.pages} onClick={() => imports.setPage(imports.page + 1)}>下一页</button></nav>
  </section>;
}
