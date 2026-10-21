# medialab 交付文档（HANDOFF）

> 状态：MediaLab 组件历史交接与使用档案。本文保留历次改造、当时验证和问题解法；它不再是
> Echo Reader 的唯一当前交付入口。跨组件新接手先读
> [根 HANDOFF v5](../../../docs/HANDOFF.md)，MediaLab 现役技术合同看 `ARCHITECTURE.md`。
> 本文正文保留旧 Cookie 文件、Chrome fetch/render 与 XHS 专用 Broker 等当时方案；这些内容是
> 演进证据，已被 v3 通用 CredentialBroker + Python Chrome146 网络栈替代。
>
> 快速导读：**§1 项目是什么 → §2 echo 资料库规范产物编排 → §3 小红书任务级 curl 单次合同 → §4 并行化历史 → §5 经验总结 → §6 功能使用手册 → §7 设计总览 → §8 已知问题 → §9 改代码前必读**。
>
> 配套文档：`DELIVERY.md`（完整交付历史）→ `ARCHITECTURE.md`（现役架构）→ `DECISIONS.md`
> （设计决策）→ `LESSONS.md`（问题/解法全集，历史编号跨分类重复）→ `README.md`（对外简介）。

---

## 1. 项目是什么

**进程内（in-process）媒体下载 + ASR 转写 + 文章化 + 文章/图片抓取库**（Python ≥3.10）。

宿主 app `import medialab` 直接异步调用。MediaLab 核心默认无 HTTP API/端口；可选本机 SessionBridge 使用 WebSocket，扩展无配置自动连接，Echo HTTP 控制面属集成层例外。

两条主链路：

```
视频链路: URL → submit()/download() → yt-dlp 下载/转音频 → [浏览器会话桥]
          → 切片(60s+重叠) → [可选倍速] → OSS → 千问 ASR → 时间戳/说话人对齐 → 落盘 → 文章化(LLM 润色)

通用文章链路: URL → submit()/scrape() → 直连(httpx) → Chrome fetch_page → Chrome render_page
小红书链路: URL → 扩展 request_session 只导出 profile cookies+UA
          → 每任务独立 curl_cffi Session → 页面一次 GET → 同一 HTML 分流
          → 每唯一图片一次或唯一视频一次 GET → 图文/视频规范产物
```

核心公共门面只有一个：`DownloadService`（service.py），其余模块全是内部能力拆分。

---

## 2. 本次交接：echo 资料库规范产物编排（2026-08）

### 2.1 背景

echo 展示端（`<original-echo-dir>`）定义了资料库规范（`docs/producer-guide.md` + `file-format.md`）：
一条信息 = 资料库根（`LIBRARY_ROOT`）下一个中文标题文件夹，含 `manifest.json`（必须）、
`article.md`、`transcript.json`、`media/original.*`、`media/playback.*`、`images/`。
medialab 旧交付布局（`delivery/{标题}/` 平铺 + `transcript.normalized.json` 三件套 + `meta.json`）
与规范不兼容，需按规范改造。评审确认 6 项决策（英文文章/同名撞车/转码成本/meta.json/LIBRARY_ROOT/旧产物不迁移）。

### 2.2 改了什么

| # | 改动 | 文件 | 为什么 |
|---|---|---|---|
| 1 | **新增 `publishing.py`**：manifest 幂等生成（id 稳定：`vid-{平台}-{视频id}` / `art-{平台}-{url哈希}`；同 id 保留 created_at）、`transcript.json` 转换（毫秒/段 id/说话人标签，语言自动判定 zh-CN/en）、media 编排（original 保留 + playback 转码，源已是 H.264+AAC MP4 / MP3 / M4A 直接复制）、同名目录 `pick_task_dir`（同 id 并入 / 不同 id 加 `-2` 后缀） | publishing.py（新） | echo 展示端扫描器按 manifest.id 区分条目，同 id 重复会导致整个文件夹被忽略 |
| 2 | **`[paths].delivery` 优先感知 `LIBRARY_ROOT` 环境变量**（显式 config.toml/overrides 仍可覆盖） | config.py | echo 后端只认 `LIBRARY_ROOT`，medialab 无需宿主改调用 |
| 3 | **转录落盘改为 `transcript.json`**（echo 格式）；`transcription.raw.json / transcript.normalized.json / transcript.txt` 三件套删除 | asr.py | 规范只定义 transcript.json 一种转录文件 |
| 4 | **润色产出 `article.md`**（原 `article.txt`）；**英文内容直接产中文版**（决策 1：`article.md` 是唯一正文，中文为主；原标题进 manifest `source.title` 作副标题）；`article.zh.txt` 删除 | refine.py / service.py | echo 阅读页只展示 article.md |
| 5 | **五处落盘改造**：download / transcribe 三阶段 / scrape / xhs 视频统一走 `media/` + manifest 编排；`meta.json` 废弃（元数据并入 manifest source：title/author/published_at）；`article.html` 删除 | service.py / article.py | 决策 4 |
| 6 | **article.md 幂等（echo §8）**：已存在不覆盖（用户可能已修改），重抓只更新 images/ 与 manifest | article.py / service.py | 防止覆盖用户修改 |
| 7 | **playback 转码（决策 3）**：H.264+AAC MP4 / MP3 / M4A 直接复制，其余 ffmpeg 转（视频 libx264+faststart，音频 AAC 128k） | publishing.py | 浏览器只读 playback，必须可播 |
| 8 | **测试**：新增 `tests/test_publishing.py`（28 例），更新 test_service/test_article 的产物断言 | tests/ | 当时全量 168 例零警告（历史数字） |
| 9 | **refine 长输入防护（LESSONS 46）**：qwen3.7-flash 在"改写文章"长输入上偶发输出爆炸（10 万+ token，非流式要等全部输出 → 15-20 分钟假挂起）。`QwenRefiner` 加 `max_tokens` 上限截断 + 超长输入自适应压小块（`shrink_threshold`）；`svc.transcribe(timeout=...)` 任务级超时兜底（超时标记 failed 不阻塞批次）；三阶段日志打点（阶段1/2/3） | refine.py / service.py / default_config.toml | 批量 27 视频实测：墨脱（47min）修复前卡死 20min，修复后 132s |

