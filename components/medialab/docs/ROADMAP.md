# medialab 视频下载会话问题：整合方案与开发计划（ROADMAP）

> 状态：阶段计划与研究历史，P0–P4 的问题、选择和验收证据全部保留。当前跨组件状态与开放项
> 以 [Echo Reader HANDOFF v5](../../../docs/HANDOFF.md) 为准；不要把本文件早期“待办”自动当作
> 当前未完成项。
> P0–P4 中的 Cookie 文件、Chrome fetch/render 与 XHS 专用实现按原文保留作为研究证据；v3 已
> 删除这些现役路径，统一使用通用 Broker 与 Python Chrome146 网络栈。

> 本文件整合三方面信息：
> 1. **medialab 现状**（本仓库：下载/转写/refine 已完备，YouTube 卡在会话信任）
> 2. **外部方案研究**（Browser Session Bridge 架构草案）
> 3. **我们自己的实测经验**（LESSONS.md：IP 信任 ≠ 会话信任、PO Token 绑定关系、换 IP 唯一解、无痕导出法）

---

## 1. 问题本质（已实证）

| 现象 | 根因 |
|---|---|
| 同 IP：Chrome 能播、yt-dlp 报 "Sign in to confirm you're not a bot" | **信任的是"会话"，不是"IP"**：Chrome 有长期访客会话/Visitor Data/播放器上下文，yt-dlp 裸请求是全新匿名客户端 |
| 导出的 cookies.txt 很快失效 | Google 轮换**打开状态**的账号 Cookie（时间戳不可信，服务端撤销） |
| bgutil PO Token 服务无效 | 错误发生在 Player 请求阶段（会话不受信任），GVS Token 根本没机会用；且 Token 绑定 Visitor/会话/视频 ID，与 yt-dlp 的会话不匹配即废 |
| 换 IP 后可能恢复 | IP 层面封锁（登录墙）只能换 IP；会话层面不信任要靠"受信会话"解决 |

**结论**：手动导出 Cookie 是可用但烦人的下限；PO Token 服务是无效的弯路；正解是 **"借用用户当前受信 Chrome 会话"**。

## 2. 目标架构：Browser Session Bridge

```
用户提交 URL
   ↓
本地服务（medialab DownloadService 扩展）
   ├── ① 匿名 yt-dlp 尝试（普通公开视频首选，不用账号）
   ├── ② 需要登录 → SessionProvider.get_session(url, platform, job_id)
   │        ├── Cookie Broker：插件即时读取 Chrome 当前 Cookie → 临时 Netscape Jar → yt-dlp
   │        └── （YouTube 失败升级）Player Context Broker：后台标签页初始化播放器
   │              → 取同一会话的 Cookie+VisitorData+Player/GVS Token → Session Bundle → yt-dlp
   └── ③ yt-dlp/FFmpeg 完成实际下载（插件永不传视频数据）
```

职责边界（关键设计原则）：
- **本地服务**：任务调度、失败分类、策略升级、临时文件生命周期
- **Chrome 插件**：只提供"当前受信会话"（Cookie / Visitor Data / Token），必要时短暂渲染播放器
- **yt-dlp/FFmpeg**：解析、下载、合并（GB 级传输绝不过插件）
- **SessionProvider 抽象**：下载器只依赖接口，不关心会话来自插件/浏览器直读/手动文件

## 3. 分阶段开发计划

### P0（已完成，无需开发）
- 现状：下载/转写/refine 完备；YouTube 走"每次手动无痕导出 + 12h 过时提醒"（`youtube_refresh_hours`）
- 兜底手段保留：手动导出法在任何阶段都可用

