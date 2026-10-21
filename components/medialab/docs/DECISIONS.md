# 设计决策记录（DECISIONS）

> 状态：历史决策证据。原有结论与实测事实保留，不用它判断 Echo Reader 当前运行状态；现役架构看
> [ARCHITECTURE](ARCHITECTURE.md)，跨组件状态看
> [根 HANDOFF v5](../../../docs/HANDOFF.md)。本文保留当时的选择，即使它们后来被替换；现役网络
> 与会话决策以 `ARCHITECTURE.md` 为准。

本项目的所有架构决策通过 "grill-me" 式逐问访谈确认（一次一个问题，附推荐答案）。以下 17 项决策是最终定稿，含"为什么"。

## 决策清单

| # | 决策点 | 结论 | 为什么 |
|---|---|---|---|
| 1 | 对外形态 | **进程内 Python 库 + async API**（宿主 import 调用） | "程序内微服务"：同进程调用，无序列化/网络/认证开销；宿主是 async 框架时可直接 await |
| 2 | 平台范围 | **extractor 白名单**（bilibili/douyin/youtube/generic），配置可扩 | 服务化产品可控性 > 覆盖面；白名单外走 generic 兜底；新平台改配置即可 |
| 3 | 并发模型 | **阶段级信号量**（下载/抓取/ASR/润色各 `max_concurrent`，默认各 3，互不占槽）+ `to_thread`；ASR 阶段内部切片提交并行（`slice_concurrency` 默认 3） | yt-dlp 是阻塞库无异步 API；进程池内存贵（每进程 ~34MB）；B站/抖音高并发触发风控。**任务级信号量会卡脖子**（转写任务占槽 8-10 分钟堵死其他任务），并发池粒度匹配"资源"而非"任务" |
| 4 | 任务状态 | **SQLite（WAL）**，tasks/files/asr 三表 | 重启可查历史；sqlite3 标准库零依赖；进度写库节流 1s |
| 5 | Cookie 管理 | Netscape 格式 + `cookies/` 目录 + 配置映射 + **过期感知** | 浏览器扩展直接导出零转换；改文件即换 cookie；过期自动告警 |
| 6 | 音频实现 | **ffmpeg 子进程** + `AudioProcessor` 抽象（v2 可换 PyAV） | 功能优先；PyAV 解决的是"GPL 分发"问题而非功能问题，留到分发阶段 |
| 7 | ASR 供应商 | **可插拔**（`AsrProvider` 接口），默认 qwen filetrans | 防供应商锁定（改价/改 API）；时间戳对齐逻辑与供应商无关 |
| 8 | OSS 角色 | **仅 ASR 前置缓存**；本地永久保留（用户删才删） | 交付物本地化 + 数据库记录；OSS 只承载"送 ASR 前的音频缓存" |
| 9 | MediaLab 核心默认无网络控制面 | 核心对外无 HTTP、无端口；可选本机 SessionBridge 使用 WebSocket，扩展无配置自动连接；loopback 边界内以 Origin 阻止普通网页，并用每连接内存 session token 做消息关联 | Origin 不是恶意本地 raw socket 下的密码学身份证明，因此 bridge 仍不得向非本机暴露 |
| 10 | 打包 | **pip 本地包**（pyproject + src/ 布局，`pip install -e .`） | 依赖（yt-dlp/oss2）自动带入；宿主升级方便；ffmpeg 是唯一外部可执行依赖（探测解决） |
| 11 | 配置传递 | 默认 config.toml + **宿主 dict 覆盖** | 开箱即用（文件可调）；宿主有自己的配置体系时可传对象 |
| 12 | 重试 | **分层**：内置 10 / 风控退避 3 / 降档 1 / ASR 重提交 3 | 各层失败模式不同：503 是暂时的退避即可；高清失败可降档；ASR 失败时 OSS 缓存还在 |
| 13 | 日志/事件 | 命名 logger（不自建 handler）+ **on_event 回调** + SQLite 查询 | 不污染宿主输出；回调是推（实时）、查询是拉（宿主自控节奏） |
| 14 | 版本边界 | **v1 核心闭环（零云依赖）→ v2 ASR 管线** | v1 可独立验证；v2 依赖链长（OSS+千问），混在一起难排查 |
| 15 | 文章化 | **转写后再加一步 LLM 润色（refine）**，qwen3.7-flash + 复用 ASR key | 口语转写稿直接可读性差（口癖/碎片化）；文章是下游常见消费形态；不修改转写结果、失败不致命，独立性好 |
| 16 | 转写倍速 | **默认 1.5x**（实测 11 个视频的公共最优） | 1.5x 文本与 1x 几乎无损且省 1/3 处理时间；2x 起说话人分离塌缩（tsinghua 2→1），3x 文本退化；diarization 对混叠内容（解说+原声）在 1.5x 也不稳 |
| 17 | YouTube cookie 工作流 | **每次下载前手动重新导出**（无痕窗口法），按文件 mtime 做过时提醒（12h） | Google 轮换撤销 cookie（时间戳不可信）；被标记 IP 上任何客户端+PO Token 都过不了登录墙，**换 IP 是唯一解**；PO Token 服务只解流级 403，复杂度不值，已弃 |

