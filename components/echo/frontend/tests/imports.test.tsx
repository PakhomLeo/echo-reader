import { act, render, renderHook, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, describe, expect, it, vi } from "vitest";
import { ImportModal } from "../src/components/imports/ImportModal";
import { parseImportUrls } from "../src/components/imports/parseImportUrls";
import type { ImportCapabilities, ImportJob } from "../src/api/imports";

const apiMocks = vi.hoisted(() => ({
  capabilities: vi.fn(), list: vi.fn(), batch: vi.fn(), create: vi.fn(),
  cancel: vi.fn(), retry: vi.fn(), reindex: vi.fn(), retryFailed: vi.fn(),
}));
vi.mock("../src/api/imports", async (original) => ({
  ...(await original<typeof import("../src/api/imports")>()),
  getImportCapabilities: apiMocks.capabilities,
  listImportJobs: apiMocks.list,
  getImportBatch: apiMocks.batch,
  createImportBatch: apiMocks.create,
  cancelImportJob: apiMocks.cancel,
  retryImportJob: apiMocks.retry,
  reindexImportJob: apiMocks.reindex,
  retryFailedImportBatch: apiMocks.retryFailed,
}));
import { useImports } from "../src/hooks/useImports";

const capabilities: ImportCapabilities = {
  status: "ready", content_root: "C:/library", batch_max_urls: 50,
  recommended_backend: "local",
  backends: {
    online: { available: false, readiness: "unavailable", reason: "缺少凭据" },
    local: { available: true, readiness: "download_required", reason: null },
  },
  indexing: { available: true, reason: null },
};
const zeroCounts = { queued: 0, running: 0, pending_index: 0, succeeded: 0, failed: 0, cancelled: 0, interrupted: 0 };

afterEach(() => {
  vi.restoreAllMocks();
  Object.values(apiMocks).forEach((mock) => mock.mockReset());
});

describe("链接解析", () => {
  it("处理 CRLF、空行、顺序、查询串和精确规范 URL 去重", () => {
    expect(parseImportUrls("\r\n https://a.test/p?b=2&a=1\r\nhttps://a.test/p?b=2&a=1\nhttps://b.test/x ")).toMatchObject({
      urls: ["https://a.test/p?b=2&a=1", "https://b.test/x"], inputCount: 3, duplicateCount: 1, errors: [],
    });
  });
  it("拒绝非 HTTPS、相对网址和 userinfo", () => {
    const result = parseImportUrls("http://a.test\n/path\nhttps://u:p@a.test/x");
    expect(result.urls).toEqual([]);
    expect(result.errors).toHaveLength(3);
  });
  it("接受 50 个且不静默截断第 51 个", () => {
    expect(parseImportUrls(Array.from({ length: 50 }, (_, i) => `https://a.test/${i}`).join("\n")).errors).toEqual([]);
    const tooMany = parseImportUrls(Array.from({ length: 51 }, (_, i) => `https://a.test/${i}`).join("\n"));
    expect(tooMany.urls).toHaveLength(51);
    expect(tooMany.errors[0]).toContain("最多导入 50");
  });
});

