import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { AppShell } from "../src/app/AppShell";

const mocks = vi.hoisted(() => ({
  switchRoot: vi.fn(),
  edit: vi.fn(),
}));

vi.mock("../src/hooks/useItems", () => ({
  useItems: () => ({
    items: [],
    loading: false,
    error: null,
    refreshing: false,
    refresh: vi.fn(),
    switchRoot: mocks.switchRoot,
  }),
}));

vi.mock("../src/hooks/useImports", () => ({
  useImports: () => ({
    activeJobs: 0,
    total: 0,
    capabilities: null,
    online: true,
    submitting: false,
    error: null,
    submit: vi.fn(),
  }),
}));

vi.mock("../src/api/items", () => ({
  getAppConfig: vi.fn().mockResolvedValue({ install_mode: false }),
  shutdownApp: vi.fn(),
}));

vi.mock("../src/components/sidebar/Sidebar", () => ({
  Sidebar: ({
    onSwitchRoot,
  }: {
    onSwitchRoot: (path: string) => Promise<unknown>;
  }) => (
    <button
      type="button"
      onClick={() => void onSwitchRoot("C:\\new-library").catch(() => undefined)}
    >
      切换资料库
    </button>
  ),
}));

vi.mock("../src/components/sidebar/ContentList", () => ({
  ContentList: ({
    query,
    filter,
    onQuery,
    onFilter,
  }: {
    query: string;
    filter: string;
    onQuery: (query: string) => void;
    onFilter: (filter: "video") => void;
  }) => (
    <div>
      <span data-testid="list-state">{`${query}:${filter}`}</span>
      <button type="button" onClick={() => onQuery("旧查询")}>设置查询</button>
      <button type="button" onClick={() => onFilter("video")}>设置筛选</button>
    </div>
  ),
}));

vi.mock("../src/components/imports/ImportModal", () => ({
  ImportModal: () => null,
}));

function renderShell() {
  return render(
    <MemoryRouter initialEntries={["/items/shared-id"]}>
      <Routes>
        <Route path="/" element={<AppShell />}>
          <Route index element={<p>资料库首页</p>} />
          <Route
            path="items/:itemId"
            element={<button type="button" onClick={mocks.edit}>编辑当前条目</button>}
          />
        </Route>
      </Routes>
    </MemoryRouter>,
  );
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

beforeEach(() => {
  mocks.switchRoot.mockReset();
  mocks.edit.mockReset();
});

describe("AppShell 资料库切换", () => {
  it("成功切换后离开可能同 ID 的旧详情页，并重置列表状态", async () => {
    mocks.switchRoot.mockResolvedValue({
      library_root: "C:\\new-library",
      item_count: 0,
      invalid_item_count: 0,
    });
    renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "设置查询" }));
    await user.click(screen.getByRole("button", { name: "设置筛选" }));
    expect(screen.getByTestId("list-state")).toHaveTextContent("旧查询:video");

    await user.click(screen.getByRole("button", { name: "切换资料库" }));

    expect(await screen.findByText("资料库首页")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑当前条目" })).not.toBeInTheDocument();
    expect(screen.getByTestId("list-state")).toHaveTextContent(":all");
    expect(mocks.edit).not.toHaveBeenCalled();
  });

  it("切换进行中立即离开旧详情页", async () => {
    const pending = deferred<{
      library_root: string;
      item_count: number;
      invalid_item_count: number;
    }>();
    mocks.switchRoot.mockReturnValue(pending.promise);
    renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "切换资料库" }));
    expect(await screen.findByText("资料库首页")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "编辑当前条目" })).not.toBeInTheDocument();

    pending.resolve({
      library_root: "C:\\new-library",
      item_count: 0,
      invalid_item_count: 0,
    });
    await waitFor(() => expect(mocks.switchRoot).toHaveBeenCalledOnce());
  });

  it("切换失败时仍离开旧详情页但保留列表筛选", async () => {
    mocks.switchRoot.mockRejectedValue(new Error("切换失败"));
    renderShell();
    const user = userEvent.setup();

    await user.click(screen.getByRole("button", { name: "设置查询" }));
    await user.click(screen.getByRole("button", { name: "设置筛选" }));
    await user.click(screen.getByRole("button", { name: "切换资料库" }));

    await waitFor(() => expect(mocks.switchRoot).toHaveBeenCalledOnce());
    expect(screen.queryByRole("button", { name: "编辑当前条目" })).not.toBeInTheDocument();
    expect(screen.getByTestId("list-state")).toHaveTextContent("旧查询:video");
    expect(screen.getByText("资料库首页")).toBeInTheDocument();
  });
});