> **P2/P3 浏览器会话桥的 12 项决策（Q7-Q18）**：经 grill-me 评审确认，记录在 [ROADMAP.md §5](ROADMAP.md)，包括：匿名优先 + session_first_platforms、auth 才升级、SessionProvider 接口、按域授权、WebSocket→Native Messaging 分阶段、MV3+原生 JS、Deno+yt-dlp-ejs、最小闭环（cookie+visitor_data）、结构化 FailureCode 等。**实测重要更正**：Q17 曾认为"换 IP 是唯一解"——后续实测发现**扩展铸造的 visitor_data + 当前会话 cookie 可绕过播放器登录墙**（LESSONS 23），换 IP 仅对纯匿名路径必要。

> **小红书例外决策**：上述通用视频会话升级策略不适用小红书。扩展只通过 `request_session` 导出用户当前 Chrome profile cookies 与 UA，不发目标站请求；进程内 Broker 缓存快照，每任务创建独立 `curl_cffi` 0.16 Session（`impersonate="chrome"` 近似 Chrome 146，不宣称精确 Chrome 151）。同一 Session 页面 GET 一次并同 HTML 分流，图文每个去重图片各 GET 一次或视频唯一 stream GET 一次；无 HEAD/probe/retry/fallback。仅页面明确 401/登录跳转按 generation 去重刷新，资源 401、403/CAPTCHA/429/network 不刷新，当前 URL 不重试，新快照仅供下一任务；成功任务只提交最终 `XhsClient` 的 cookie jar，Broker 校验域并以 generation+revision CAS 回写内存。完整合同见 [ARCHITECTURE.md §6.11](ARCHITECTURE.md#611-小红书单次会话合同)。

> **P4 文章抓取的 10 项决策（Q1-Q12）**：记录在 [ROADMAP.md P4 段](ROADMAP.md)，包括：submit 统一入口 + 域名规则表分派（默认文章）、chrome_first_domains 白名单（反爬平台首跳 Chrome）、提取全在 Python（SiteAdapter 注册表 + trafilatura 兜底）、直连→fetch→render 升级链、验证码交互、渲染纳入实现（不延后）、文章产物幂等覆盖、图片按平台防盗链等。扩展侧新增 fetch_page/render_page/render_poll/render_close 四消息，WS 传输加固（continuation 帧 + req_id 匹配，LESSONS 24-25）。

## 实施中发现的关键 API 事实（实测，非文档推断）这些是写代码过程中**实测确认**的事实，对后续维护至关重要：

1. **千问 key（`sk-ws-` 前缀）在公共 DashScope 端点有效**：`https://dashscope.aliyuncs.com/api/v1`。用户最初规范的 MaaS workspace 端点（`{workspace_id}.cn-beijing.maas.aliyuncs.com`）实测域名不存在（SSL 证书不匹配）——**用公共端点即可，无需 workspace_id**。
2. **`file_urls` 数组只允许 1 个链接**：提交多个 URL 返回 `InvalidParameter.MalformedURL: The file_urls array only allows one file link`。因此**每个音频切片必须单独提交一个异步任务**。
3. **说话人分离参数是 `diarization_enabled: true`**：单独传 `speaker_count` 无效（被接受但无 speaker_id）；两者同传有效。开启后句子级返回 `speaker_id`。
4. **transcription_url 下载的 JSON 结构**：
   ```json
   {"transcripts": [{"sentences": [{"begin_time", "end_time", "text", "sentence_id", "speaker_id", "words": [...]}]}]}
   ```
   与轮询响应的 `output.results[].transcription_url` 是两套结构；**句子/词级时间戳都在**（毫秒）。
5. **transcription_url 域名需代理访问**（`dashscope-result-bj.oss-cn-beijing.aliyuncs.com` 直连 DNS 失败）——下载结果必须走配置的代理。
6. **异步提交必须带 `X-DashScope-Async: enable` 头**，否则 403 "does not support synchronous calls"。
7. **oss2 的 V4 签名**：用 `oss2.AuthV4(ak, sk)` + `oss2.Bucket(auth, endpoint, bucket, region=...)`。`Auth` 不支持 `auth_version` 参数。
8. **V4 签名 URL 只对 GET 方法有效**：HEAD 请求会 403。URL 验证必须用 `GET + Range: bytes=0-0`（返回 206，总大小从 `Content-Range` 头取）。
9. **OSS 上传显式指定 Content-Type**（`audio/mp4`）：否则按扩展名推断（如 .txt → text/plain），会被我们自己的 URL 验证拒绝。

## 平台风控观察（运营经验）

- **B站/抖音**：无 cookie 时高频 503/412（音频轨限流最狠）；503 是暂时的，指数退避 30s 起步可恢复；有 cookie 后显著改善
- **YouTube**：cookie 会被浏览器安全机制轮换失效（"The provided YouTube account cookies are no longer valid"），**需要用户定期重新导出**；代理 IP 触发 bot 检测时强制要登录 cookie
- **抖音**：必须 cookie（游客设备指纹不够），无 cookie 直接报 "Fresh cookies are needed"