describe("导入 API", () => {
  it("使用新路径、查询参数和精确批次请求体", async () => {
    const api = await vi.importActual<typeof import("../src/api/imports")>("../src/api/imports");
    const fetchMock = vi.spyOn(globalThis, "fetch").mockImplementation(async () => new Response("{}", { status: 200 }));
    await api.getImportCapabilities();
    await api.listImportJobs({ page: 2, pageSize: 10, status: "failed", batchId: "b/1" });
    const body = { urls: ["https://a.test/"], mode: "auto" as const, audio: null, audio_format: "mp3", speed_factor: 1, timeout: 1800, asr_backend: "auto" as const, local_timestamp_mode: "vad" as const, local_diarization: true, local_speaker_count: 0 };
    await api.createImportBatch(body);
    await api.cancelImportJob("j/1"); await api.retryImportJob("j/1"); await api.reindexImportJob("j/1"); await api.retryFailedImportBatch("b/1");
    expect(fetchMock.mock.calls.map(([url]) => url)).toEqual([
      "/api/v1/import-capabilities",
      "/api/v1/import-jobs?page=2&page_size=10&status=failed&batch_id=b%2F1",
      "/api/v1/import-batches",
      "/api/v1/import-jobs/j%2F1/cancel",
      "/api/v1/import-jobs/j%2F1/retry",
      "/api/v1/import-jobs/j%2F1/reindex",
      "/api/v1/import-batches/b%2F1/retry-failed",
    ]);
    expect(JSON.parse(String((fetchMock.mock.calls[2][1] as RequestInit).body))).toEqual(body);
  });

  it("展示 FastAPI 字符串与验证数组 detail", async () => {
    const api = await vi.importActual<typeof import("../src/api/imports")>("../src/api/imports");
    const fetchMock = vi.spyOn(globalThis, "fetch");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: "批次已满" }), { status: 409 }));
    await expect(api.createImportBatch({} as never)).rejects.toThrow("批次已满");
    fetchMock.mockResolvedValueOnce(new Response(JSON.stringify({ detail: [{ loc: ["body", "urls"], msg: "List should have at most 50 items" }] }), { status: 422 }));
    await expect(api.createImportBatch({} as never)).rejects.toThrow("请求参数错误：urls: List should have at most 50 items");
  });
});

describe("批量添加面板", () => {
  it("展示能力默认值、禁用原因并提交一个原生批次", async () => {
    const submit = vi.fn(async () => ({}));
    render(<ImportModal capabilities={capabilities} online submitting={false} error={null} onClose={() => {}} onSubmit={submit} onViewTasks={() => {}} />);
    const user = userEvent.setup();
    expect(screen.getByRole("radio", { name: /智能选择/ })).toBeChecked();
    expect(screen.getByText(/服务推荐：本地识别/)).toBeInTheDocument();
    expect(screen.getByRole("radio", { name: /在线识别/ })).toBeDisabled();
    expect(screen.getByText("缺少凭据")).toBeInTheDocument();
    await user.type(screen.getByLabelText("内容链接（每行一个）"), "https://a.test/x?z=1{enter}https://b.test/y");
    await user.click(screen.getByRole("button", { name: "添加 2 个链接" }));
    await waitFor(() => expect(submit).toHaveBeenCalledWith({
      urls: ["https://a.test/x?z=1", "https://b.test/y"], mode: "auto", audio: null,
      audio_format: "mp3", speed_factor: 1, timeout: 1800, asr_backend: "auto",
      local_timestamp_mode: "vad", local_diarization: true, local_speaker_count: 0,
    }));
  });

  it("从 txt 文件追加内容并保持用户触碰后的后端选择", async () => {
    render(<ImportModal capabilities={capabilities} online submitting={false} error={null} onClose={() => {}} onSubmit={async () => {}} onViewTasks={() => {}} />);
    const user = userEvent.setup();
    await user.click(screen.getByRole("radio", { name: /^本地识别/ }));
    const file = new File(["https://file.test/a\r\n\r\nhttps://file.test/b"], "urls.txt", { type: "text/plain" });
    Object.defineProperty(file, "text", { value: async () => "https://file.test/a\r\n\r\nhttps://file.test/b" });
    await user.upload(screen.getByLabelText("从文件添加"), file);
    await waitFor(() => expect(screen.getByText(/2 个有效链接/)).toBeInTheDocument());
    expect(screen.getByRole("radio", { name: /^本地识别/ })).toBeChecked();
  });

  it("文件读取失败时显示表单错误", async () => {
    render(<ImportModal capabilities={capabilities} online submitting={false} error={null} onClose={() => {}} onSubmit={async () => {}} onViewTasks={() => {}} />);
    const file = new File(["x"], "urls.csv", { type: "text/csv" });
    Object.defineProperty(file, "text", { value: async () => { throw new Error("read failed"); } });
    await userEvent.upload(screen.getByLabelText("从文件添加"), file);
    expect(await screen.findByRole("alert")).toHaveTextContent("无法读取所选文件");
    expect(screen.getByRole("button", { name: /^添加 .*个链接$/ })).toBeDisabled();
  });

  it("管理初始焦点、Escape 和焦点恢复", async () => {
    const opener = document.createElement("button"); document.body.append(opener); opener.focus();
    const close = vi.fn();
    const view = render(<ImportModal capabilities={capabilities} online submitting={false} error={null} onClose={close} onSubmit={async () => {}} onViewTasks={() => {}} />);
    expect(screen.getByLabelText("内容链接（每行一个）")).toHaveFocus();
    screen.getByRole("button", { name: "高级选项" }).focus();
    await userEvent.tab();
    expect(screen.getByRole("button", { name: "关闭添加内容" })).toHaveFocus();
    await userEvent.tab({ shift: true });
    expect(screen.getByRole("button", { name: "高级选项" })).toHaveFocus();
    await userEvent.keyboard("{Escape}"); expect(close).toHaveBeenCalled();
    view.unmount(); expect(opener).toHaveFocus(); opener.remove();
  });
});

