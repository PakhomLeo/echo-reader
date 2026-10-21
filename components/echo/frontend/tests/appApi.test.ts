import { beforeEach, describe, expect, it, vi } from "vitest";
import { getAppConfig, shutdownApp, switchLibrary } from "../src/api/items";

describe("宿主管理 API", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("使用同源精确路径和请求体", async () => {
    const fetchMock = vi.spyOn(globalThis, "fetch")
      .mockResolvedValueOnce(new Response(JSON.stringify({ install_mode: true }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ library_root: "C:/Library", item_count: 2, invalid_item_count: 0 }), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ status: "shutting_down" }), { status: 200 }));

    await getAppConfig();
    await switchLibrary("C:\\Library");
    await shutdownApp();

    expect(fetchMock.mock.calls[0][0]).toBe("/api/v1/app-config");
    expect(fetchMock.mock.calls[1]).toEqual(["/api/v1/library/switch", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path: "C:\\Library" }),
    }]);
    expect(fetchMock.mock.calls[2]).toEqual(["/api/v1/app/shutdown", { method: "POST" }]);
  });

  it("显示宿主409结构化 detail 消息", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(new Response(JSON.stringify({
      detail: { code: "imports_active", message: "仍有导入任务进行中" },
    }), { status: 409 }));
    await expect(shutdownApp()).rejects.toMatchObject({
      status: 409,
      code: "imports_active",
      message: "仍有导入任务进行中",
    });
  });
});
