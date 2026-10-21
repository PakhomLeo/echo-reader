# medialab 交付记录（DELIVERY）

> 状态：累积历史交付档案。本文保留历任阶段、当时的验证数字和问题解法；当前跨组件状态、真实
> E2E 与开放项以 [Echo Reader HANDOFF v5](../../../docs/HANDOFF.md) 为准。
> 正文中的静态 Cookie、Chrome fetch/render、curl-cffi 0.16 和 XHS 专用 Broker 是历史交付记录，
> 不应复制到现役代码；当前方案见 `ARCHITECTURE.md`。
>
> MediaLab 组件维护再读 `ARCHITECTURE.md`（现役架构）、`DECISIONS.md`（设计决策）、
> `LESSONS.md`（踩坑全集，历史编号跨分类重复）、`ROADMAP.md`（阶段计划）、`HANDOFF.md`（组件交接）、
> `README.md`（对外简介）、`browser-extension/README.md`（扩展协议与 UI）。

---

## 1. 项目定位

**进程内（in-process）媒体下载 + ASR 转写 + 文章化 + 文章/图片抓取库**（Python ≥3.10）。

宿主 app `import medialab` 直接异步调用。MediaLab 核心默认无 HTTP API/端口；可选本地 SessionBridge 使用 WebSocket，扩展自动连接且无需用户连接配置，Echo 控制面是集成层例外。

一句话能力：

> 给一个链接（视频或文章），输出：① 视频/音频文件；② 带时间戳+说话人的转写文本；③ 可读文章（中/英）；④ 文章 + 图片（微信/知乎/头条/X/小红书/任意网站）；⑤ 浏览器会话桥——免手动导出 Cookie，YouTube 登录墙也能下，登录文章也能抓。

**两条主链路**：

```
视频链路: URL → submit() → router → 下载/转音频 → [浏览器会话桥] → 切片(60s+重叠)
          → [可选倍速] → OSS → 千问 ASR → 时间戳/说话人对齐 → 落盘 → 文章化

通用文章链路: URL → submit() → router → 直连(httpx) → Chrome fetch_page → Chrome render_page
小红书链路: URL → 扩展 request_session 仅导出 cookies+UA → 任务级 curl_cffi Session 页面 GET 一次
          → 同一 HTML 分流 → 每唯一图片一次或唯一视频一次 GET（详见 ARCHITECTURE §6.11）
          → SiteAdapter 提取 → 图片下载 → {标题}/article.md + images/ + manifest.json
```

---

## 2. 历任干了什么（按阶段）

| 阶段 | 内容 | 状态 |
|---|---|---|
| 一 | v1 下载闭环（yt-dlp 白名单/线程池/SQLite/Netscape cookie/ffmpeg） | ✅ |
| 二 | v2 ASR 管线（OSS 上传→千问 filetrans→时间戳对齐） | ✅ |
| 三 | refine 文章化（qwen3.7-flash 润色，英文自动出中文版） | ✅ |
| 四 | 倍速基准（1.5x 为公共最优，bench/ bench2/ 证据） | ✅ |
| 五 | YouTube 受阻排查（会话信任≠IP 信任，无痕导出工作流） | ✅ |
| 六 | P1 Cookie Broker（`--cookies-from-browser`） | ✅ |
| 七 | P2/P3 浏览器会话桥（扩展 + WS bridge + POT 插件；**实测绕过 YouTube 登录墙**） | ✅ |
| 八 | P4 文章/图片抓取（router 分派 + SiteAdapter 注册表 + 三级升级链） | ✅ 2026-08 |
| 九 | 插件 UI 重做（苹果风、主视图极简 + 设置页）+ 全量 bug 修复 | ✅ 2026-08 |
| 十 | 全库架构审查与修复（12 项全修：可选依赖延迟导入/Bridge 并发 pending/fallback 降档生效/优雅 close/事件线程统一/ASR 契约/session.proxy/OSS 排序/域名精确匹配/renderTabs 持久化/无效配置清理/lxml 警告），详见 `HANDOFF.md §4.6` | ✅ 2026-08 |
| 十一 | **并行化改造 + 代码整理**：阶段级信号量（下载/抓取/ASR/润色各 max_concurrent 互不占槽）、transcribe 拆三阶段、ASR 切片并行提交（slice_concurrency）、wait 默认统一 False、删 article 兼容导出与 `_classify` 死代码、urllib→httpx，详见 `HANDOFF.md §4` | ✅ 2026-08 |
| 十二 | **小红书任务级 curl 单次合同**：扩展只导出 profile cookies+UA；每任务独立 `curl_cffi` Session 页面一次 GET并同源分流，每唯一图片一次或唯一视频一次 GET | ✅ 2026-08（自动化合同；真实 E2E 当前为一条成功、一条修复后待复测） |