function job(status: ImportJob["status"], overrides: Partial<ImportJob> = {}): ImportJob {
  return { id: "j1", batch_id: "b1", position: 1, url: "https://a.test/", requested_mode: "auto", effective_mode: "download", asr_backend: null, status, stage: status, message: null, progress: null, queue_position: null, eta_seconds: null, attempt: 1, created_at: "", updated_at: "", started_at: null, completed_at: null, item_id: null, item_path: null, error: null, warning: null, quality: null, duplicate: null, can_cancel: false, can_retry: false, can_reindex: false, is_active: ["queued", "running", "pending_index"].includes(status), ...overrides };
}

describe("useImports 状态转换", () => {
  it("历史成功不导航，观察到的活跃任务完成后才通知", async () => {
    apiMocks.capabilities.mockResolvedValue(capabilities);
    apiMocks.list.mockResolvedValueOnce({ items: [job("succeeded", { id: "old", item_id: "old-item" }), job("queued")], total: 2, page: 1, page_size: 20, active_count: 1, counts: { ...zeroCounts, queued: 1, succeeded: 1 } });
    const succeeded = vi.fn();
    const { result } = renderHook(() => useImports(succeeded));
    await waitFor(() => expect(result.current.online).toBe(true));
    expect(succeeded).not.toHaveBeenCalled();
    apiMocks.list.mockResolvedValue({ items: [job("succeeded", { item_id: "new-item" })], total: 1, page: 1, page_size: 20, active_count: 0, counts: { ...zeroCounts, succeeded: 1 } });
    await act(() => result.current.reload());
    expect(succeeded).toHaveBeenCalledWith("new-item");
  });

  it("批次失败项重试后立即合并新排队任务", async () => {
    apiMocks.capabilities.mockResolvedValue(capabilities);
    const failed = job("failed", { can_retry: true });
    apiMocks.list.mockImplementation(async () => apiMocks.retryFailed.mock.calls.length
      ? { items: [queued], total: 1, page: 1, page_size: 20, active_count: 1, counts: { ...zeroCounts, queued: 1 } }
      : { items: [failed], total: 1, page: 1, page_size: 20, active_count: 0, counts: { ...zeroCounts, failed: 1 } });
    apiMocks.create.mockResolvedValue({ id: "b1", status: "failed", total: 1, active_count: 0, counts: { failed: 1 }, created_at: "", updated_at: "", items: [failed] });
    const queued = job("queued", { id: "j2", attempt: 2 });
    apiMocks.retryFailed.mockResolvedValue({ id: "b1", status: "queued", total: 1, active_count: 1, counts: { queued: 1 }, created_at: "", updated_at: "", items: [queued] });
    apiMocks.batch.mockImplementation(async () => apiMocks.retryFailed.mock.calls.length
      ? { id: "b1", status: "queued", total: 1, active_count: 1, counts: { queued: 1 }, created_at: "", updated_at: "", items: [queued] }
      : { id: "b1", status: "failed", total: 1, active_count: 0, counts: { failed: 1 }, created_at: "", updated_at: "", items: [failed] });
    const { result } = renderHook(() => useImports(vi.fn()));
    await waitFor(() => expect(result.current.jobs).toEqual([failed]));
    await act(() => result.current.submit({ urls: ["https://a.test/"], mode: "auto", audio: null, audio_format: "mp3", speed_factor: 1, timeout: 1800, asr_backend: "auto", local_timestamp_mode: "vad", local_diarization: true, local_speaker_count: 0 }));
    await waitFor(() => expect(result.current.batch?.id).toBe("b1"));
    await act(() => result.current.retryFailed(result.current.batch!));
    expect(result.current.jobs[0]).toEqual(queued);
    expect(result.current.activeJobs).toBe(1);
    expect(result.current.error).toBeNull();
  });

  it("使用跨分页 active_count 而不是状态 counts 计算活动任务数", async () => {
    apiMocks.capabilities.mockResolvedValue(capabilities);
    const visible = Array.from({ length: 20 }, (_, index) => job("queued", { id: `j${index}` }));
    apiMocks.list.mockResolvedValue({
      items: visible,
      total: 22,
      page: 1,
      page_size: 20,
      active_count: 21,
      counts: { ...zeroCounts, queued: 20, running: 1, pending_index: 1 },
    });
    const { result } = renderHook(() => useImports(vi.fn()));
    await waitFor(() => expect(result.current.jobs).toHaveLength(20));
    expect(result.current.activeJobs).toBe(21);
    expect(result.current.total).toBe(22);
  });

  it.each([
    "批次中没有可重试任务",
    "导入服务请求失败（500）",
    "导入服务未连接",
  ])("批次重试失败保持 rejection 并公开错误：%s", async (message) => {
    apiMocks.capabilities.mockResolvedValue(capabilities);
    const failed = job("failed", { can_retry: true });
    const target = { id: "b1", status: "failed" as const, total: 1, active_count: 0, counts: { failed: 1 }, created_at: "", updated_at: "", items: [failed] };
    apiMocks.list.mockResolvedValue({ items: [failed], total: 1, page: 1, page_size: 20, active_count: 0, counts: { ...zeroCounts, failed: 1 } });
    apiMocks.retryFailed.mockRejectedValue(new Error(message));
    const { result } = renderHook(() => useImports(vi.fn()));
    await waitFor(() => expect(result.current.online).toBe(true));
    await act(async () => {
      await expect(result.current.retryFailed(target)).rejects.toThrow(message);
    });
    expect(result.current.error).toBe(message);
    expect(result.current.submitting).toBe(false);
  });

  it("批次重试成功会清除先前错误并合并任务", async () => {
    apiMocks.capabilities.mockResolvedValue(capabilities);
    const failed = job("failed", { can_retry: true });
    const target = { id: "b1", status: "failed" as const, total: 1, active_count: 0, counts: { failed: 1 }, created_at: "", updated_at: "", items: [failed] };
    const queued = job("queued", { attempt: 2 });
    const updated = { ...target, status: "queued" as const, active_count: 1, counts: { queued: 1 }, items: [queued] };
    apiMocks.list.mockImplementation(async () => apiMocks.retryFailed.mock.calls.length > 1
      ? { items: [queued], total: 1, page: 1, page_size: 20, active_count: 1, counts: { ...zeroCounts, queued: 1 } }
      : { items: [failed], total: 1, page: 1, page_size: 20, active_count: 0, counts: { ...zeroCounts, failed: 1 } });
    apiMocks.retryFailed.mockRejectedValueOnce(new Error("导入服务未连接")).mockResolvedValueOnce(updated);
    const { result } = renderHook(() => useImports(vi.fn()));
    await waitFor(() => expect(result.current.online).toBe(true));
    await act(async () => { await expect(result.current.retryFailed(target)).rejects.toThrow(); });
    expect(result.current.error).toBe("导入服务未连接");
    await act(() => result.current.retryFailed(target));
    expect(result.current.error).toBeNull();
    expect(result.current.jobs[0]).toEqual(queued);
  });
});
