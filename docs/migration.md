# 迁移记录

## 来源

本仓库在 2026-08-10 从以下本地项目建立：

- `<original-medialab-dir>`
- `<original-echo-dir>`

来源目录保持原位；新项目不通过符号链接或运行配置引用它们。

## 复制范围

Echo 按其 Git 跟踪清单复制，共 131 个文件、3,182,956 bytes，包括 Rust/React 源码、测试、
`Cargo.lock`、`bun.lock`、文档、示例资料库和第三方许可说明。2026-08-10 对原目录重新核验的
来源指纹为：

```text
SHA-256 tree fingerprint: e871bba2a1f17a8d14cf163d3a631db202c39b39af89ee0a4ae796645ea131db
```

MediaLab 复制 66 个源码文件、585,230 bytes，包括 Python 包、测试、浏览器扩展、示例、项目文档、
配置模板和项目规则。2026-08-10 对原目录 allowlist 重新核验的来源指纹为：

```text
SHA-256 tree fingerprint: cdb94872c91616fe2898894a4ffbdd5d98f19f49681b085244d46f3422746f4c
```

指纹按“相对路径 + 文件长度 + 每文件 SHA-256”排序后再次计算 SHA-256，用于确认来源目录在本轮
开发后仍未被写入。早期迁移记录使用过另一套未固定的树摘要算法；本节以有文件数、字节数和可复现
输入格式的复核结果为准。建仓后的唯一组件级整理见下节。

## 建仓时的组件级整理（迁移基线）

在初始复制完成的基线中，`components/echo/.gitignore` 做了一个必要的非运行时代码调整：

- 将原来的 GBK 文本统一为 UTF-8。
- 将 `library/` 改为根锚定的 `/library/`。

原规则在一个全新的父级 Git 仓库里会把 `backend/src/library/*.rs` 也当成用户资料库忽略，导致六个 Rust 源文件不能正常纳入版本控制。锚定后仍忽略 Echo 顶层真实 `library/`，但不再误伤后端源码。初始基线中，除这一个 ignore 文件外，Echo 其余 130 个来源文件与原项目逐字节一致；MediaLab 的 66 个来源文件全部一致。

此结论只描述“刚复制完成”的迁移基线。此后 Echo Reader 在新目录中继续开发了前端、XHS、
SessionBridge、集成服务、测试和文档；这些变化只发生在新仓库，不再与来源文件逐字节一致。来源
目录未被写入的最终复核仍由上一节两个来源指纹证明，演进过程见
[2026-08-10 开发记录](history/2026-08-10-development-record.md)。

## 明确排除

以下内容属于运行状态、凭据或可重建产物，没有复制：

- 子项目 `.git` 元数据。
- `.venv`、`.pytest_cache`、`__pycache__`、`.pyc`。
- `node_modules`、`frontend/dist`、Rust `backend/target`、Playwright 报告。
- Echo 的真实 `library/`；其内容约 2.95 GB，属于用户资料。
- MediaLab 的 `delivery/`、`tmp/`、`out/`、bench 产物和 SQLite/WAL 文件。
- MediaLab 的真实 `config/secrets.toml` 与平台 Cookie。

这些内容既不影响源码完整性，也不应进入新仓库。Echo 的小型 `library.example/` 和 MediaLab 的 `secrets.toml.example` 已保留，供开发与配置参考。

## 新增内容

根级工具配置、Python 集成层、共享资料库占位、启动/验证脚本和项目级文档是 Echo Reader 新增的组合层，不会反向同步到两个来源项目。