---

## 3. 能力全景

| 能力 | 说明 |
|---|---|
| 多平台下载 | B站/抖音/YouTube + generic 兜底（yt-dlp 白名单、自动 cookie/代理/JS challenge） |
| 统一入口 | `svc.submit(url)` 按域名规则表分派视频/文章（`router.py`） |
| 交付形态 | 视频保留或转音频（mp3/m4a/aac/flac/wav/opus/ogg） |
| 文章抓取 | 直连 → Chrome fetch → Chrome render 升级链；5 家专属适配器 + trafilatura 兜底 |
| 小红书图文/视频 | 必须当前 Chrome SessionBridge 导出内存凭证；任务级 `curl_cffi` Session 页面一次 GET并同源分流，每个唯一资源各一次 GET |
| 图片下载 | 通用站点保留现有宽容规则；小红书同一任务 Session 对所有去重图片各 GET 一次，任一失败整任务失败 |
| 并发 | 阶段级信号量（下载/抓取/ASR/润色各默认 3，互不占槽）+ to_thread；ASR 切片并行提交（slice_concurrency 默认 3） |
| 持久化 | SQLite（WAL）：tasks/files/asr 三表 |
| 分层重试 | yt-dlp 内置(10) → 风控退避(3) → 降档(1) → ASR 重提交(3) → 无语音片跳过 |
| ASR | 千问 qwen-audio-3.0-asr-flash-filetrans；切片并发；时间戳/说话人对齐；倍速 |
| 文章化 | qwen3.7-flash 润色（按人分段/去口癖）；英文内容自动出中文版 |
| 会话桥 | Chrome 扩展（Cookie Broker + YouTube mint + 通用文章 fetch/render）↔ 本地 WS bridge；小红书扩展只导出 cookies+UA，不发目标站请求 |
| 产物目录 | 资料库根（`LIBRARY_ROOT` 或 `[paths].delivery`）下 `{中文标题}/`，按 echo producer-guide 规范：`manifest.json` + `article.md` + `transcript.json` + `media/original.*` + `media/playback.*` + `images/`；同名文件夹按 manifest.id 区分（同 id 并入，不同 id 加 `-2` 后缀）；article.md 已存在不覆盖 |

---

## 4. 快速使用

```python
import asyncio
from medialab import DownloadService

async def main():
    svc = DownloadService(on_event=lambda ev, p: print(ev, p))
    await svc.initialize()                      # 自检 db/ffmpeg/cookie/OSS/refine

    # ① 统一入口：router 自动分派（视频→下载，文章→抓取）
    t = await svc.submit("https://www.bilibili.com/video/BV...")
    await t.wait(svc)

    # ② 视频下载/转音频/转写（可绕过 submit 直调）
    t2 = await svc.download(url, audio="mp3")   # 转音频
    t3 = await svc.transcribe(url, speed_factor=1.5)  # 全自动转写

    # ③ 文章抓取（白名单站点需 Chrome 会话）
    t4 = await svc.scrape("https://mp.weixin.qq.com/s/...")
    await t4.wait(svc)                          # 产物 {标题}/article.md + manifest.json

    await svc.close()

asyncio.run(main())
```

