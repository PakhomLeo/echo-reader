import { test, expect, type Page } from "@playwright/test";
import * as path from "node:path";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

async function openPicker(page: Page) {
  await page.goto("/");
  await page.getByRole("button", { name: "选择文件夹" }).click();
  const dialog = page.getByRole("dialog", { name: "选择文件夹" });
  await expect(dialog).toBeVisible();
  return dialog;
}

test("文件夹选择：弹窗浏览到目标目录并切换资料库", async ({ page }) => {
  const dialog = await openPicker(page);

  // 打开时显示磁盘根
  await expect(dialog.getByText("本机磁盘")).toBeVisible();
  const diskRow = dialog.locator(".picker-row").first();
  await expect(diskRow).toContainText(":\\");

  // 进入盘符根，浏览到项目测试资料库目录
  await diskRow.click();
  await expect(dialog.locator(".picker-path")).not.toHaveText("本机磁盘");

  // 通过路径栏逐级进入当前复制项目的测试资料库，禁止读取原项目运行目录。
  const target = path.resolve(here, "../../.tmp-library");
  const parts = target.split("\\").slice(1);
  let current = "C:";
  for (const part of parts) {
    current = `${current}\\${part}`;
    // 点击对应目录项（图标与名称同在一个按钮，使用包含匹配）
    await dialog.locator(".picker-row", { hasText: part }).first().click();
    await expect(dialog.locator(".picker-path")).toContainText(part);
  }
  // 最终路径用 title 属性断言（文本断言受反斜杠转义干扰）
  await expect(dialog.locator(".picker-path")).toHaveAttribute("title", target);

  // 选择此文件夹：.tmp-library 含 3 个 item
  await dialog.getByRole("button", { name: "选择此文件夹" }).click();
  await expect(dialog).not.toBeVisible();

  // 侧边栏显示新库的条目
  await expect(page.locator(".sidebar-item")).toHaveCount(3);
  await expect(page.locator(".sidebar-item").first()).toContainText("example-item");
});

test("文件夹选择：取消关闭弹窗，库不变", async ({ page }) => {
  const dialog = await openPicker(page);
  await expect(dialog.getByText("本机磁盘")).toBeVisible();
  await dialog.getByRole("button", { name: "取消" }).click();
  await expect(dialog).not.toBeVisible();
  await expect(page.locator(".sidebar-item")).toHaveCount(3);
});
