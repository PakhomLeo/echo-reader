# Echo Reader

Echo Reader 是一个本机 Windows 内容导入与阅读应用：MediaLab 把 URL 生产为标准资料条目，
Echo 扫描、阅读和编辑条目，`src/echo_reader` 负责持久批次、进程生命周期和两者协调。
`LIBRARY_ROOT` 下的文件是数据面，Echo `/api/v1` 是控制面；两个组件不相互导入内部代码。

## 安装版

安装版由一个 Python 宿主和一个 Rust 网关进程组成。Rust 在动态 loopback 端口上同源托管
React 和全部公开 API；私有 Python 导入服务另用动态 loopback 端口，只由 Rust 按精确路由
代理。安装目录只放可替换程序；资料库在 Windows Documents 已知文件夹，状态、模型、配置、
日志和临时文件在 `%LOCALAPPDATA%\EchoReader` 下。

因 Chrome 扩展的 SessionBridge 合同固定为 `127.0.0.1:19876`，应用是每台机器单实例；
再次启动会打开已运行实例。关闭浏览器标签不会停止应用；侧栏“退出 Echo Reader”仅在
无活动导入时接受退出。宿主用每次启动随机 URL 建立浏览器 session，未持有 session 的其他
本机进程不能直接读取公开 API 或资料库文件。安装、持久路径、浏览器授权和选择性迁移见
[安装与本机运行](docs/installation.md)。

## 源码开发

```powershell
cd <repo-root>
mise install
.\scripts\setup.ps1
.\scripts\run-reader.ps1
```

开发时也可单独启动 Echo backend、Vite 和 import service。完整命令、包构建和验证见
[开发指南](docs/development.md)。源码模式默认使用仓库 `content/`、`.runtime/` 和 `config/`；
安装版不依赖或修改源码 checkout。

## 现役合同

- 一个输入 URL 形成一个稳定 item；发布是 staging 快照和可回滚目录切换。
- 导入批次、job 和时间线在 `imports.db` schema v2 持久化；成功产物只保存资料库相对
  `item_path`，不持久安装机器的绝对路径。
- 资料库切换会拒绝活动导入和与 state/cache/config/logs/tmp 重叠的目录，并对 Echo
  切换与 `app.json` 持久化做失败回滚。
- 页面和资源请求按跳验证 HTTPS/域名，每跳预解析公网 IP 并固定到该次传输；
  安全 DNS 固定与代理不能同时使用，会明确失败。
- 在线 ASR 在质量门禁通过前不发布；本地模型以 revision + 文件摘要 manifest 验证，
  用跨进程锁避免并发下载，重启后复用已验证快照。

详细边界见[文档权威索引](docs/README.md)和[当前交付文档](docs/HANDOFF.md)。带日期报告是当时的
实测证据，不自动成为现役架构说明。

## 许可边界

本仓库和安装包当前只用于私人本机验证。组合项目没有仓库级许可证，包内 FFmpeg
构建也带来 GPL 义务。[THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 保留第三方声明，不授予
组合项目的再分发权；未完成项目许可选择和分发合规前，不得公开发布 installer 或
standalone zip。
