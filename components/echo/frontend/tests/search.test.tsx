import { afterEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { SearchPage } from "../src/components/search/SearchPage";

const status = {
  index_path: "/cache/x.sqlite",
  schema_version: "1",
  indexed_items: 2,
  library_items: 2,
  chunks: 9,
  stale_items: [],
  missing_items: [],
  orphan_items: [],
  up_to_date: true,
};

function json(body: unknown, init?: ResponseInit) {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init,
  });
}

function renderAt(url: string) {
  return render(
    <MemoryRouter initialEntries={[url]}>
      <Routes>
        <Route path="/search" element={<SearchPage />} />
        <Route path="/items/:itemId" element={<p>item page</p>} />
      </Routes>
    </MemoryRouter>,
  );
}

afterEach(() => vi.unstubAllGlobals());

describe("SearchPage", () => {
  it("groups hits per item and links to the item", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/api/v1/search/status")) return json(status);
      return json({
        query: "播放器",
        effective_query: "播放器",
        hits: [
          { item_id: "id-b", folder_name: "b", title: "第二篇", kind: "article", locator: "1", start_ms: null, snippet: "讲的是播放器和字幕", score: -2 },
          { item_id: "id-b", folder_name: "b", title: "第二篇", kind: "transcript", locator: "s1", start_ms: 65000, snippet: "播放器字幕", score: -1 },
          { item_id: "id-a", folder_name: "a", title: "第一篇", kind: "ocr", locator: "images/1.png", start_ms: null, snippet: "图里的播放器", score: -0.5 },
        ],
      });
    });
    vi.stubGlobal("fetch", fetchMock);
    renderAt("/search?q=播放器");

    expect(await screen.findByText("第二篇")).toBeInTheDocument();
    expect(screen.getByText("讲的是播放器和字幕")).toBeInTheDocument();
    expect(screen.getByText("转录 01:05")).toBeInTheDocument();
    expect(screen.getByText("图片文字")).toBeInTheDocument();
    expect(screen.getAllByRole("link").map((a) => a.textContent)).toEqual(["第二篇", "第一篇"]);
    expect(
      fetchMock.mock.calls.some(([url]) => String(url).includes("q=%E6%92%AD%E6%94%BE%E5%99%A8")),
    ).toBe(true);
    await screen.findByText(/已索引 2 \/ 2 条/);

    await userEvent.click(screen.getByRole("link", { name: "第二篇" }));
    expect(await screen.findByText("item page")).toBeInTheDocument();
  });

  it("submits queries, toggles question mode and rebuilds the index", async () => {
    const calls: string[] = [];
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      calls.push(`${init?.method ?? "GET"} ${url}`);
      if (url.startsWith("/api/v1/search/status")) return json({ ...status, up_to_date: false, stale_items: ["id-a"] });
      if (url.startsWith("/api/v1/search/rebuild")) {
        return json({ rebuilt: { added: 2, updated: 0, removed: 0, unchanged: 0 }, status });
      }
      return json({ query: "x", effective_query: "x", hits: [] });
    }));
    renderAt("/search");
    expect(await screen.findByText(/有更新/)).toBeInTheDocument();

    await userEvent.click(screen.getByLabelText("问句模式"));
    await userEvent.type(screen.getByLabelText("搜索内容"), "哪些文章讲到银杏？");
    await userEvent.click(screen.getByRole("button", { name: "搜索" }));
    expect(await screen.findByText("没有找到匹配的内容")).toBeInTheDocument();
    expect(calls.some((c) => c.includes("mode=question") && c.includes("/api/v1/search?"))).toBe(true);

    await userEvent.click(screen.getByRole("button", { name: /重建索引/ }));
    await waitFor(() => expect(calls).toContain("POST /api/v1/search/rebuild"));
    expect(await screen.findByText("已索引 2 / 2 条")).toBeInTheDocument();
  });
});
