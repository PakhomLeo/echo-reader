# Echo Reader 本机版

从桌面或开始菜单打开 **Echo Reader**。应用会在本机动态端口启动并打开默认浏览器；关闭浏览器标签页
不会停止后台，侧栏的“退出 Echo Reader”会在没有进行中任务时安全退出。再次点击快捷方式会打开已经
运行的实例，不会启动第二套数据库或模型进程。

快捷方式先打开每次启动随机生成的 session URL，随后浏览器只保存 session-only、HttpOnly
Cookie。未持有该 session 的其他本机进程不能直接读取应用 API、资料库媒体或页面；退出应用后
session 自动失效。由于浏览器扩展桥固定使用 19876，同一台 Windows 机器同时只运行一个实例。

## 持久数据

安装目录只放可替换的程序文件。关闭、升级和普通卸载都不会删除以下用户数据：

- 默认资料库：`%USERPROFILE%\Documents\Echo Reader Library`（也可在界面中切换）。
- 任务历史与 MediaLab 状态：`%LOCALAPPDATA%\EchoReader\state`。
- 本地 ASR 模型与 OpenVINO 缓存：`%LOCALAPPDATA%\EchoReader\cache`。
- 本机配置与密钥：`%LOCALAPPDATA%\EchoReader\config`。
- 日志与临时文件：`%LOCALAPPDATA%\EchoReader\logs`、`%LOCALAPPDATA%\EchoReader\tmp`。

如需在线 ASR，把安装目录 `config-templates\secrets.toml.example` 复制为
`%LOCALAPPDATA%\EchoReader\config\secrets.toml` 后编辑。不要把真实密钥发给他人或提交到 Git。
本地 ASR 不需要在线密钥；首次使用会下载并校验模型，后续启动复用已经验证的模型资产。

## 浏览器授权

需要登录态的平台使用随安装包提供的 Chrome 扩展。打开 `chrome://extensions`、开启开发者模式，
选择“加载已解压的扩展程序”，指向安装目录中的 `browser-extension`，然后点击扩展里的授权按钮。
扩展只连接本机 `127.0.0.1:19876`，不持久保存 Cookie 文件。

## 从源码版选择性迁移

先退出所有 Echo Reader 实例并确保旧数据库没有 WAL/SHM/journal 文件，再运行：

```powershell
& "$env:LOCALAPPDATA\Programs\Echo Reader\EchoReader.exe" `
  --migrate-from "<old-install-or-repo-dir>"
```

迁移只复制资料库、配置、两个 SQLite 数据库和本地模型；不会复制测试运行、构建输出、日志、临时
Cookie 或 OpenVINO 编译缓存，也不会修改或删除源目录。冲突会明确失败，不会覆盖不同内容。

## 许可

Echo Reader 源码以 MIT 许可证发布，许可证原文见 `LICENSE.txt`。程序束中的第三方组件
（FFmpeg LGPL-3.0 构建、PyInstaller 引导程序、Deno、OpenVINO、sherpa-onnx 等）保留各自许可证，
声明与原文见 `THIRD_PARTY_NOTICES.md` 和 `licenses\`。再分发本程序束时须一并提供这些文件。
FFmpeg 源码获取方式见 `licenses\FFMPEG_SOURCE.txt`；`tools\` 下的 FFmpeg 可执行文件和 DLL
可以替换成你自己编译的同一主版本（替换前先退出 Echo Reader，重装或升级会恢复原文件）。