**浏览器会话桥**（视频登录墙 + 文章白名单站点必需）：

```python
from medialab.bridge import SessionBridgeServer, wait_for_plugin
from medialab.session import ExtensionSessionProvider

server = SessionBridgeServer(); server.start()  # 默认 127.0.0.1:19876
svc = DownloadService(session_provider=ExtensionSessionProvider(server))
```

扩展无配置自动连接。服务端只接受通过 `chrome-extension://` Origin 校验的连接，为连接下发
内存 session token，并在断线/重连时轮换；`role=pot` 使用服务进程内部自动鉴权。

一键联调：
- 视频：`python examples/session_bridge_demo.py <url> -a m4a`
- 文章：`python examples/article_demo.py <url> --bridge`
- 公开文章直连（非白名单）：`python examples/article_demo.py <url>`

**产物位置**：资料库根（`LIBRARY_ROOT` 环境变量优先，缺省 `[paths].delivery`）下 `{中文标题}/`，按 [echo producer-guide](../../echo/docs/producer-guide.md) 规范：
`manifest.json`（必须，id 稳定唯一）+ `article.md`（AI 整理文章）+ `transcript.json`（echo 格式转录）+ `media/original.*`（原始文件）+ `media/playback.*`（浏览器播放版，H.264+AAC/MP3/M4A，源已是可播编码则直接复制）+ `images/`；
`medialab.db` 任务历史。**产出后需通知展示端 rescan**（echo：`POST /api/v1/library/rescan`）。

---

## 5. 模块设计与职责

| 模块 | 职责 | 关键接口 |
|---|---|---|
| `service.py` | 编排一切；async API；submit 分派；并发池；事件分发；会话获取 | `initialize / download / transcribe / submit / scrape / refine / query / wait / list / asr_result / close` |
| `router.py` | submit 类型分派：域名规则表 + URL 模式例外（Q2/Q3） | `detect_kind(cfg, url) -> video|article` |
| `site_adapters.py` | **站点适配器注册表**：5 家专属提取 + trafilatura 兜底 + HTML→Markdown | `SiteAdapter` 基类（match/extract/extract_render/image_referer/needs_raw_dom/site_name）；`find_adapter / article_platform / html_to_markdown` |
| `article.py` | 通用文章升级链 + 小红书同一任务 Session 的图文产物编排 | `ArticleExtractor.scrape / scrape_captured`；`Article / ArticleResult / ScrapeError` |
| `xhs_client.py` | 每任务独立 `curl_cffi` Session；页面/图片/视频单次 GET、响应校验与凭证安全回写 | `XhsClient.from_broker / fetch_page / download_image / download_video / commit_credentials` |
| `publishing.py` | 资料库规范产物编排（echo producer-guide）：manifest 幂等生成 / transcript.json 转换 / media original+playback 编排 | `write_manifest / build_transcript / write_transcript / publish_media / pick_task_dir / derive_id` |
| `config.py` | 配置三级合并（默认 toml < secrets.toml < 宿主 dict） | `load_config / resolve_dir` |
| `store.py` | SQLite（WAL + busy_timeout + 锁） | `create_task / set_status / add_file / add_asr / ...` |
| `downloader.py` | yt-dlp 封装 + 匿名优先升级 + 分层重试 | `download_video / build_opts / parse_cookie_source` |
| `platforms.py` | URL→平台 + extractor 白名单 + 会话域表（视频侧） | `detect_platform / allowed_extractors / session_domains` |
| `cookies.py` | Netscape 解析 + 过期/过时感知（YouTube mtime 提醒） | `resolve_cookie_file / cookie_status / check_cookies` |
| `audio.py` | ffmpeg：transcode/slice（空片降级）/speed（atempo 级联） | `AudioProcessor` |
| `oss.py` | OSS 上传/V4 签名/URL 验证/滚动清理 | `OssClient / verify_url` |
| `asr.py` | ASR 提供方抽象 + qwen 实现 + 切片管线 + 对齐 | `AsrProvider / QwenFiletransProvider / AsrPipeline` |
| `refine.py` | 转写稿→文章（qwen3.7-flash，中/英） | `QwenRefiner.refine / save`；`is_chinese_content` |
| `session.py` | 通用会话抽象；小红书内存凭证快照、缓存与 generation 去重刷新 | `XhsCredentialSnapshot / XhsCredentialBroker`；通用 `BrowserSession / SessionProvider` |
| `bridge.py` | 本地 SessionBridgeServer：WS 服务端（扩展 Origin、每连接内存鉴权、req_id→pending、ping、会话/POT/fetch/render 转发） | `request_session / request_fetch / request_render...`；小红书只使用 `request_session` |
| `events.py` | 事件定义与分发（5 类） | `Emitter.emit` |
| `browser-extension/` | Chrome 扩展：Cookie Broker + mint + 通用文章 fetch/render；小红书只导出当前 profile cookies 与 UA | 见 `browser-extension/README.md` |
| `src/yt_dlp_plugins/extractor/medialab_bridge.py` | yt-dlp POT Provider 插件（按需向扩展要 Token） | `@register_provider MedialabBridgePTP` |

