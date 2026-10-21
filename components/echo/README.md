# Echo

本地混合内容阅读、播放、转录校对与 Markdown 编辑工具。

左侧导航浏览资料库，右侧统一阅读页面展示 AI 整理的文章、可播放的音视频、与播放时间同步的时间线转录，并支持直接修改文章和转录文字。

## 技术栈

- 前端：React + TypeScript + Vite + React Router + react-markdown + CodeMirror 6
- 后端：Rust + Axum + Tokio + serde + pulldown-cmark + BLAKE3（媒体 Range 与静态托管自实现）
- 数据：本地文件系统（无数据库）

## 环境要求

使用 [mise](https://mise.jdx.dev) 管理工具链，项目根目录的 `mise.toml` 声明：

- rust 1.97.1
- bun 1.3.14

```powershell
mise install
```

## 资料库

资料库根目录通过环境变量指定：

```powershell
$env:LIBRARY_ROOT = "D:\path\to\library"
```

未设置时回落到组合项目的示例资料库 `<repo-root>\content`（即仓库根目录的
`content/`，含可直接用于开发调试的示例数据）。

每条信息对应资料库根目录下一个一级子文件夹，格式见 [docs/file-format.md](docs/file-format.md)。
资料库可同时包含文章、视频、音频和 `mixed` 混合条目；混合条目可在主媒体之外声明多个
附加媒体，前端提供独立的“混合”筛选和附加媒体播放器。

## 运行

开发模式（两个终端）：

```powershell
# 后端 http://127.0.0.1:3000
cd backend
cargo run

# 前端 http://127.0.0.1:5173（/api 代理到后端）
cd frontend
bun install
bun run dev
```

生产模式：先构建前端，再由 Rust 服务托管同一来源：

```powershell
cd frontend
bun run build
cd ../backend
cargo run --release
# 打开 http://127.0.0.1:3000
```

## 质量门

```powershell
# 后端
cd backend
cargo fmt --check
cargo clippy --all-targets -- -D warnings
cargo test

# 前端
cd frontend
bun run typecheck
bun run lint
bun run test          # Vitest 单元测试
bunx playwright test  # 浏览器集成测试（使用系统 Edge，需先构建后端 exe）
```

Playwright 集成测试前需要先构建后端可执行文件：

```powershell
cd backend
cargo build
```

注意：Playwright 会**独立启动**测试后端（`reuseExistingServer: false`，指向临时测试资料库），不会复用 3000 端口上正在运行的后端；测试结束后需重启开发后端。

## 文档

- [Echo Reader 当前交付文档](../../docs/HANDOFF.md)：组合项目当前状态、运行、验证与开放项
- [历史交付文档（HANDOFF）](docs/HANDOFF.md)：原 Echo 独立组件的设计决策、经验总结与功能指南
- [内容生产指南（给内容处理 AI）](docs/producer-guide.md)：抓取/下载/转录结果如何编排成资料库文件夹
- [架构说明](docs/architecture.md)
- [API 契约](docs/api.md)
- [资料库文件格式](docs/file-format.md)
- [历史项目计划书](PROJECT_PLAN.md)：原 standalone Echo 的冻结需求与阶段计划

## License

MIT – see [LICENSE](LICENSE). Third-party notices: [THIRD_PARTY_NOTICES.md](../../THIRD_PARTY_NOTICES.md).
