# Windows 安装与本机运行

## 启动与单实例

从开始菜单或桌面打开 Echo Reader。宿主启动一个动态 loopback Rust 网关和一个私有动态
loopback Python 导入服务，然后在默认浏览器中打开 Rust URL。关闭标签不会退出后台。

SessionBridge 固定占用 `127.0.0.1:19876`，因此同一机器只运行一个 Echo Reader。进程生命期
Windows mutex 防止第二实例；再次点击快捷方式会验证 `instance.json` 中的 PID、loopback URL
和 boot ID，然后打开已运行实例的 session 启动 URL。若 19876 被其他程序占用，启动会明确失败，不会
终止占用者。

session 启动 URL 中的随机 token 只用于一次浏览器 bootstrap；Rust 随后设置 session-only、
`HttpOnly`、`SameSite=Strict` cookie 并重定向到 `/`。公开 API、页面、媒体和静态资源都需要该
cookie；`/health` 只暴露启动状态。Rust 到私有 Python 服务使用另一个随机内部 token，浏览器
不会收到它。退出应用后两个 token 都失效。

## 持久路径

安装目录默认为 `%LOCALAPPDATA%\Programs\Echo Reader`，只包含可替换程序。升级或普通卸载
不删除用户数据：

| 数据 | 默认路径 |
|---|---|
| 资料库 | Windows Documents 已知文件夹下的 `Echo Reader Library` |
| 导入历史、MediaLab 状态、实例记录 | `%LOCALAPPDATA%\EchoReader\state` |
| 本地 ASR 模型与 OpenVINO 缓存 | `%LOCALAPPDATA%\EchoReader\cache` |
| `app.json`、`secrets.toml` | `%LOCALAPPDATA%\EchoReader\config` |
| 日志 | `%LOCALAPPDATA%\EchoReader\logs` |
| 临时文件 | `%LOCALAPPDATA%\EchoReader\tmp` |

`AppPaths` 是路径权威。界面切换资料库后，绝对路径原子保存到 `config\app.json`。
不允许选择文件系统根、不存在/不可写目录，或与 state/cache/config/logs/tmp 互相包含的
目录。有活动导入时拒绝切换；Echo 扫描、持久化或协调器初始化失败时回滚旧资料库。

## 密钥与浏览器授权

在线 ASR 需先把安装目录 `config-templates\secrets.toml.example` 复制为
`%LOCALAPPDATA%\EchoReader\config\secrets.toml` 并填写。本地 ASR 不需要这些密钥。

需要登录态的平台使用包内 `browser-extension`：在 `chrome://extensions` 开启开发者模式，
选择“加载已解压的扩展程序”并指向该目录。扩展只把授权域 Cookie 提供给
`127.0.0.1:19876`；不传输目标内容，不写 Cookie 文件到持久目录。

## 退出与异常结束

侧栏“退出 Echo Reader”调用同源 `POST /api/v1/app/shutdown`。只有无活动 job 时才返回
`202`；否则返回 `409 imports_active`。正常退出先停止接收工作，再关闭导入服务和 Rust
子进程。两条路径都有硬截止：导入服务超过宽限会将活动状态标为 `interrupted` 并终止
异常宿主；Rust 在宿主消失时通过父进程 watchdog 自行退出，必要时强制终止，避免孤儿网关。

## 从源码版选择性迁移

先退出所有 Echo Reader，并确保源 SQLite 没有 `-wal`、`-shm` 或 `-journal` 边车文件：

```powershell
& "$env:LOCALAPPDATA\Programs\Echo Reader\EchoReader.exe" `
  --migrate-from "<old-install-or-repo-dir>"
```

`--migrate-from` 只复制 `content/`、已选配置文件、`.runtime/imports.db`、
`.runtime/medialab.db` 和 `.runtime/models/local-asr`；不复制日志、临时 Cookie、测试/构建输出或
OpenVINO 编译缓存，不修改源目录。普通目标冲突会失败；唯一可被源库替换的目标 DB
是“当前正式 schema、表精确匹配、一行数据也没有、且无边车文件”的首次启动空库。新于当前
程序的源 DB schema 在修改目标前拒绝。

迁移只 staging 实际包含所选资产的持久根（另含保存 journal/marker 的 state），再通过持久
journal 切换这些目标根；启动时在创建任何应用目录前自动
恢复中断切换。无法验证的 journal 保留并 fail closed。完成标记让同一未变源可幂等重跑；
已完成后源内容变化会被拒绝。

## 打包形式与许可

standalone zip 是免安装程序束，但仍按安装模式使用 Documents/LOCALAPPDATA 持久目录，不是
把用户数据放在解压目录的 portable app。installer 在此程序束上增加每用户安装、快捷方式和
升级替换。

两种形式当前都只能私人本机验证。组合项目无仓库级许可证，包内 FFmpeg 构建带 GPL 义务；
未完成项目许可与依赖分发合规前，不得公开再分发。
