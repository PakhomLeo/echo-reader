import { describe, expect, it } from "vitest";
import { renderHook, act } from "@testing-library/react";
import { useSidebarCollapsed } from "../src/components/sidebar/useSidebarCollapsed";

describe("useSidebarCollapsed 持久化", () => {
  it("默认展开", () => {
    localStorage.clear();
    const { result } = renderHook(() => useSidebarCollapsed());
    expect(result.current.collapsed).toBe(false);
  });

  it("切换后写入 localStorage", () => {
    localStorage.clear();
    const { result } = renderHook(() => useSidebarCollapsed());
    act(() => result.current.toggle());
    expect(result.current.collapsed).toBe(true);
    expect(localStorage.getItem("reader.sidebar.collapsed")).toBe("1");
  });

  it("刷新后从 localStorage 恢复折叠", () => {
    localStorage.setItem("reader.sidebar.collapsed", "1");
    const { result } = renderHook(() => useSidebarCollapsed());
    expect(result.current.collapsed).toBe(true);
  });
});