### 2.3 产物与行为（新布局）

```
{LIBRARY_ROOT}/{中文标题}/
├── manifest.json        # 必须（id/source/article/transcript/primary_media）
├── article.md           # AI 整理文章（已存在不覆盖）
├── transcript.json      # echo 格式转录（毫秒/段 id/speaker/语言）
├── media/
│   ├── original.*       # 原始下载文件
│   └── playback.*       # 浏览器播放版（可播编码，直接复制或转码）
└── images/              # 文章插图（抓取）
```

- id：`vid-{平台}-{视频id}`（视频）/ `art-{平台}-{url哈希}`（文章/无 id）；同内容重跑 id 不变 → 幂等并入原文件夹
- 同名不同内容 → `标题-2/-3...` 后缀（决策 2），绝不合并
- 转写：`media/playback.m4a`（音频主媒体）+ `transcript.json` + `article.md`（中文为主）
- 抓取：`article.md` + `images/` + `manifest.json`（无 meta.json；author/date 进 source 字段）
- 产出后需展示端 rescan：echo `POST /api/v1/library/rescan`（medialab 事件 `EVENT_TASK_DONE` 带 out_dir，宿主自行触发）

### 2.4 已知边界

- 转码成本：非 H.264 源（mkv/webm 等）需全量转码一次（决策 3 已确认）；H.264+AAC MP4 直接复制
- `svc.refine(task_id)` 补跑时若 article.md 已存在会明确报错（echo §8 语义）
- 旧 `delivery/` 产物不迁移（决策 6），规范只约束新任务
- DB asr 表列名未改（`normalized_path` 列现存 transcript.json 路径；raw/txt 列不再写入）
- **长视频 refine**：qwen3.7-flash 偶发输出爆炸假挂起——`max_tokens`+自适应分块+任务级 timeout 已兜底（LESSONS 46）；批量跑长视频建议 `[refine].model="qwen-turbo"`（实测 7s vs flash 35s，输出克制）
- **转写任务超时**：`svc.transcribe(timeout=秒)` 超时标记 failed（error_detail 前缀 `[timeout]`），to_thread 线程不可取消，宿主跳过继续即可

---

## 3. 本次交接：小红书任务级 curl 单次合同（2026-08）

小红书必须使用用户当前 Chrome SessionBridge，但扩展只通过 `request_session` 导出当前 profile 的 cookies 与 UA，不访问任何小红书页面或资源。单个 `DownloadService` 默认在自己的 Broker 中缓存凭证，也可用 `xhs_credential_broker=` 注入共享 Broker。Echo HTTP 层会把一个进程级 Broker 注入每个 job 新建的 Service；其他宿主创建多个 Service 时需显式共享。每个任务仍创建独立 `curl_cffi` Session。

