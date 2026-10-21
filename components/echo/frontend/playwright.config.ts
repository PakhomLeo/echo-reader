import { defineConfig } from "@playwright/test";
import * as path from "node:path";
import * as fs from "node:fs";
import { fileURLToPath } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));

// 测试资料库：从 library.example 复制，避免污染示例库
const testLibrary = path.resolve(here, ".tmp-library");
if (fs.existsSync(testLibrary)) {
  fs.rmSync(testLibrary, { recursive: true, force: true });
}
fs.cpSync(path.resolve(here, "../library.example"), testLibrary, { recursive: true });

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 60_000,
  expect: { timeout: 10_000 },
  workers: 1,
  use: {
    baseURL: "http://127.0.0.1:5173",
    channel: "msedge",
    headless: true,
  },
  webServer: [
    {
      command: "bun run dev",
      url: "http://127.0.0.1:5173",
      reuseExistingServer: true,
      timeout: 30_000,
    },
    {
      // 后端必须由 Playwright 用测试资料库独立启动，禁止复用外部进程（避免污染示例库）
      command: "..\\backend\\target\\debug\\echo-backend.exe",
      url: "http://127.0.0.1:3000/api/v1/health",
      reuseExistingServer: false,
      timeout: 15_000,
      env: { LIBRARY_ROOT: testLibrary },
    },
  ],
});
