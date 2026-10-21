# 2026-08-10 开发与问题解决记录

> 类型：历史记录，不是当前运行手册。当前结论见 [交付文档 v4](../HANDOFF.md)。

本记录把本轮散落在多个旧 HANDOFF、ROADMAP、LESSONS 和会话中的事实按时间线整理。原文档仍
保留；这里的目的只是让后续协作者能从“现象 → 原因 → 解决 → 验证”快速定位。

## 阶段一：建立新仓库而不碰来源

**需求**：把 `<original-medialab-dir>` 与 `<original-echo-dir>` 组合成新项目，但保留独立组件且不能改原目录。

**问题**：原树包含 GB 级 `.git`、Rust target、node_modules、真实媒体资料库、虚拟环境、数据库和
Cookie。直接递归复制会膨胀、泄密并把运行状态当源码。

**解决**：Echo 按 131 个 Git tracked 文件复制；MediaLab 按 66 个源码/文档/测试白名单复制；排除
真实 library、delivery/tmp/out、数据库、依赖和构建物。根建 uv workspace、mise 工具链、启动脚本、
集成层和统一 ignore。两个来源目录后来用独立 tree hash 再次确认未被修改。

早期 `migration.md` 曾记录未固定输入格式的树摘要 `ABF92B...532621`（Echo）与
`2B018B...DF16`（MediaLab）。收口时发现摘要算法不可复现，改用“相对路径 + 文件长度 + 每文件
SHA-256”的确定性清单重新核验，得到 Echo `e871bb...131db` 与 MediaLab `cdb948...6f4c`。旧值在
这里保留作为问题经过，现役完整凭据以 [迁移记录](../migration.md) 为准。

## 阶段二：保持组件独立并建立最小连接

**问题**：MediaLab 是进程内 Python 库，Echo 是 Rust/React 应用，两者没有现成网络集成；前端也
没有可调用的内容导入 API。

**解决**：共享 `LIBRARY_ROOT` 作为数据面；根 `ImportCoordinator` 调 MediaLab public API，成功后
POST Echo rescan；FastAPI 单 worker 队列把同一能力提供给前端。Echo 离线时保留已完成内容并报告
warning，不做跨进程全局回滚。

## 阶段三：阅读器前端重构

**参考**：Bear 的三栏与聚焦、Readwise Reader 的捕获入口、Inoreader/Reeder 的类型筛选与持续播放、
Feedly 的稳定导航。

**问题**：原 UI 只有窄侧栏和阅读详情；条目增多后无法扫描，导入没有入口，标题层级和媒体首屏占用
也不合理。

**解决**：实现导航/内容列表/阅读器三栏，搜索与类型筛选，onboarding、加载/空/错误状态，独立导入
抽屉与任务进度；小屏逐级聚焦；重组标题/meta/toolbar，视频采用 16:9/500px，正文 720px，保留
sticky/mini player，修复转录编辑 flex。

## 阶段四：小红书重复请求与登录问题

**现象**：其他平台正常，小红书连续使用后出现验证码/掉登录；旧链路会先抓一次判断视频，图文再走
ArticleExtractor fetch/render，图片和视频又由 Python 用另一套会话下载。

**原因**：单任务重复页面请求、client/网络画像切换、宽泛重试和渲染 fallback 会增加风控暴露；同时
“403/验证码”被错误等同为 Cookie 失效。

**第一轮解决**：把小红书改成单次 capture，同一 HTML 同时判断图文/视频；删除 request_session +
第二次页面 fetch/render fallback，图片/视频按 URL 单次请求，任何必需资源失败就整任务失败。

## 阶段五：从“让 Chrome 代下”转为 Python 近似浏览器

开发中曾实现 Chrome downloads/DNR 路线，让当前 Chrome 直接下载资源。用户随后明确纠正：目标不是
由 Chrome 发请求，而是 Python 伪装相近版本并复用浏览器凭证。因此旧 download_resource、DNR 权限、
ChromeResourceDownloader 路径被完整删除，没有兼容层。

现役方案采用维护中的 `curl_cffi` 0.16：每任务独立 Chrome preset Session，同一 Session 完成页面和
全部资源。它提供版本级 TLS/HTTP 画像，但不是本机浏览器全部 Canvas/WebGL/字体等指纹；文档明确
不夸大。

## 阶段六：凭证复用与保守刷新

**需求**：Cookie/凭证获取一次后一直使用，明确失效才重新连接插件。

**解决**：`XhsCredentialBroker` 在服务生命周期内缓存 Cookie+UA，不设 TTL、不落盘。generation 负责
single-flight refresh，revision 负责成功任务 Set-Cookie CAS。只有页面 401 或同域登录跳转触发刷新，
且当前任务不重试；403、资源失败、验证码、429 与网络错误不刷新。

## 阶段七：自动 SessionBridge 配对

**旧问题**：扩展 0.1.0 要用户复制配对 token，服务/扩展版本不同步时会一直显示“未配置”。

**解决**：0.2.0 扩展固定连 loopback 19876；服务校验 extension Origin 后下发每连接内存 token，所有
消息绑定 token+req_id，断线轮换。手动 token 参数、环境变量、popup 输入/复制/更换 UI 和 storage
路径全部删除。POT 保留独立内部 token，因为它仍是现役 yt-dlp provider，不是兼容层。

**验收教训**：Chrome 扩展卡片显示路径和版本比截图文案更可靠。用户最初截图仍是 0.1.0；从新项目
目录重载 0.2.0 后才进入正确自动连接链。

## 阶段八：两条真实帖子验证

### 帖子一：`6a758af100000000280078ab`