**关键设计**：
- **路由优先级（Q2/Q3）**：`video_patterns > article_patterns > article_domains > video_domains > default_kind(article)`；纯域名规则表，不预检不嗅探
- **通用抓取白名单（Q7）**：非小红书站点由 `[article].chrome_first_domains` 决定首跳 Chrome 或直连；小红书始终走 ARCHITECTURE §6.11 的专用合同。
- **升级链（Q10）**：直连 → `fetch_page`（SW 认证请求）→ `render_page`（后台标签页 + Readability，pending 时激活重试）；验证码保持标签页轮询等用户（超时 CAPTCHA_REQUIRED）
- **提取全在 Python（Q6）**：扩展只执行（fetch/render/采集），适配器与清洗逻辑在 `site_adapters.py`；render 返回 Readability 清洗结果 + images_all（懒加载图）
- **小红书扩展只做凭证导出**：第一次任务通过 `request_session` 获取当前 Chrome profile cookies 与 UA，之后由进程内 Broker 复用；扩展不请求小红书页面/资源。单个 `DownloadService` 默认自带 Broker，也支持 `xhs_credential_broker=` 注入。Echo HTTP 服务把一个共享 Broker 注入每个 job 的新 Service；其他多 Service 宿主需自行显式共享。服务进程重启后重新读取，凭证不落盘。
- **小红书 Python Session**：每任务独立 `curl_cffi` 0.16 Session，`impersonate="chrome"` 当前近似 Chrome 146，不宣称精确 Chrome 151；预设保持自己成套的 UA/default headers，插件 UA 只作为快照元数据。一个 Session 完成页面一次 GET、同 HTML 分流、每唯一图片一次或唯一视频一次 GET，无 HEAD/probe/retry/fallback。页面/资源及重定向目标强制 HTTPS 域白名单；跨源 CDN Referer 只发页面 origin，避免泄漏页面 token。
- **刷新与回写**：仅页面明确 401 或最终 URL 为登录页时 generation 去重刷新；资源 401、403/CAPTCHA/429/network 不刷新。当前 URL 不重试，新快照只供下一任务。成功任务只提交最终 `XhsClient` 的 cookie jar，Broker 校验 cookie 域并以 generation+revision CAS 回写进程内缓存；best-effort 回写失败不影响已完成内容。
- **文章产物幂等（echo §8）**：`article.md` 已存在则不覆盖（用户可能已修改）；重抓只更新 images/ 与 manifest
- **同名文件夹按 manifest.id 区分**：同 id 幂等并入原目录；不同 id / 无 manifest 的旧目录加 `-2/-3...` 后缀，绝不合并
- **meta.json 废弃**：抓取元数据（标题/作者/发布时间）并入 `manifest.json` 的 source 字段；`transcription.raw.json / transcript.normalized.json / transcript.txt / article.zh.txt` 均不再产出
- **playback 转码**：播放文件必须浏览器可播（H.264+AAC MP4 / MP3 / M4A）；源已是可播编码直接复制，否则 ffmpeg 转码（决策 3）
- **自动连接（UI）**：扩展固定连接本机默认端口，无 token 输入或保存步骤；SW 通过 alarms 与 popup 唤醒自动重连，断线后的新连接会获得新的内存 session token
- **会话文件生命周期（Q9）**：临时 cookie 落 `tmp/{task_id}/session/`（子目录！放根目录会被产物扫描误收），任务 finally 清理
- **结构化错误（Q16）**：`FailureCode` 枚举（16 项，含文章抓取的 CONTENT_NOT_FOUND/CAPTCHA_REQUIRED）；`tasks.error_code` 保留 4 类兼容层，精确分类进 `error_detail` 前缀