### P1：Cookie Broker MVP（零插件，先验证）— ✅ 已完成（2026-08）
- **做法**：`yt-dlp --cookies-from-browser chrome/edge/firefox`（yt-dlp 内置直读浏览器 Cookie）
- medialab 落地：`[download].cookie_source` 配置（`browser:chrome` / `browser:chrome:ProfileName` / 静态文件路径 / 空=现状）；`downloader.parse_cookie_source` 解析；锁库错误包装为可操作提示（auth 分类）
- **实测结论（重要）**：
  - Chrome 运行中读取 Cookie 库**间歇性失败**（SQLite WAL 允许并发读，仅写事务瞬间锁库）——多数时候浏览器开着也能读，失败时给出明确提示
  - `yt-dlp-ChromeCookieUnlock` 插件**可用**（兼容 yt-dlp 2026.7.4，已 pip 安装）但机制是 **Restart Manager 强杀持有锁的浏览器进程**——指向用户主 profile 会强杀用户 Chrome，**默认不启用**，仅配合专用 profile 作为 opt-in
  - 隔离测试验证：插件解锁只杀持有目标 profile 锁的进程，其他 Chrome 实例不受影响
- **验收**：B站/抖音等平台 `cookie_source="browser:chrome"` 免手动导出即可下载（实测通过）
- 风险：需处理浏览器锁库（已有清晰错误）；Chrome 应用绑定加密（app-bound encryption）可能使部分环境读不到——错误会走同一个提示路径

### P2：Chrome 插件 Cookie Broker（通用平台解决）
- **P2a 已完成（含联调）**：
  - `browser-extension/`：MV3 插件（Cookie Broker + popup 四态状态 UI + alarms keepalive + 异常回传）
  - `bridge.py`：本地 SessionBridgeServer（扩展 Origin + 每连接内存会话鉴权、请求转发、ping 探测、超时处理）
  - `session.py`：SessionProvider 体系 + `ExtensionSessionProvider`；downloader 匿名优先 + auth 升级
  - `examples/session_bridge_demo.py`：一键联调（扩展自动连接，无用户连接配置）
  - ✅ **端到端验证通过**：B站、抖音均通过 Chrome 扩展会话完成下载（全程免手动导出）
  - 白名单授权：扩展内置平台白名单（bilibili/douyin/youtube 域名），popup 一键授权全部，白名单可编辑/恢复默认
  - 联调中发现并修复：MV3 SW 挂起断连（alarms）、origin pattern 需带路径、临时 cookie 误入产物扫描目录、分类裸词误判（LESSONS 15-19）
- **P2b 已完成（代码）**：平台适配器 `session_domains` 可配置化（`[platforms].session_domains` toml 覆盖，Q10）
- 剩余：正式 Native Messaging 迁移（Q4，正式打包时）
- 插件能力：`chrome.cookies` 读取指定域 Cookie（含 HttpOnly/分区）→ 本地服务转 Netscape Jar
- 权限最小化：`optional_host_permissions`，首访域名才申请；不申请 `<all_urls>`
- 通信：`localhost WebSocket`（仅 127.0.0.1）+ `chrome-extension://` Origin 普通网页门禁；每连接下发内存 session token，断线失效/重连轮换，无用户连接配置。Origin 不抵御恶意本地 raw socket 伪造，loopback 是必要信任边界
- medialab：`SessionProvider` 接口（`get_session(url, platform, job_id) -> BrowserSession`）+ 临时文件清理
- **验收**：10+ 平台免手动导出；任务结束临时 cookie 即删

### P3：YouTube Session Bridge
- **P3a ✅ 环境就绪**：Deno 2.9.4 + `yt-dlp-ejs 0.8.0`（`[jsc] JS Challenge Providers: deno` 确认）；YouTube 匿名公开视频成功率基线待干净 IP 下补测
- **P3b ✅ 核心闭环完成并实测通过**：
  - 扩展 `mint_yt_token`：后台标签页（`world: MAIN`）读 `window.ytcfg` 的 INNERTUBE 上下文 → `visitor_data` + 尽力 WebPoClient PO Token
  - `bridge.request_mint` 多客户端路由（role=pot 外部客户端转发）
  - `BrowserSession.visitor_data/po_token` → `--extractor-args youtube:visitor_data=...` 注入
  - yt-dlp POT provider 插件：`src/yt_dlp_plugins/extractor/medialab_bridge.py`（**必须放 extractor/ 目录**——extractor 插件规范启动时加载并执行 @register_provider；同时需要 yt-dlp-getpot 框架？不需要——原生 @register_provider 即可）
  - 🎉 **实测**：被登录墙拦截的 qM4AWORRT48 在扩展会话（cookie+visitor_data）下**下载成功**——"Sign in to confirm" 绕过
  - PO Token 为尽力而为（本次未生成也成功，印证 Q15 最小闭环：cookie+visitor_data 足够多数场景）