- 只提交一次，页面读取成功。
- 图片地址由 XHS HTML 给成 `http://` CDN URL，严格 HTTPS 检查在 Python 发资源请求前拒绝。
- 修复：`normalize_xhs_resource_url` 对 XHS 自有 `http://` 与 protocol-relative URL 统一升级 HTTPS，
  视频和图片共享同一 helper，之后仍执行授权域与私网安全校验。
- 按用户的“不要反复请求”要求，修复后没有自动重跑，所以该帖仍未验证成功。

### 帖子二：`6a562f0c000000001003cecf`

- 单次提交成功，标题与正文生成，6 张 WebP 均下载。
- 新问题：图片文件存在但 `article.md` 没有本地图片引用，Echo 因而不显示图片。
- 修复：XHS 严格路径在全部图片成功后按顺序追加 `![图片 N](images/NNNN.ext)`；手工修复本次已有
  产物并通过 Echo API 验证 6 个资产 200。

## 阶段九：自动配对与文档收口后的目录治理

全仓测试发现 `ExtensionSessionProvider` 的单测直接写 `tmp/session-job1.cookies`，绕过
`DownloadService` 的任务 finally 清理。生产 Provider 也忽略了传入 `paths.temp`。

解决：Provider 现在优先解析配置的 temp 目录；测试给它独立 `TemporaryDirectory`。旧 80-byte 文件
在完整质量门开始前位于 `tmp/session-job1.cookies`；后续 downloader fixture 正好把这个顶层文件当作
下载产物移动为 `out/session-job1.cookies`，进一步验证了原设计风险。文件内容没有读取；完整收口
汇报后，用户明确确认清场，该文件随精确 `out` 白名单目标删除。

清场执行前重新确认 backend/import/bridge 的端口、命令行、父子关系与绝对可执行路径，再停止对应
进程树。只删除 18 个预览过且互不嵌套的 ignored 目标，共 24,244 个可重建文件、
2,960,600,569 bytes；没有运行 broad `git clean -X`。清场后 3000、3100、3101、19876 均为空，
缓存、DB、Cookie、依赖和构建残留扫描为零；真实 library、示例库、配置、源码、文档、锁文件、
`.git` 和两个来源目录保持不变。由于构建环境也按确认删除，清场后不重复跑测试；本轮测试凭据来自
清场前的完整质量门，下一次验证先执行 `scripts/setup.ps1`。

## 问题到代码的快速映射

| 问题 | 现役解法 | 关键位置 |
|---|---|---|
| 重复抓取/多 client | 单页面 + 同任务 Session | `service.py`, `xhs_client.py` |
| HTTP CDN URL | XHS URL 规范化再校验 | `site_adapters.py`, `xhs_video.py` |
| Cookie 频繁获取 | no-TTL Broker + generation/revision CAS | `session.py` |
| 手动 token | Origin gate + 每连接内存 token | `bridge.py`, `background.js` |
| 图片下载但不可见 | 成功后写 Markdown 本地引用 | `article.py` |
| 测试 Cookie 残留 | Provider 使用配置 temp；测试临时目录 | `session.py`, `test_bridge.py` |
| 导入成功但 Echo 离线 | soft rescan warning / strict option | `integration.py`, `echo_api.py` |
| 前端缺捕获流程 | 本地 import API + drawer + job hook | `server.py`, Echo frontend imports 模块 |

更早的细粒度经验不要从这里推断或删减，继续查阅 MediaLab
[LESSONS](../../components/medialab/docs/LESSONS.md)、[DECISIONS](../../components/medialab/docs/DECISIONS.md)
以及 Echo [历史 HANDOFF](../../components/echo/docs/HANDOFF.md)。

## 阶段十：把 XHS 优化推广到全部平台

**新需求**：Cookie/其他会话凭证不应只有小红书缓存；Python Chrome 近似画像也要覆盖视频、转写
下载和全部文章平台。

**依赖发现**：当时 XHS 使用 curl-cffi 0.16，但 yt-dlp 2026.7.4 的官方 curl handler 明确只接受
0.10–0.15。继续保留 0.16 会得到“XHS 能用、其他平台实际没有 handler”的假通用化。

**最终选择**：锁 `curl-cffi==0.15.0` 与 Chrome146。文章由任务级 `BrowserHttpClient` 使用一个
Session 请求页面和资源；yt-dlp 用 `ImpersonateTarget("chrome", "146")`。扩展只提供 Cookie/UA
与 YouTube mint，不代发内容。

**凭据状态**：把 `XhsCredentialBroker` 重写为按 `(platform, domains)` 分区的通用
`CredentialBroker`。Echo import service 在进程生命周期共享它；empty Cookie 同样缓存；明确 auth
才 generation refresh；成功 jar 用 generation/revision CAS 回写。静态 Cookie、直接读 Chrome DB、
扩展 fetch/render、Readability 与 XHS-only constructor 全部删除，不留 fallback/兼容别名。

**平台同步**：视频 detector/router/extractor/session domains/扩展白名单同步覆盖 B站、抖音、
YouTube、X、TikTok、Vimeo、Twitch、Instagram、Facebook；默认文章域也给微信、知乎、头条、
小红书、Medium、掘金、少数派、36Kr、博客园、InfoQ、简书稳定平台名和会话域。

**计数口径**：文章和 XHS 的“一次”是应用层调用；redirect 仍可能有多次 wire 请求。yt-dlp 保留
成熟的 transport/fragment retry、风险退避与格式降档，通用化只统一网络画像和凭据生命周期。

**文档治理**：现役说明集中到根 HANDOFF v3、MediaLab ARCHITECTURE 与扩展 README。旧组件
HANDOFF/DELIVERY/ROADMAP/DECISIONS/LESSONS 原文保留并加历史标记，避免把失败路线忘掉，也避免
下一位 AI 把旧路线误当当前代码。