---

## 6. 配置体系

```toml
[concurrency] max_concurrent = 3                     # 每个阶段（下载/抓取/ASR/润色）各自的并发上限，互不占槽
[asr]         slice_concurrency = 3                  # ASR 切片提交/轮询并行数（RPM 600 允许 3-5）
[platforms]   enabled / session_domains            # 视频平台与会话域（P2 授权）
[router]      default_kind="article" / video_domains / article_domains / video_patterns / article_patterns
[article]     chrome_first_domains（6 域）/ user_agent / proxy / direct_timeout / direct_retries /
              min_text_chars=200 / render_wait_ms / render_poll_ms /
              captcha_wait_seconds=60 / max_images=100 / image_retries / image_timeout /
              max_image_bytes / output_html=false
[cookies]     dir / 平台→文件名 / youtube_refresh_hours=12
[paths]       delivery / temp / db
[download]    format / retries / fragment_retries / risk_retries / fallback_format / proxy /
              js_runtimes=["deno"] / remote_components / cookie_source / session_first_platforms
[audio]       ffmpeg_path
[progress]    throttle = 1.0
[oss]         presign_expires / max_batches / ttl_seconds / slice_seconds / overlap_seconds
[asr]         model / language_hints / diarization_enabled / speaker_count / slice_concurrency=3 / poll_interval / max_wait
[refine]      enabled / model="qwen3.7-flash" / base_url / temperature / max_input_chars /
              timeout_seconds / max_retries / max_tokens=12000（防输出爆炸假挂起，LESSONS 46）/
              shrink_threshold=2（超长输入自适应压小块）/ speaker_label / zh_threshold=0.2
[transcribe]  timeout（任务级超时保护，超时标记 failed）
```

OSS 启用规则：`secrets.toml [oss]` 密钥齐全即启用，无独立开关。

密钥只放 `config/secrets.toml`（gitignore）；缺失不阻塞下载，仅 ASR/OSS/refine 报明确错误。SessionBridge 固定连接本机端点，无用户配置；POT 鉴权值由服务进程自动生成并注入，不列为用户环境配置。

---

## 7. 经验总结（浓缩，完整原文见 LESSONS.md）

**代码级**：SequenceMatcher autojunk；`-c copy` 尾部空片降级；千问无语音片跳过；atempo >2x 级联；临时 cookie 别放产物扫描目录；错误分类用精确短语；Windows subprocess 编码；`_tasks` 完成后清理；**可选依赖必须延迟导入**（顶层 import 会炸掉整包导入）；**单 pending 槽并发丢失且超时会误删他人等待者，必须按 req_id 索引**；**"最后一次 continue"是死代码**（降档重试要测试断言第二次调用）；**to_thread 线程无法取消**（close 只能优雅等待，事件要 call_soon_threadsafe 回 loop 线程）；**lxml 元素真值判断已发 FutureWarning，用 `is not None`**；**任务级信号量 = 卡脖子**（阶段级信号量互不占槽，LESSONS 36）；**ThreadPoolExecutor 按列表序取结果 = 保序 + 真并行**（LESSONS 37）；**httpx 不抛 HTTP 错误需显式查 status_code**，迁移时测试 mock 要跟着换（LESSONS 38-39）。

