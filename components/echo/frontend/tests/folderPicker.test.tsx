import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FolderPickerModal } from "../src/components/sidebar/FolderPickerModal";
import { browseDirectory } from "../src/api/items";

vi.mock("../src/api/items", () => ({
  browseDirectory: vi.fn(),
}));

const mockBrowse = vi.mocked(browseDirectory);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

beforeEach(() => {
  mockBrowse.mockReset();
});

describe("FolderPickerModal", () => {
  it("打开时列出磁盘根，选择按钮在根路径下禁用", async () => {
    mockBrowse.mockResolvedValueOnce({
      path: "",
      parent: null,
      entries: [{ name: "C:\\", is_dir: true }],
    });
    const onClose = vi.fn();
    const onSelect = vi.fn();
    render(<FolderPickerModal onClose={onClose} onSelect={onSelect} />);

    await waitFor(() => expect(screen.getByText("C:\\")).toBeInTheDocument());
    expect(screen.getByText("本机磁盘")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择此文件夹" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "↑ 上一级" })).toBeDisabled();
  });

  it("点击目录进入并显示子项；文件行不可点击", async () => {
    mockBrowse
      .mockResolvedValueOnce({
        path: "",
        parent: null,
        entries: [{ name: "C:\\", is_dir: true }],
      })
      .mockResolvedValueOnce({
        path: "C:\\data",
        parent: "C:\\",
        entries: [
          { name: "subdir", is_dir: true },
          { name: "readme.txt", is_dir: false },
        ],
      });
    const onSelect = vi.fn();
    render(<FolderPickerModal onClose={vi.fn()} onSelect={onSelect} />);

    await waitFor(() => expect(screen.getByText("C:\\")).toBeInTheDocument());
    await userEvent.click(screen.getByText("C:\\"));

    await waitFor(() => expect(screen.getByText("subdir")).toBeInTheDocument());
    expect(screen.getByText("readme.txt")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "选择此文件夹" })).toBeEnabled();
    expect(screen.getByRole("button", { name: "↑ 上一级" })).toBeEnabled();
  });

  it("选择此文件夹回调选中路径", async () => {
    mockBrowse.mockResolvedValue({
      path: "C:\\data",
      parent: "C:\\",
      entries: [{ name: "subdir", is_dir: true }],
    });
    const onSelect = vi.fn().mockResolvedValue(undefined);
    render(<FolderPickerModal onClose={vi.fn()} onSelect={onSelect} />);

    await waitFor(() => expect(screen.getByText("subdir")).toBeInTheDocument());
    await userEvent.click(screen.getByRole("button", { name: "选择此文件夹" }));

    await waitFor(() => expect(onSelect).toHaveBeenCalledWith("C:\\data"));
  });

  it("切换进行中锁定关闭和目录导航", async () => {
    mockBrowse.mockResolvedValue({
      path: "C:\\data",
      parent: "C:\\",
      entries: [{ name: "subdir", is_dir: true }],
    });
    const pending = deferred<void>();
    render(
      <FolderPickerModal
        onClose={vi.fn()}
        onSelect={() => pending.promise}
      />,
    );
    await waitFor(() => expect(screen.getByText("subdir")).toBeInTheDocument());

    await userEvent.click(screen.getByRole("button", { name: "选择此文件夹" }));

    expect(screen.getByRole("dialog", { name: "选择文件夹" })).toHaveAttribute(
      "aria-busy",
      "true",
    );
    expect(screen.getByRole("button", { name: "关闭" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "取消" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "↑ 上一级" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "subdir" })).toBeDisabled();

    pending.resolve();
    await waitFor(() =>
      expect(screen.getByRole("button", { name: "关闭" })).toBeEnabled(),
    );
  });

  it("浏览失败显示错误", async () => {
    mockBrowse
      .mockResolvedValueOnce({
        path: "",
        parent: null,
        entries: [{ name: "C:\\", is_dir: true }],
      })
      .mockRejectedValueOnce(new Error("文件夹不存在或无法访问"));
    render(<FolderPickerModal onClose={vi.fn()} onSelect={vi.fn()} />);

    await waitFor(() => expect(screen.getByText("C:\\")).toBeInTheDocument());
    await userEvent.click(screen.getByText("C:\\"));
    await waitFor(() => expect(screen.getByText("文件夹不存在或无法访问")).toBeInTheDocument());
  });
});
