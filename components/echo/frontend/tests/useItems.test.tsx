import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useItems } from "../src/hooks/useItems";
import type { ItemSummary } from "../src/api/types";

const mocks = vi.hoisted(() => ({
  listItems: vi.fn(),
  rescanLibrary: vi.fn(),
  switchLibrary: vi.fn(),
}));

vi.mock("../src/api/items", () => mocks);

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((complete) => {
    resolve = complete;
  });
  return { promise, resolve };
}

function item(id: string, title: string): ItemSummary {
  return {
    id,
    folder_name: id,
    source_type: "article",
    has_article: true,
    has_transcript: false,
    has_media: false,
    updated_at: "2026-08-12T00:00:00Z",
    source_title: title,
    source_author: null,
    published_at: null,
  };
}

const oldItem = item("old", "旧库");
const newItem = item("new", "新库");

beforeEach(() => {
  mocks.listItems.mockReset();
  mocks.rescanLibrary.mockReset();
  mocks.switchLibrary.mockReset();
});

describe("useItems library generations", () => {
  it("clears old-library items while a switch is pending", async () => {
    const pendingSwitch = deferred<{
      library_root: string;
      item_count: number;
      invalid_item_count: number;
    }>();
    mocks.listItems
      .mockResolvedValueOnce({ items: [oldItem] })
      .mockResolvedValueOnce({ items: [newItem] });
    mocks.switchLibrary.mockReturnValue(pendingSwitch.promise);
    const { result } = renderHook(() => useItems());
    await waitFor(() => expect(result.current.items).toEqual([oldItem]));

    let switchPromise!: ReturnType<typeof result.current.switchRoot>;
    act(() => {
      switchPromise = result.current.switchRoot("C:\\new-library");
    });
    expect(result.current.items).toEqual([]);
    expect(result.current.loading).toBe(true);

    pendingSwitch.resolve({
      library_root: "C:\\new-library",
      item_count: 1,
      invalid_item_count: 0,
    });
    await act(async () => {
      await switchPromise;
    });
    expect(result.current.items).toEqual([newItem]);
  });

  it("ignores an old-library response that arrives after a successful switch", async () => {
    const oldResponse = deferred<{ items: typeof oldItem[] }>();
    mocks.listItems
      .mockReturnValueOnce(oldResponse.promise)
      .mockResolvedValueOnce({ items: [newItem] });
    mocks.switchLibrary.mockResolvedValue({
      library_root: "C:\\new-library",
      item_count: 1,
      invalid_item_count: 0,
    });
    const { result } = renderHook(() => useItems());

    await act(async () => {
      await result.current.switchRoot("C:\\new-library");
    });
    expect(result.current.items).toEqual([newItem]);

    await act(async () => {
      oldResponse.resolve({ items: [oldItem] });
      await oldResponse.promise;
    });

    await waitFor(() => expect(result.current.items).toEqual([newItem]));
  });

  it("clears old-library items when the switched-library reload fails", async () => {
    mocks.listItems
      .mockResolvedValueOnce({ items: [oldItem] })
      .mockRejectedValueOnce(new Error("新库加载失败"));
    mocks.switchLibrary.mockResolvedValue({
      library_root: "C:\\new-library",
      item_count: 0,
      invalid_item_count: 0,
    });
    const { result } = renderHook(() => useItems());
    await waitFor(() => expect(result.current.items).toEqual([oldItem]));

    await act(async () => {
      await result.current.switchRoot("C:\\new-library");
    });

    expect(result.current.items).toEqual([]);
    expect(result.current.error).toBe("新库加载失败");
    expect(result.current.loading).toBe(false);
  });
});