- **遗留**：Q4 Native Messaging 正式化；PO Token 按需生成链路（GVS/Player 403 场景）待遇真实 403 时验证；干净 IP 下匿名成功率基线

### P4（可选扩展）：文章抓取 — ✅ 已完成（2026-08，grill-me 评审后实施）
- **通用站点**：`svc.submit(url)` 统一入口 + `router.py` 域名规则表分派；非小红书由 `ArticleExtractor` 保留直连 → Chrome fetch → Chrome render 升级链及原重试规则。
- **小红书现役合同**：扩展只用 `request_session` 导出当前 Chrome profile cookies+UA，不发目标站请求。进程内 Broker 缓存快照，每任务独立 `curl_cffi` 0.16 Session（`impersonate="chrome"` 近似 Chrome 146，不是精确 Chrome 151），页面 GET 一次并同 HTML 分流；图文每个去重图片各 GET 一次，视频唯一 stream GET 一次。无 HEAD/probe/retry/fallback。仅页面明确 401/登录跳转 generation 去重刷新；资源 401、403/CAPTCHA/429/network 不刷新，当前 URL 不重试，新快照只给下一任务；成功任务只提交最终 `XhsClient` 的 cookie jar，Broker 校验域并以 generation+revision CAS 回写内存，不落盘。
- **升级链**：直连（httpx）→ 插件 `fetch_page`（SW 认证请求）→ 插件 `render_page`（后台标签页 + vendored Readability）；验证码保持标签页轮询等用户（CAPTCHA_REQUIRED 超时）
- **现役产物**：图文 `article.md + images/ + manifest.json`，视频 `media/ + manifest.json`。
- **扩展侧**：复用无配置自动连接的同一 bridge，新增 fetch_page/render_page/render_poll/render_close 消息；白名单扩容 5 家文章域名；vender `readability.js`（Apache-2.0）
- **适配器**：wechat（#js_content）/ zhihu（js-initialData hydration）/ toutiao（_SSR_HYDRATED_DATA）/ x（oEmbed 免登录优先 + meta 兜底）/ xhs（__INITIAL_STATE__，需 raw_dom）
- 与 P2 插件共用会话与权限架构；历史 P4 交付时曾记录 119 用例，不代表当前收集数。

## 5. grill-me 评审结论（已确认决策）

### 公共策略
- **Q7 下载策略**：匿名优先（Level 0），`[download].session_first_platforms = ["bilibili", "douyin", "youtube"]` 名单内平台默认直接带会话；其余平台 auth 失败才升级
- **Q8 升级触发**：仅 auth 类错误（`classify_error` auth）触发"带 Cookie 重试"，每任务最多升级一次；网络类走既有退避，403 留给平台适配器级处理
- **Q9 会话接口**：`SessionProvider.get_session(url, platform, job_id) -> BrowserSession(cookie_file, headers, proxy, expires_at)` 挂 `DownloadService(session_provider=...)`；临时文件放 `tmp/{task_id}/session.cookies`，任务 finally 清理；P1 的 browser 字符串模式作为 `BrowserCookieProvider` 内置实现共存
- **Q10 域名粒度**：platforms.py 升级为平台适配器（`match_domains` 识别 + `session_domains` 授权读取），插件按会话域集合读取 Cookie，权限精确到域
- **Q16 错误分类**：结构化 `FailureCode` 枚举（AUTH_REQUIRED/COOKIE_ROTATED/JS_RUNTIME_MISSING/EJS_UNAVAILABLE/PO_PROVIDER_UNAVAILABLE/PO_TOKEN_REJECTED/MEDIA_403/RATE_LIMITED/DRM_PROTECTED/NETWORK_MISMATCH/FORMAT_UNAVAILABLE/UNSUPPORTED/UNKNOWN），`classify_error` 返回精确 code，`tasks.error_code` 4 类经 `legacy_code` 映射；NETWORK_MISMATCH 优先实现
- **Q17 安全模型**：URL 校验仅 https/拒 localhost/私有 IP/云 metadata；WebSocket 仅 127.0.0.1，以扩展 Origin 阻止普通网页并轮换每连接内存 session token；Origin 不是恶意本地 raw socket 下的密码学证明；临时文件用后即删；`role=pot` 由服务进程内部自动鉴权

