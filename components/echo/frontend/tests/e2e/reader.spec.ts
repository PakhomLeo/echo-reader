import { test, expect, type Page } from "@playwright/test";

const ITEM_URL = "/items/01J4R7ZQ6YEPNXQFSX3B9S8A2V";

async function openItem(page: Page) {
  await page.goto(ITEM_URL);
  await expect(page.locator("h1.document-title")).toHaveText("example-item");
}

async function waitVideoReady(page: Page) {
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return !!v && v.readyState >= 1;
  });
}

test("完整阅读流程：播放、切转录、跟随、跳转、编辑、滚动恢复", async ({ page }) => {
  // 1. 打开带视频的文章
  await openItem(page);
  await waitVideoReady(page);

  const video = page.locator("video");

  // 2. 开始播放
  await page.locator(".media-play-button").first().click();
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return !!v && !v.paused;
  });

  // 跳到确定位置，等待播放推进
  await page.evaluate(() => {
    const v = document.querySelector("video")!;
    v.currentTime = 2.0;
  });
  await page.waitForTimeout(500);
  const timeBefore = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  expect(timeBefore).toBeGreaterThan(1.5);

  // 记录已加载标记：切换模式后 loadeddata 不应再次触发
  await page.evaluate(() => {
    const v = document.querySelector("video")!;
    const w = window as unknown as { __loadCount?: number };
    w.__loadCount = 0;
    v.addEventListener("loadeddata", () => {
      w.__loadCount = (w.__loadCount ?? 0) + 1;
    });
  });

  // 3. 从文章切换到转录
  await page.getByRole("tab", { name: "时间线文本" }).click();

  // 4-6. 视频不能重新加载；播放状态保持；时间继续前进（偏差 < 0.2s 粒度）
  await page.waitForTimeout(800);
  const timeAfter = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  const paused = await video.evaluate((v: HTMLVideoElement) => v.paused);
  const loadCount = await page.evaluate(
    () => (window as unknown as { __loadCount?: number }).__loadCount ?? 0,
  );
  expect(loadCount).toBe(0);
  expect(paused).toBe(false);
  expect(timeAfter).toBeGreaterThan(timeBefore - 0.2);

  // 7. 开启跟随高亮
  await page.getByRole("button", { name: "跟随高亮" }).click();

  // 8. 当前段落正确高亮（暂停后 seek 到 segment_000003: 4200-6400ms，seek 完成触发一次 timeupdate）
  await page.evaluate(() => {
    const v = document.querySelector("video")!;
    v.pause();
    v.currentTime = 4.3;
  });
  await expect(page.locator(".transcript-segment-active")).toHaveAttribute(
    "data-segment-id",
    "segment_000003",
  );

  // 9. 点击时间戳跳转并播放
  const secondTimestamp = page.locator(".transcript-timestamp").nth(1);
  await secondTimestamp.click();
  await page.waitForTimeout(300);
  const jumped = await video.evaluate((v: HTMLVideoElement) => v.currentTime);
  expect(Math.abs(jumped - 2.0)).toBeLessThan(0.5);
  expect(await video.evaluate((v: HTMLVideoElement) => v.paused)).toBe(false);

  // 10. 编辑并保存转录
  await page.getByRole("button", { name: "编辑转录" }).click();
  const firstTextarea = page.locator(".transcript-textarea").first();
  await firstTextarea.fill("这是被修改的第一段转录文字。");
  await page.getByRole("button", { name: "保存修改" }).click();
  await expect(page.getByText("已保存")).toBeVisible();
  await expect(page.locator(".transcript-segment").first()).toContainText("这是被修改的第一段转录文字");

  // 11. 切回文章模式
  // 先滚动文章到某位置（记录），用于第 12 步恢复验证
  await page.getByRole("tab", { name: "文章" }).click();
  const scroller = page.locator(".document-page-scroller");
  await scroller.evaluate((el: HTMLElement) => {
    el.scrollTop = 600;
  });
  await page.waitForTimeout(200);
  const articleScroll = await scroller.evaluate((el: HTMLElement) => el.scrollTop);
  expect(articleScroll).toBeGreaterThan(400);

  // 切到转录再切回文章，滚动位置应恢复
  await page.getByRole("tab", { name: "时间线文本" }).click();
  await page.getByRole("tab", { name: "文章" }).click();
  await page.waitForTimeout(200);
  const restored = await scroller.evaluate((el: HTMLElement) => el.scrollTop);
  expect(Math.abs(restored - articleScroll)).toBeLessThan(50);
});

test("视频仅播放时置顶；音频有底部迷你播放栏与模式切换", async ({ page }) => {
  // 视频项：播放中卡片 sticky，暂停后不 sticky
  await page.goto(ITEM_URL);
  await expect(page.locator("h1.document-title")).toHaveText("example-item");
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return !!v && v.readyState >= 1;
  });

  const mediaCard = page.locator(".media-card");
  // 初始暂停：不置顶
  await expect(mediaCard).not.toHaveClass(/media-card-sticky/);

  await page.locator(".media-play-button").first().click();
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return !!v && !v.paused;
  });
  await expect(mediaCard).toHaveClass(/media-card-sticky/);

  await page.locator(".media-play-button").first().click();
  await page.waitForFunction(() => {
    const v = document.querySelector("video");
    return !!v && v.paused;
  });
  await expect(mediaCard).not.toHaveClass(/media-card-sticky/);

  // 音频项：有文章/时间线切换、迷你播放栏可播放
  await page.goto("/items/01J4R7ZQ6YEPNXQFSX3B9S8A2X");
  await expect(page.locator("h1.document-title")).toHaveText("podcast-item");
  await expect(page.getByRole("tab", { name: "文章" })).toBeVisible();
  await expect(page.getByRole("tab", { name: "时间线文本" })).toBeVisible();

  const miniPlayer = page.locator(".mini-player");
  await expect(miniPlayer).toBeVisible();
  await page.waitForFunction(() => {
    const a = document.querySelector("audio");
    return !!a && a.readyState >= 1;
  });
  await page.locator(".mini-player .media-play-button").click();
  await page.waitForFunction(() => {
    const a = document.querySelector("audio");
    return !!a && !a.paused;
  });
  await expect(miniPlayer).toBeVisible();

  // 切到时间线文本，播放器（迷你栏）依然存在
  await page.getByRole("tab", { name: "时间线文本" }).click();
  await expect(page.locator(".mini-player")).toBeVisible();
});