**WS 桥级（P4 实测新增）**：websocket-server 0.6.4 读大消息截断且不支持 continuation 帧——**必须覆写 `read_next_message`**（LESSONS 24）；响应按 `req_id` 匹配、扩展不回 ping（LESSONS 25/29）；SW 挂起半死连接靠新连接顶替 + 0.5min alarm（LESSONS 15）。

**站点级（P4 实测新增）**：微信正文只在可见标签页渲染（激活重试，LESSONS 26）；小红书 INITIAL_STATE 嵌 JS `undefined` 需清洗、无签名 URL 返回 32×32 占位图（LESSONS 27-28）；知乎 hydration content 可能是段落对象数组。

**MV3 插件**：SW 30s 挂起断 WS（alarms keepalive）；权限 pattern 带路径 + 双 pattern；`permissions.request` 必须 popup 手势；`executeScript` files+func 不能同用；`world: MAIN` 读 ytcfg；异常回传服务端。

**yt-dlp 生态**：POT 插件必须放 `yt_dlp_plugins/extractor/`；EJS 用 PyPI 包 + Deno；`--extractor-args youtube:visitor_data=` 注入。

**平台/运营**：B站/抖音 503 退避可恢复；YouTube cookie 轮换（时间戳不可信）；扩展铸造 visitor_data+会话 cookie 可绕过登录墙；换 IP 仅对纯匿名路径必要；清理脚本必须精确锚定文件名（LESSONS 30 误删教训）。

---

## 8. 已知问题与后续

1. **X 视频推文默认走文章**（域名规则无 URL 级区分；x.com/status 默认文章，`/video/` 后缀走视频）
2. X 推文 oEmbed 直连需代理环境实测（`[article].proxy`）
3. 小红书真实 E2E：第二条图文已成功导入标题、正文和 6 张图片；第一条只请求一次，暴露 HTTP CDN URL 问题，规范化修复后未重跑。完整双帖闭环仍待完成。
4. 头条反爬墙（滑块/风控）场景未真机验证，验证码交互路径已就绪未实测
5. ~~ASR 切片串行~~（已并行化：切片提交/轮询 ThreadPoolExecutor 默认 3 路，`[asr].slice_concurrency` 可调）
6. diarization 对倍速脆弱（混叠内容 1.5x 塌缩，无自动降级）
7. YouTube 匿名成功率基线待干净 IP 补测；PO Token 按需链路待真实 403 验证
8. Native Messaging 正式化（打包时，协议与传输已解耦）
9. 同标题视频并发任务写同一目录（单文件冲突有 task 前缀兜底）

---

## 9. 测试与验证

```bash
mise exec '--' uv run pytest components/medialab/tests -q
```
覆盖：config 合并 / cookies stale / platforms（含 lookalike 域名负例）/ store CRUD + interrupted 标记 / ASR 对齐 / 切片倍速 / refine 分块 / 错误分类 / 会话层 / bridge（Origin/连接会话鉴权、转发、req_id 匹配、断线轮换、mint、fetch/render、**并发双请求**、**超时不误删他人 pending**）/ 小红书 `curl_cffi` Session 构造、每 URL 单次 GET、凭证缓存与共享 Broker 注入、并发 generation 去重刷新、页面 401/登录跳转与资源 401/403/CAPTCHA/429/network 的刷新边界、HTTPS 域/重定向目标校验、Referer token 脱敏、Cookie 域与 revision CAS、凭证不进入 repr/错误信息 / **fallback 降档确实产生第二次下载调用** / **可选依赖缺失时 `import medialab` 正常** / **close() 等待运行中线程** / **session.proxy 进入 yt-dlp opts** / router 分派 / article 适配器。

