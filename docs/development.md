# 开发指南

## 环境

```powershell
cd <repo-root>
mise install
.\scripts\setup.ps1
```

仓库元数据固定工具版本。用 mise 运行任务、uv 管理 Python workspace、Bun 管理前端、Cargo
管理 Rust。不提交 `.venv`、`node_modules`、模型、资料库、密钥或构建输出。

本地 ASR 固定使用 Intel OpenVINO `GPU`，不静默退回 CPU。可在下载模型前检查：

```powershell
mise exec -- uv run --extra local-asr python -c "import openvino as ov; print(ov.Core().available_devices)"
```

## 运行

最接近安装版的源码入口：

```powershell
.\scripts\run-reader.ps1
```

组件开发可用三终端：

```powershell
.\scripts\run-backend.ps1
.\scripts\run-frontend.ps1
.\scripts\run-import-service.ps1
```

此形态保留固定开发端口：Vite 代理 Echo API，前端开发配置代理 import API。安装版不使用
这些公开固定端口，而是 Rust 单 origin + 私有 Python 动态端口。源码模式默认资料库为
`content/`，运行状态为 `.runtime/`。

组件单独运行：

```powershell
cd components\echo\backend
cargo run

cd ..\frontend
bun run dev

cd <repo-root>
uv run --package medialab python components\medialab\examples\host_demo.py <url>
```

## 验证

代码修改的仓库门禁：

```powershell
.\scripts\verify.ps1
.\scripts\verify.ps1 -E2E
```

`-E2E` 显式启用 Playwright，平台真实网络验证不在 mock/仓库门禁中。

## 构建 Windows 包

```powershell
.\scripts\build-package.ps1 -Version 0.1.0
```

脚本构建 React、Rust 网关、冻结 Python 宿主、工具与许可素材，生成 standalone zip，并在可用
Inno Setup 时生成每用户 installer。`-SkipInstaller` 只跳过 installer 编译，不把 standalone
程序束变成另一套架构。

构建在冻结前扫描 MediaLab package source，并在组装后再次扫描 bundle；任何真实
`secrets.toml`、`.env`、私钥/证书密钥或 Cookie 凭据文件都会使构建失败，模板文件不受影响。
不要把本机凭据放进 Python package source tree。

`build-package.ps1` 会调用：

```powershell
.\scripts\test-package.ps1 -BundleRoot <bundle-directory>
```

包烟测仅在仓库 `out/.runtime` 下创建隔离根，验证冻结 worker spawn、动态 loopback 启动、
AppPaths 注入、重启持久、空闲 shutdown、SessionBridge 端口释放以及宿主强杀后 Rust watchdog。
若 `127.0.0.1:19876` 已占用，烟测 fail closed，不会停止占用进程。

公开分发 standalone 或 installer 前需满足的第三方许可义务（FFmpeg GPLv3 等）见
[安装文档](installation.md#打包形式与许可)。

## 文档归属

跨组件现役合同写在 `docs/`；MediaLab 细节写在 `components/medialab/docs/ARCHITECTURE.md`；
Echo API 写在 `components/echo/docs/api.md`。带日期报告和旧计划是不可改写的历史证据。