- `curl_cffi` 0.16 使用 `impersonate="chrome"`，当前近似 Chrome 146；它保持预设自己的成套 UA/default headers，插件 UA 只存为快照元数据。它不是本机 Chrome 151 的精确指纹，文档与 UI 都不得如此宣称。
- 同一个任务 Session 对页面发起一次应用层 GET，并用同一 HTML 判断图文/视频；图文每个去重图片 URL 各 GET 一次，视频只选一个 stream 并 GET 一次。
- 无 HEAD/probe/retry/fallback，不切换 Session。一次 curl 调用内部可能跟随重定向；当前 URL 一旦失败不重试。
- Broker 首次任务从插件取凭证，后续任务无 TTL 复用，服务重启后再取，不落盘。只有页面明确 401 或最终 URL 跳登录页才按 generation 去重刷新；资源 401、裸 403、CAPTCHA、429、network 不刷新。刷新后的快照只给下一任务，当前任务仍失败；刷新读取失败会清掉已知过期快照。
- 页面、资源及重定向后的最终 URL 都受 HTTPS 域白名单约束；跨源 CDN Referer 只发送页面 origin，避免泄漏 token。
- 成功任务只提交最终 `XhsClient` 的 cookie jar；Broker 校验 cookie 域并以 generation+revision CAS 回写内存。旧任务/旧 revision 不能覆盖新快照，best-effort 回写失败不影响已经完成的内容任务。