**真机验证记录（2026-08-04）**：
- 历史站点检查不作为当前小红书任务级 curl 合同的 E2E 证据；2026-08-10 新证据为一条图文成功、一条修复后待复测，详见根 HANDOFF v5。
- 视频：B站/抖音/YouTube 扩展会话下载 ✅；YouTube 登录墙绕过 ✅（qM4AWORRT48）
- UI：主视图/设置/授权/主题/图标 用户目验通过

**手工验证链路**：
1. `python examples/host_demo.py <url> -a mp3`
2. `python examples/e2e_transcribe.py`（B站全流程，需 secrets.toml）
3. `python examples/session_bridge_demo.py <url> -a m4a`（Chrome 扩展联调）
4. `python examples/article_demo.py <url>`（公开文章直连）/ `--bridge`（白名单站点）

---

## 10. 环境与运维

- Python 3.12.11（mise），venv `.venv/`，`pip install -e ".[cloud,bridge]"`（依赖：yt-dlp/yt-dlp-ejs/httpx/curl-cffi 0.16/trafilatura/cssselect + 可选 oss2/websocket-server）
- ffmpeg（mise）、**Deno 2.9.4**（mise，js_runtimes）+ `yt-dlp-ejs`
- 代理 `http://127.0.0.1:10808`（[download].proxy 仅 YouTube；[article].proxy 供海外文章直连）
- cookie 目录 `cookies/`；**YouTube 下载前重新导出**（无痕窗口法，LESSONS 17）
- 密钥 `config/secrets.toml`
- 扩展：`chrome://extensions/` 开发者模式加载 `browser-extension/`；**改 background.js/popup/manifest 后必须重载**。小红书扩展只导出当前 profile cookies+UA；白名单默认值改动需「恢复默认」；pip 装包走 `--proxy http://127.0.0.1:10808`
- PO Token 服务（bgutil）已移除（解不了登录墙）；`yt-dlp-ChromeCookieUnlock` 默认不启用（强杀浏览器）

---

## 11. 给下一个 AI 的提醒

- 改代码前先读 `LESSONS.md`（按章节/标题检索，历史编号跨分类重复）和 `DECISIONS.md`；跨组件新接手先读根 `docs/HANDOFF.md`
- 产物目录按 echo producer-guide 规范（`manifest.json`/`article.md`/`transcript.json`/`media/`）编排，见 `publishing.py`；改产物格式前先同步 echo 侧 `docs/producer-guide.md` 与 `docs/file-format.md`
- **close() 是优雅等待**：等 to_thread 任务自然完成才关库，宿主需要立即中断时直接结束进程
- **事件回调永远在事件循环线程**执行（Emitter 经 call_soon_threadsafe 派发），宿主回调不要假设线程
- **DB 状态含 interrupted**：崩溃重启后上次未完成任务不再是 queued/running
- **fallback_format 降档真的会执行**：风控耗尽后多一次降档下载（最坏耗时变长）
- **扩展与 Python 侧是两套部署**：Python 改完即生效；扩展任何改动（含 manifest）都要 `chrome://extensions` 重新加载
- 真机测试前确认：扩展已重载并已自动连接本机 SessionBridge
- 真实转写前确认：secrets.toml、ffmpeg、Deno/EJS、YouTube 需新鲜 cookie 或扩展会话
- 控制台中文乱码是 Windows GBK 显示问题，文件是 UTF-8，不是 bug
- 文本对比永远 `autojunk=False`
- 大消息测试（文章 HTML >100KB）会触发 WS 分片路径——改动 bridge 后必须用真机大页面回归
- 清理/批处理脚本的正则必须锚定文件名前缀，禁止裸正则扫全目录（LESSONS 30 血泪教训）