### P2（Chrome 插件 Cookie Broker）
- **Q11 技术栈**：Manifest V3 + 原生 JS，仓库 `browser-extension/`，开发者模式加载 unpacked；TS/Vite 后置
- **Q12 里程碑**：P2a（插件骨架 + Cookie Broker + WebSocket + 临时文件 + auth 升级闭环）→ P2b（平台适配器注册表 + 授权 UI + 撤销）
- **Q4 通信**：验证期 localhost WebSocket → 正式版 Native Messaging（协议与传输解耦）

### P3（YouTube Session Bridge）
- **Q2 范围**：YouTube 独立 P3 阶段（通用平台先行）
- **Q13 路径**：P3a（干净 IP + 匿名 + Deno/EJS 验证公开视频，情报验证）→ P3b（自研 extension-backed POT Provider，复用 P2 插件后台标签页；不动用现成 WPC——新浏览器=新访客会话，已被实测证实无效）
- **Q14 环境**：换 Deno + `yt-dlp-ejs`（PyPI `yt-dlp[default]` 自带匹配版本，替代 GitHub 远程组件）
- **Q15 Bundle 范围**：第一版最小闭环 = Cookie + Visitor Data（`--extractor-args youtube:visitor_data=...`）；Player/GVS Token 按错误分类按需生成
- **Q18 顺序**：P3a 与 P2a 并行（P3a 不依赖插件）；P2b/P3b 殿后

### 验收标准
- P2a：session_first 平台插件会话全流程下载成功；匿名平台 auth 失败自动升级成功；任务结束临时文件确认删除
- P2b：平台表可配置、授权可撤销、新平台零插件发版
- P3a：干净 IP 下 YouTube 公开视频匿名下载成功率基线
- P3b：登录内容 + 流级 403 场景经 extension-backed provider 可下载

## 6. 后续待办

1. **P3 补测**（待用户）：换真正不同的出口 IP 后，测 YouTube 匿名公开视频成功率基线（deno/EJS 就绪）
2. **Q4 Native Messaging 正式化**（正式打包时）：替换 WebSocket 传输层（协议不变）
3. **PO Token 按需生成链路**：遇 GVS/Player 403 时经扩展铸造 token（当前 mint 已就绪，WebPoClient 参数需按 YouTube 版本调优）
4. **P4 验证与维护**：X oEmbed 与头条风控场景仍待真机验证；小红书第二条图文已成功，第一条修复 HTTP CDN URL 规范化后仍待用户明确重提；站点适配器随改版维护。

## 7. 全库架构审查与修复（2026-08，独立于 P 系列）

外部审查报告 12 项发现全部属实并已修复。当时记录 134 用例全绿；这是历史数字，当前测试数以本文末尾实测为准。

## 8. 并行化改造（2026-08，独立于 P 系列）

按 8 原则审查 + 多链接并行诉求完成阶段级并发改造。当时记录 134 用例全绿；这是历史数字。

## 9. 当前验证基线

自动化验证运行 `mise exec '--' uv run pytest components/medialab/tests -q`；最终测试数以新鲜命令结果为准，不在历史计划中硬编码。小红书真实 E2E 当前为一条成功、一条修复后待复测，不能假报双帖完成。