权威细节见 [ARCHITECTURE.md §6.11](ARCHITECTURE.md#611-小红书单次会话合同)。自动化测试覆盖单次调用、缓存/刷新边界和安全回写；2026-08-10 真实 E2E 为第二条图文成功、第一条暴露 HTTP CDN URL 后已修复但未重跑，不能假报双帖完成。

### 3.1 上一任交接

并行化改造与代码整理（2026-08，历史）见 §4。

---

## 4. 上一任交接：并行化改造与代码整理（2026-08，历史）

### 4.1 背景

两个诉求：
1. 按 8 条原则（见项目根 `AGENTS.md`）审查项目——找违反原则的死代码/兼容层/自造轮子
2. **多链接并行**：很多链接同时进来时，不能因为某个任务卡住（ASR 轮询/风控退避/LLM 调用）而阻塞其他任务的后续阶段

### 4.2 改了什么

| # | 改动 | 文件 | 为什么 |
|---|---|---|---|
| 1 | **任务级信号量 → 阶段级信号量**：`_sem`(1 个) 拆为 `_download_sem/_scrape_sem/_asr_sem/_refine_sem`（各 `max_concurrent`，默认各 3）；`transcribe` 拆为 `_transcribe_download → _transcribe_asr → _transcribe_refine` 三阶段，各自独立占槽 | service.py | 原设计一个转写任务占满 1 个全局槽 8-10 分钟（大部分是 ASR 轮询等待），3 个转写任务就把下载/抓取全堵死。阶段拆开后：下载阶段不占 ASR 槽，一个任务卡在 ASR 轮询/重试不阻塞其他任务的下载与抓取 |
| 2 | **ASR 切片并行提交**：`AsrPipeline.transcribe` 的"提交→轮询→取回"从逐片串行改为 `ThreadPoolExecutor(slice_concurrency)`（默认 3，配置 `[asr].slice_concurrency`） | asr.py | 1 小时音频 60 片逐片串行约 8-10 分钟，RPM 600 允许 3-5 并发（HANDOFF 旧已知问题 #3）；切片/上传保持串行（快路径） |
| 3 | `transcribe/scrape/submit` 的 `wait` 默认统一为 `False`（与 `download` 一致） | service.py | 原默认 `wait=True` 让宿主丢入多个链接时第一个 await 就卡住整个循环，与"并行处理大量链接"直接冲突 |
| 4 | 删 `article.py` 兼容导出块（`WechatAdapter` 等 7 个符号的 re-export，注释自述"兼容导出"）；删 `service.py` 死代码 `_classify()`；`asr.py/refine.py` 手写 `urllib` HTTP 全部换 `httpx`（项目已有依赖） | article.py / service.py / asr.py / refine.py | 原则 1（过时直接删）、原则 6（先翻已有依赖）。测试同步更新（`test_article.py` 改从 `site_adapters` 导入；`test_refine.py` 的 urllib mock 改为 patch `httpx.post`） |

### 4.3 为什么这样设计（并发模型）

```
原: 一个任务 = 一个全局槽(Semaphore(3))，槽内串行执行整个流水线
    任务A(转写): [下载][切片×60][上传][ASR×60][轮询×60][refine]  ← 全程占1槽，8-10分钟
    任务B(下载): 排队等槽……（被 A 的 ASR 等待堵死）

改后: 阶段级信号量，任务内阶段切换即释放槽
    任务A(转写): [下载槽]→(释放)→[ASR槽]→(释放)→[refine槽]
    任务B(下载):      [下载槽]      ← 不被 A 的 ASR 阶段阻塞
    ASR 槽内: 60 片切片 ThreadPoolExecutor(3) 并行提交/轮询 → 3-4 分钟
```

- 保持 `queued→running→done/failed` 状态机、`_tasks` 注册表、事件回调、`close()` 优雅关闭语义**全部不变**——只动了并发控制层
- 事件回调本来就 `call_soon_threadsafe` 单线程派发、store 有锁，并行后线程安全无新增风险
- ASR 无语音片（`NoSpeechError`）在 worker 内捕获转 `skipped` 标记，不重试不抛出，跳过逻辑与原来一致

### 4.4 对宿主有影响的行为变更（升级注意）

1. **`wait` 默认值**：`transcribe/scrape/submit` 现在默认 `wait=False` 立即返回 handle；要等待请显式 `await svc.wait(task_id)` 或传 `wait=True`（README/HANDOFF 示例本来就是显式 wait，不受影响）
2. **`max_concurrent` 语义变了**：从"全局并发上限"变成"**每个阶段**的并发上限"（默认各 3，总线程峰值 = 阶段数 × 3，在 asyncio 默认线程池 min(32, cpu+4) 内）。并发总量理论上比旧版大，但阶段间互不占槽正是目的
3. **新增配置 `[asr].slice_concurrency = 3`**：单任务内并行提交的切片数（RPM 600 允许 3-5）
4. **`medialab.article` 不再 re-export 适配器符号**：`WechatAdapter` 等请从 `medialab.site_adapters` 导入
5. `service._classify` 删除：宿主若依赖它，改用 `session.classify_error` + `session.legacy_code`

### 4.5 本轮新增经验（LESSONS 36-38，详见 LESSONS.md）

- **任务级信号量 = 卡脖子**：并发池的粒度必须匹配"资源"而不是"任务"；等待型阶段（轮询/退避）绝不能占共享槽
- **ThreadPoolExecutor 结果顺序**：`futures` 列表按序 submit 后按序 `fut.result()`，天然保序且仍是真并行（总耗时 = 最慢切片）；上传/切片等快路径保持串行，避免无谓线程
- **httpx 与 urllib 行为差异**：`urllib` 的 `HTTPError` 只在 HTTP 错误时抛（异常即错误），`httpx` 默认不抛——必须显式检查 `status_code`；`httpx.post(json=...)` 自动序列化并带 `Content-Type`，代理用 `proxy=` 参数

### 4.6 上一任交接：架构审查与修复（2026-08，历史）

外部审查报告 12 项发现全部属实并已修复：可选依赖延迟导入 / Bridge 并发 pending（`_pending` 单槽 → req_id 字典）/ fallback 降档生效（拆两阶段）/ 优雅 close / 事件线程统一（call_soon_threadsafe）/ ASR 契约补 `fetch_result` / `session.proxy` 注入生效 / OSS 滚动清理按 `last_modified` 排序 / 域名精确匹配（`host_matches`）/ renderTabs 双写 storage.session / 删 3 个无效配置 / lxml FutureWarning。明确未做：不引入 DDD/Repository/DI、不做任务强制取消（to_thread 线程不可取消，close 只能优雅等待）。完整清单见 `DELIVERY.md §2` 阶段十，经验见 LESSONS 31-35。

---

## 5. 经验总结

### 5.1 本轮新增（LESSONS 36-38 + 上轮 31-35，详细见 LESSONS.md）

1. **任务级信号量 = 卡脖子**：并发池粒度必须匹配"资源"而不是"任务"。轮询/退避这类"等待型"阶段（ASR 轮询可占 8-10 分钟）绝不能占共享槽——一个任务占满全局槽，其他任务全排队。解法：阶段级信号量 + 阶段函数拆分，状态机/事件/关闭语义不动。
2. **`ThreadPoolExecutor` 保序取结果**：`futures = [ex.submit(...) for ...]` 后**按列表序** `fut.result()`——天然保序且仍是真并行（总耗时 = 最慢切片），不需要 `as_completed` + 手动排序。
3. **httpx 与 urllib 行为差异**：`urllib` 只在 HTTP 错误时抛 `HTTPError`（异常即错误）；`httpx` 默认**不抛**，必须显式查 `status_code`（≥400 时 `r.text` 取错误体）。`httpx.post(json=...)` 自动序列化 + Content-Type，代理用 `proxy=` 参数。
4. **可选依赖绝不能在模块顶层无条件 import**——`import medialab` 的导入链很长（service→article→bridge/oss），任何一环的顶层 import 都会炸掉整个包的导入。延迟到真正使用点，并抛带安装提示的错误。
5. **单 pending 槽 + 并发 = 丢请求**；且超时清理写 `self._pending = None` 会连别人的请求一起清掉。等待者容器必须按请求 id 索引，清理只动自己的槽。
6. **"最后一次 continue"是死代码**——`for range(N)` 里最后一轮 `continue` 后没有下一轮。这种"重试再试一次"的写法只能用**测试断言第二次调用真的发生**来防回归（现在有 `TestFallbackFormat`）。
7. **`asyncio.to_thread` 的线程无法被取消**：取消外层 task 只是放弃等待，线程照跑。凡是"任务在线程 + 资源在 asyncio 层"的生命周期，必须优雅等待而不是取消；事件跨线程必须 `call_soon_threadsafe` 统一回 loop 线程。
8. **lxml Element 的真值判断在 6.x 已发 FutureWarning**（无子节点元素为 False，未来恒 True）——`_parse(x) or fallback` 这类写法必须改 `is not None`。

### 5.2 既有经验浓缩（完整原文见 LESSONS.md）

- **WS 桥**：websocket-server 0.6.4 读大消息截断且不支持 continuation 帧，必须覆写 `read_next_message`；响应必须回显 `req_id` 匹配；MV3 SW 空闲 30s 挂起断 WS，用 alarms 0.5min 重连 + ping 探测半死连接。
- **站点抓取**：微信正文只在可见标签页渲染（pending 时激活重试）；小红书 SSR JSON 嵌 JS `undefined` 要清洗、无签名图片 URL 返回 32×32 占位图（校验内容真实性不能只看状态码）；知乎 hydration content 可能是段落对象数组。
- **YouTube**：cookie 会被 Google 轮换撤销（时间戳不可信，12h 过时提醒 + 无痕导出法）；**扩展铸造的 visitor_data + 会话 cookie 可绕过播放器登录墙**（LESSONS 23，重要）；PO Token 服务只解流级 403，解不了登录墙，已弃。
- **平台**：B站/抖音 503 是暂时的（指数退避 30s 起步可恢复）；抖音无 cookie 直接拒绝。
- **千问 ASR**：纯异步模型（必须 `X-DashScope-Async: enable` 头）；`file_urls` 只允许 1 个链接（每切片单独提交）；`diarization_enabled` 才能拿到 speaker_id；transcription_url 域名要代理；"SUCCESS_WITH_NO_VALID_FRAGMENT" 是跳过不是重试。
- **OSS**：V4 签名必须传 region；签名 URL 只对 GET 有效（验证用 GET+Range，总大小在 Content-Range）；上传显式 Content-Type。
- **代码级**：`SequenceMatcher` 对比必须 `autojunk=False`；ffmpeg 子进程必须 `encoding="utf-8", errors="replace"`；atempo >2x 需级联；临时 cookie 别放产物扫描目录；错误分类用精确短语别用裸词；POT 插件必须放 `yt_dlp_plugins/extractor/`；清理脚本正则必须锚定文件名前缀（LESSONS 30 血泪）。

---

## 6. 功能使用手册

### 6.1 安装与环境

```bash
pip install -e ".[cloud]"        # 基础 + OSS；bridge 功能再加 ",bridge"
# 可选依赖：oss2（cloud）/ websocket-server、websocket-client（bridge），
# 不装也能 import medialab，用到时才报错提示
```

环境：Python ≥3.10（项目用 3.12.11，mise 管理）；ffmpeg（配置→PATH→mise 探测）；Deno 2.9.4（mise）+ yt-dlp-ejs（YouTube JS challenge）；代理 `http://127.0.0.1:10808`（`[download].proxy` 仅 YouTube，`[article].proxy` 供海外文章）。

### 6.2 核心 API（全部 async）

```python
import asyncio
from medialab import DownloadService

async def main():
    svc = DownloadService(config=None, on_event=lambda ev, p: print(ev, p))
    await svc.initialize()   # 自检：db/ffmpeg/cookie 过期告警/OSS/refine；标记上次残留任务

    # ① 统一入口：router 按域名规则表分派（视频→下载，文章→抓取）
    t = await svc.submit("https://mp.weixin.qq.com/s/...")
    await t.wait(svc)

    # ② 视频下载 / 转音频（mp3/m4a/aac/flac/wav/opus/ogg）
    t2 = await svc.download("https://www.bilibili.com/video/BV...", audio="mp3")
    await t2.wait(svc)

    # ③ 全自动转写：下载→转音频→切片→倍速→OSS→千问→对齐→文章化
    t3 = await svc.transcribe("https://youtu.be/...", speed_factor=1.5)
    await t3.wait(svc)
    r = await svc.asr_result(t3.id)          # {"segments": [{text,start,end,speaker_id}]}

    # ④ 对已完成转写的任务单独补跑文章化（不重新下载/转写）
    await svc.refine(t3.id)                  # 产物 article.md（已存在则不覆盖）

    # ⑤ 文章/图片抓取（白名单站点需要 Chrome 会话桥）
    t4 = await svc.scrape("https://www.zhihu.com/question/...")
    await t4.wait(svc)

    # 查询 / 等待 / 关闭
    status = await svc.query(t.id)
    rows = await svc.list(limit=50)
    await svc.close()                        # 优雅关闭：等现有任务完成后再关库

asyncio.run(main())
```

任务状态机：`queued → running → done/failed`（崩溃残留自动标记 `interrupted`）。事件：`task_started / task_progress / task_done / task_failed / cookie_expired`，**全部在事件循环线程回调**（节流 1s）。

**并发（阶段级，本轮新增）**：`download/transcribe/scrape/submit` 默认 `wait=False` 立即返回 handle，宿主可同时丢入大量链接；任务在**阶段级信号量**（下载/抓取/ASR/润色各 `max_concurrent`，默认各 3）下运行，阶段间互不占槽——转写任务的 ASR 阶段慢/重试不阻塞其他任务的下载与抓取；ASR 阶段内部切片提交/轮询并行（`[asr].slice_concurrency`，默认 3，RPM 600 允许 3-5）。

### 6.3 浏览器会话桥（可选，登录墙场景必需）

```python
from medialab.bridge import SessionBridgeServer, wait_for_plugin
from medialab.session import ExtensionSessionProvider

server = SessionBridgeServer()  # 默认 127.0.0.1:19876
server.start()
svc = DownloadService(session_provider=ExtensionSessionProvider(server))
# Chrome 加载 browser-extension/（开发者模式 unpacked）→ 自动连接 → 授权域名
```

服务端在 loopback 信任边界内以 `chrome-extension://` Origin 阻止普通网页，并为每次连接
下发只驻内存的 session token；断线即失效，重连自动轮换。Origin 可被恶意本地 raw
socket 伪造，不是密码学身份认证，因此 bridge 不得监听非本机地址。yt-dlp `role=pot`
使用服务进程内部自动鉴权，无用户配置。

- **P1 直读浏览器库**（零插件）：`DownloadService(session_provider=BrowserCookieProvider("chrome"))` 或配置 `[download].cookie_source = "browser:chrome[:Profile]"`。
- 会话策略：`[download].session_first_platforms`（默认 bilibili/douyin/youtube）直接带会话；其余平台匿名优先、auth 类失败才升级（每任务最多一次）。
- 临时 cookie 落 `tmp/{task_id}/session/`（子目录，防被产物扫描误收），任务结束清理。
- YouTube 扩展会话还会铸造 `visitor_data`（+尽力 PO Token），绕过播放器登录墙（LESSONS 23）。
- 非小红书文章站点使用通用直连/fetch/render 规则。
- **小红书**：必须当前 Chrome SessionBridge，但扩展只 `request_session` 导出 cookies+UA。每任务独立 `curl_cffi` Session 对页面 GET 一次并同源分流，每个去重图片或唯一视频 stream 各 GET 一次；无 HEAD/probe/retry/fallback。

### 6.4 配置体系（三级合并：默认 toml < secrets.toml < 宿主 dict）

完整键表见 `ARCHITECTURE.md §5` / `DELIVERY.md §6`。要点：

- **OSS 启用规则 = `secrets.toml [oss]` 密钥齐全**（无独立开关）；`[oss] max_batches/ttl_seconds` 控制滚动清理。
- `[asr]` model/language_hints/diarization_enabled/poll 间隔；`[refine]` 润色模型/温度/分块上限/zh_threshold（英文自动出中文版阈值）。
- `[download]` format/retries/risk_retries/fallback_format（现在真的会执行）/proxy/js_runtimes=["deno"]/cookie_source/session_first_platforms。
- 密钥只放 `config/secrets.toml`（模板 `src/medialab/config/secrets.toml.example`）。

### 6.5 产物与数据

| 位置 | 内容 |
|---|---|
| `LIBRARY_ROOT/{中文标题}/` | 资料库规范布局（echo producer-guide）：`manifest.json`（必须）+ `article.md` + `transcript.json` + `media/original.*` + `media/playback.*` + `images/`；资料库根优先取 `LIBRARY_ROOT` 环境变量，缺省 `[paths].delivery`；同名按 manifest.id 区分（同 id 并入 / 不同 id 加 `-2` 后缀）；article.md 已存在不覆盖 |
| `medialab.db` | tasks/files/asr 三表（WAL），任务历史 |
| `tmp/` | 任务隔离临时目录，用后即删 |
| `cookies/` | 各平台 Netscape cookie 文件（改文件即换 cookie） |

### 6.6 测试与验证

```bash
mise exec '--' uv run pytest components/medialab/tests -q
```

覆盖：config 合并 / cookies stale / platforms（含域名负例）/ store CRUD + interrupted / ASR 对齐（含并行切片语义）/ 切片倍速 / refine 分块（httpx mock）/ 错误分类 / 会话层 / bridge（Origin/连接会话鉴权、req_id、并发双请求、超时不误删、断线轮换）/ 小红书任务 Session、每 URL 单次 GET、共享 Broker 注入、generation 去重刷新、刷新错误边界、HTTPS/域/重定向目标、Referer token 脱敏、Cookie 域与 revision CAS、秘密不进入 repr/错误信息 / **fallback 降档真的二次调用** / **可选依赖缺失时 import** / **close 等待线程** / proxy 注入 / router 分派 / article 适配器 / **xhs_video** / **publishing**。

**小红书回归位置**：`tests/test_service.py` 的单任务编排、`tests/test_article.py` 的 captured 严格图片用例、`tests/test_xhs_client.py`、`tests/test_xhs_video.py`、`tests/test_bridge.py` 的会话请求用例。真实回归当前是一条成功、一条修复后待复测。

真机联调：`examples/host_demo.py`（下载）、`examples/e2e_transcribe.py`（全流程转写）、`examples/session_bridge_demo.py`（扩展会话）、`examples/article_demo.py`（文章，`--bridge` 走 Chrome）。

---

## 7. 设计总览

### 7.1 模块地图

```
medialab/
├── service.py          # DownloadService：唯一公共门面；async API；并发池；事件分发；会话获取
├── router.py           # submit 类型分派：域名规则表 + URL 模式例外（纯规则，不预检）
├── downloader.py       # yt-dlp 封装：opts 构建/cookie 注入/进度节流/风控退避/降档/匿名升级
├── platforms.py        # URL→平台（精确域名匹配）/extractor 白名单/会话域表
├── cookies.py          # Netscape 解析 + 过期/过时感知
├── audio.py            # ffmpeg：transcode / slice（尾部空片降级）/ speed（atempo 级联）
├── oss.py              # 上传/V4 预签名/URL 验证/滚动清理（last_modified 排序）
├── asr.py              # AsrProvider 契约（submit/poll/fetch_result）+ 千问实现 + 切片管线 + 对齐
├── refine.py           # 转写稿→文章（qwen3.7-flash，中/英，分块）
├── article.py          # 通用文章升级链 + 小红书同一任务 Session 图文产物
├── xhs_client.py       # 小红书任务级 curl_cffi Session + 内存凭证协作
├── xhs_video.py        # 单次页面 HTML 解析 + 唯一视频码流选择
├── site_adapters.py    # 站点适配器注册表（微信/知乎/头条/X/小红书 + trafilatura 兜底）
├── publishing.py       # echo 资料库规范产物编排：manifest / transcript.json / media 转码 / 同名目录决策
├── session.py          # FailureCode 分类/BrowserSession/SessionProvider/Netscape 写入
├── bridge.py           # 本地 SessionBridgeServer（WS；Origin/连接会话鉴权；req_id→pending；请求转发）
├── store.py            # SQLite（WAL + busy_timeout + 线程锁）：tasks/files/asr
├── config.py           # 配置三级合并
└── events.py           # 事件定义 + 线程安全派发（call_soon_threadsafe）
```

### 7.2 关键设计原则（维持现状，不要改）

1. **进程内库，唯一门面**：MediaLab 核心对外走 `DownloadService`，默认不引入 HTTP 服务/认证层；可选 SessionBridge 和 Echo 集成面例外。
2. **阻塞工作在 to_thread 线程池**（阶段级 Semaphore，默认每阶段 3）：yt-dlp 是阻塞库；**线程无法被取消**——所以 close 是优雅等待，事件统一回 loop 线程。转写拆为下载/ASR/润色三阶段独立信号量，ASR 阶段切片提交再并行（slice_concurrency），一个任务卡在 ASR 不阻塞其他任务的下载。
3. **SQLite + WAL + 单连接 + 调用方锁**：当前线程规模下不需要 ORM。
4. **分层重试**：yt-dlp 内置(10) → 平台风控退避(3) → 降档格式(1) → ASR 重提交(3) → 无语音片跳过。各层失败模式不同，别合并。
5. **staging 目录**：先下载到 `delivery/.staging-{task_id}`，拿到标题后重命名为标题目录，避免半成品污染交付目录。
6. **错误分类集中**：`FailureCode` 枚举 + `tasks.error_code` 4 类兼容层（auth/network/unsupported/other），`classify_error` 用精确短语匹配。
7. **宽泛异常捕获只出现在外部系统边界**（OSS/refine/图片下载/桥接），以及"失败不致命"路径；业务主链不铺 try/except。
8. **站点适配器以真实失败证据驱动**：不为理论场景加补丁（小红书占位图、微信可见性这些全是实测踩出来的）。
9. **不做的边界**：不引入 DDD/Repository/CQRS/DI 容器、不给每个配置段建模型、不把所有函数包 Result、不抽象"万能媒体流水线框架"。

### 7.3 线程与事件模型（本轮修正后的正确理解）

```
宿主事件循环线程 (asyncio)
  ├── DownloadService 全部 async API、事件回调、close()
  ├── Emitter: emit() 一律 call_soon_threadsafe → _invoke() 在 loop 线程执行
  └── 阶段级信号量（download/scrape/asr/refine 各 Semaphore，默认各 3，互不占槽）
        └── asyncio.to_thread(worker)         ← 阻塞工作线程池
              ├── _run_download / _run_scrape
              └── 转写三阶段：_transcribe_download → _transcribe_asr → _transcribe_refine
                    ├── store 写操作（线程锁保护）
                    └── SessionBridgeServer._request（阻塞等 WS 响应）
              └── ASR 阶段内部：切片提交/轮询 ThreadPoolExecutor(slice_concurrency，默认 3) 并行
WS 服务器线程（bridge）→ _on_message → pending[req_id] 匹配 → event.set() 唤醒等待线程
```

要点：**事件回调永远在 loop 线程**；**worker 线程在任务自然结束前不可中断**（close 必须等）；**bridge 的并发请求各自独立**（按 req_id 字典，不做全局互斥）；**阶段间互不占槽**——一个任务在 ASR 阶段慢/重试不阻塞其他任务的下载/抓取，`max_concurrent` 是每阶段并发上限。

### 7.4 会话桥协议（扩展 ↔ bridge，见 browser-extension/README.md）

扩展无配置连接 `127.0.0.1:19876`。服务端在 loopback 信任边界内以
`chrome-extension://` Origin 阻止普通网页，再下发仅对当前连接有效的内存 session token；
断线销毁，重连轮换。Origin 不抵御恶意本地 raw socket 伪造；`role=pot` 由服务进程内部自动鉴权。

通用协议含 `get_session / mint_yt_token / fetch_page / render_page / render_poll / render_close / ping`。小红书只使用 `get_session` 导出当前 profile cookies 与 UA；扩展不调用小红书 `fetch_page`/render，也不发任何目标站网络请求。页面与资源均由 Python 任务级 `XhsClient` 完成。

---

## 8. 已知问题与后续方向

1. X 视频推文 URL 级区分未做（`/video/` 后缀已覆盖，`x.com/status` 默认文章）；X oEmbed 直连需代理实测
2. 头条滑块/风控场景未真机验证（验证码交互路径已就绪）
3. ~~ASR 切片串行~~（已并行化：切片提交/轮询 ThreadPoolExecutor 默认 3 路，`[asr].slice_concurrency` 可调；1 小时音频约 8-10 分钟 → 3-4 分钟）
4. diarization 对倍速脆弱（混叠内容 1.5x 塌缩，无自动降级）
5. YouTube 匿名成功率基线待干净 IP 补测；PO Token 按需链路待真实 GVS/Player 403 验证
6. Native Messaging 正式化（打包时替换 WebSocket 传输层，协议已解耦）
7. 同标题视频并发任务写同一目录（单文件冲突有 task 前缀兜底）
8. 当前 Echo Reader 根已有 `.git`，但尚无 commit、`git ls-files=0`；任何清场前先建立快照或基线提交
9. **小红书真实帖子 E2E 未完全闭环**：第二条图文已成功；第一条修复 HTTP CDN URL 规范化后未重跑。
10. **小红书链接凭据与签名有时效**：应使用用户当前 Chrome 会话中的完整链接，并在任务级 Session 建立后立即完成页面与资源单次请求。

## 9. 改代码前必读

1. 先读 `LESSONS.md`（按章节和标题检索，历史编号跨分类重复）——多数"看起来对的写法"都在里面被证伪过
2. 扩展与 Python 是两套部署：Python 改完即生效；扩展改动要 `chrome://extensions` 重新加载
3. 改 bridge 后必须用大页面（文章 HTML >100KB）真机回归——WS 分片路径
4. 改下载流程后跑 `tests/test_downloader.py`（fallback 二次调用是重点回归项）
5. 文本对比永远 `autojunk=False`；Windows 控制台中文乱码是 GBK 显示问题不是 bug
6. 清理/批处理脚本正则必须锚定文件名前缀（LESSONS 30）
7. 改并发相关（信号量/线程/阶段拆分）后跑全量 `pytest tests -q`：阶段信号量、切片并行、close 语义都在现有用例覆盖内
8. 换 HTTP 客户端必须同步换测试 mock 层（LESSONS 39 血泪：urllib→httpx 时 test_refine 发过真请求）
