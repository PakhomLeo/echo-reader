import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Outlet, Route, Routes } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ImportJob } from "../src/api/imports";
import { ImportsPage, type AppOutletContext } from "../src/components/imports/ImportsPage";
import type { ImportsController } from "../src/hooks/useImports";

function job(overrides: Partial<ImportJob> = {}): ImportJob {
  return { id: "j1", batch_id: "b1", position: 1, url: "https://a.test/", requested_mode: "auto", effective_mode: null, asr_backend: null, status: "failed", stage: "extract", message: "提取失败", progress: null, queue_position: null, eta_seconds: null, attempt: 1, created_at: "", updated_at: "", started_at: null, completed_at: null, item_id: null, item_path: null, error: { code: "extract_failed", message: "无法提取", retryable: true }, warning: null, quality: null, duplicate: null, can_cancel: false, can_retry: true, can_reindex: false, is_active: false, ...overrides };
}

function controller(overrides: Partial<ImportsController> = {}): ImportsController {
  return {
    jobs: [], capabilities: null, batch: null, page: 1, pageSize: 20, total: 0, pages: 1,
    counts: { queued: 0, running: 0, pending_index: 0, succeeded: 0, failed: 0, cancelled: 0, interrupted: 0 }, activeJobs: 0,
    status: "all", online: true, loading: false, submitting: false, actionJobId: null, error: null,
    reload: vi.fn(async () => {}), submit: vi.fn(async () => ({}) as never), setPage: vi.fn(), setStatus: vi.fn(),
    cancel: vi.fn(async () => ({}) as never), retry: vi.fn(async () => ({}) as never), reindex: vi.fn(async () => ({}) as never), retryFailed: vi.fn(async () => ({}) as never),
    ...overrides,
  };
}

function renderPage(imports: ImportsController) {
  const context: AppOutletContext = { imports, openImport: vi.fn() };
  return render(<MemoryRouter initialEntries={["/imports"]}><Routes><Route element={<Outlet context={context} />}><Route path="imports" element={<ImportsPage />} /><Route path="items/:id" element={<p>阅读页</p>} /></Route></Routes></MemoryRouter>);
}

describe("导入任务页", () => {
  it("按服务动作能力展示重试，并驱动筛选和分页", async () => {
    const failed = job();
    const imports = controller({ jobs: [failed], total: 21, pages: 2 });
    renderPage(imports);
    const user = userEvent.setup();
    expect(screen.queryByRole("button", { name: /打开内容/ })).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "按原配置重试" }));
    expect(imports.retry).toHaveBeenCalledWith(failed);
    await user.selectOptions(screen.getByLabelText("状态"), "interrupted");
    expect(imports.setStatus).toHaveBeenCalledWith("interrupted");
    await user.click(screen.getByRole("button", { name: "下一页" }));
    expect(imports.setPage).toHaveBeenCalledWith(2);
  });

  it("仅为成功且有 item_id 的任务提供打开操作", async () => {
    renderPage(controller({ jobs: [job({ status: "pending_index", can_retry: false, can_reindex: true }), job({ id: "done", status: "succeeded", item_id: "item/1", error: null, can_retry: false })], total: 2 }));
    expect(screen.getByRole("button", { name: "仅重新索引" })).toBeInTheDocument();
    await userEvent.click(screen.getByRole("button", { name: /打开内容/ }));
    expect(screen.getByText("阅读页")).toBeInTheDocument();
  });

  it("取消排队任务前确认", async () => {
    const queued = job({ status: "queued", can_cancel: true, can_retry: false, error: null });
    const imports = controller({ jobs: [queued], total: 1 });
    vi.spyOn(window, "confirm").mockReturnValue(false);
    renderPage(imports);
    await userEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(imports.cancel).not.toHaveBeenCalled();
  });

  it("warning-only任务提供同源诊断入口，并展示结构化错误来源", () => {
    renderPage(controller({ jobs: [
      job({ id: "warning", warning: "媒体探测失败", error: null, can_retry: false }),
      job({ id: "error", error: { source: "medialab", code: "download.auth", message: "需要登录", retryable: false }, can_retry: false }),
    ], total: 2 }));
    const links = screen.getAllByRole("link", { name: "查看诊断数据" });
    expect(links[0]).toHaveAttribute("href", "/api/v1/import-jobs/warning");
    expect(screen.getByText("medialab / download.auth")).toBeInTheDocument();
  });

  it.each(["批次中没有可重试任务", "导入服务请求失败（500）", "导入服务未连接"])(
    "以可访问警报展示批次操作错误：%s",
    (message) => {
      renderPage(controller({ error: message }));
      const alert = screen.getByRole("alert");
      expect(alert).toHaveTextContent(message);
      expect(alert).toHaveAttribute("aria-live", "assertive");
    },
  );
});
