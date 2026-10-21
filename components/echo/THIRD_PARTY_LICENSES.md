# 第三方许可证说明

本项目使用以下开源依赖，全部为宽松许可证（MIT 或同类）。许可证原文链接如下，正式发布前请再次核对实际依赖版本的版权声明。

## 前端

| 项目 | 许可证 | 说明 |
|---|---|---|
| React | MIT | UI 库，https://github.com/facebook/react/blob/main/LICENSE |
| React DOM | MIT | React 渲染器，https://github.com/facebook/react/blob/main/LICENSE |
| Vite | MIT | 构建工具，https://github.com/vitejs/vite/blob/main/LICENSE |
| React Router | MIT | 路由，https://github.com/remix-run/react-router/blob/main/LICENSE |
| react-markdown | MIT | Markdown 渲染，https://github.com/remarkjs/react-markdown/blob/main/license |
| remark-gfm | MIT | GFM 语法扩展，https://github.com/remarkjs/remark-gfm/blob/main/license |
| CodeMirror 6 (@codemirror/*) | MIT | 编辑器，https://github.com/codemirror/codemirror5/blob/master/LICENSE |
| Lucide React | ISC | 图标库，https://github.com/lucide-icons/lucide/blob/main/LICENSE |
| unist-util-visit | MIT | AST 遍历，https://github.com/syntax-tree/unist-util-visit/blob/main/license |
| Vitest | MIT | 单元测试，https://github.com/vitest-dev/vitest/blob/main/LICENSE |
| Testing Library | MIT | 组件测试，https://github.com/testing-library/react-testing-library/blob/main/LICENSE |
| Playwright | Apache-2.0 | 浏览器集成测试，https://github.com/microsoft/playwright/blob/main/LICENSE |
| TypeScript | Apache-2.0 | 类型系统，https://github.com/microsoft/TypeScript/blob/main/LICENSE.txt |

## 后端

| 项目 | 许可证 | 说明 |
|---|---|---|
| Axum | MIT | Web 框架，https://github.com/tokio-rs/axum/blob/main/LICENSE |
| Tokio | MIT | 异步运行时，https://github.com/tokio-rs/tokio/blob/master/LICENSE |
| Serde / serde_json | MIT | 序列化，https://github.com/serde-rs/serde/blob/master/LICENSE-MIT |
| pulldown-cmark | MIT | Markdown 解析，https://github.com/pulldown-cmark/pulldown-cmark/blob/master/LICENSE |
| BLAKE3 | CC0-1.0 / Apache-2.0 | 内容哈希，https://github.com/BLAKE3-team/BLAKE3/blob/master/LICENSE |
| tempfile | MIT | 临时文件，https://github.com/Stebalien/tempfile/blob/master/LICENSE |
| tracing | MIT | 日志，https://github.com/tokio-rs/tracing/blob/master/LICENSE |
| mime_guess | MIT | MIME 推断，https://github.com/abonander/mime_guess/blob/master/LICENSE |
| uuid | Apache-2.0 / MIT | 唯一 ID，https://github.com/uuid-rs/uuid/blob/main/LICENSE-APACHE |
| chrono | MIT / Apache-2.0 | 时间，https://github.com/chronotope/chrono/blob/main/LICENSE.txt |
| reqwest | MIT / Apache-2.0 | 本机导入服务反向代理与集成测试 HTTP 客户端，https://github.com/seanmonstar/reqwest/blob/master/LICENSE-MIT |

## 说明

- 依赖的传递依赖（如 hyper、http-body-util、matchit 等）同样为宽松许可证，具体以 `Cargo.lock` 与 `bun.lock` 中锁定的版本为准。
- 本项目不使用任何需要单独商用授权的组件。
