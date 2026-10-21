# 经验教训（LESSONS）

实施过程中踩过的坑和沉淀的经验，按类别记录。**修 bug 或扩展功能前先读这份**。

## 一、代码级（最容易再次踩的）

1. **`with sqlite3.Connection` 块内调用 `conn.close()` 会炸**
   `sqlite3.Connection` 的上下文管理器退出时会 commit/rollback，若连接已 close 则抛 `ProgrammingError: Cannot operate on a closed database`。close() 只用锁、不在 with 块内。
   （`store.py close()` 踩过，测试 tearDown 里报错半天）

2. **yt-dlp 的 `requested_downloads` 是 dict 列表，不是路径列表**
   `info["requested_downloads"]` 每个元素是 dict，取 `d["filepath"]`。直接 `Path(p)` 会 `TypeError: not 'dict'`。
   （downloader.py `_run_ydl` 踩过）

3. **Python API 的 `extract_info` 不打印格式列表**（CLI 的 `-F` 才会）
   自己渲染格式表格，否则用户以为卡死。

4. **yt-dlp Python API 选项格式与 CLI 不同**（实测确认）：
   - `js_runtimes` 必须是 dict：`{"node": {}}`（传 list 报 `ValueError: Invalid js_runtimes format`）
   - `remote_components` 必须是 list：`["ejs:github"]`（传字符串被拆成字符集；传 dict 的 key 不匹配）
   - `allowed_extractors` 是 list（正则匹配 extractor 名）

5. **PowerShell 调用坑**：
   - 空字符串参数 `""` 传给原生 exe 会被**整个丢弃**，导致后续参数全部错位（demo 脚本踩过，用 argparse 解决）
   - `mise exec -- ffmpeg ... -q:a 2 ...` 含冒号的编码参数会被 mise 传坏（"Unable to choose an output format for 'libmp3lame'"），**直接调用 `mise which ffmpeg` 拿完整路径**，不走 mise exec 转发

6. **ffmpeg 子进程在 Windows 的编码**：中文文件名/输出导致默认 GBK 解码崩溃（`UnicodeDecodeError`）。所有 subprocess.run 必须 `encoding="utf-8", errors="replace"`。
   （audio.py `_run` 踩过，stderr 变 None 时切片静默失败）

7. **mp3 源不能 `-c copy` 切到 .m4a**：容器与编码不匹配（ffmpeg rc=-6）。切片目标扩展名跟随源格式。

8. **ffmpeg 的 BtbN 构建带 `libmp3lame` 但转码命令要写对编码器名**；`ffprobe` 与 `ffmpeg` 同目录（`Path(ffmpeg_path).with_name("ffprobe.exe")`），Windows 大小写不敏感。

9. **`difflib.SequenceMatcher` 的 autojunk 会毁掉英文长文本对比**：对 >2000 字符的英文文本，autojunk 启发式把高频字符当垃圾忽略，相似度严重低估（实测 0.324 vs `autojunk=False` 的 0.974）。**做文本相似度对比必须传 `autojunk=False`**。中文文本不受影响（低频字）。

10. **`-c copy` 切片在文件尾部会产出空文件（无流）**：B站 m4a 含 edit list/时间基偏差（如 ffprobe 报 300.010667s），切 `[300, 301]` 得到 314B 空文件 → 上传千问报 "have no audio"。修复：`audio.slice` 检测 <1024B 自动降级重编码（编码器按目标扩展名选）；`AsrPipeline.slice_audio` 跳过尾部 <1s 的碎片（其内容已被前一片重叠覆盖）。

11. **千问 "SUCCESS_WITH_NO_VALID_FRAGMENT" / "have no audio" 是"无语音"不是错误**：纪录片里的纯音乐段（实测 hainan 3:00-4:01）会返回该码。不要重试 4 次，`NoSpeechError` 直接跳过该片继续。

12. **ffmpeg atempo 单滤镜上限 2.0，>2x 需级联**：`audio.speed` 最初 guard 把 >2.0 直接拒绝（级联代码写了但执行不到），3x 全挂。修复后 0.5~16x 自动级联（3x = atempo=2.0,atempo=1.5）。

13. **Chrome Cookie 库锁是"间歇性"的不是"永久"的**：`--cookies-from-browser chrome` 在 Chrome 运行中多数时候能读（SQLite WAL 并发读），只有写事务瞬间报 "Could not copy Chrome cookie database"（yt-dlp issue 7271）。所以 `cookie_source="browser:chrome"` 可以直接作为默认路径，失败时给可操作提示即可，不必要求用户关浏览器。

14. **yt-dlp-ChromeCookieUnlock 插件的机制是"强杀浏览器"**：它用 Windows Restart Manager（`RmShutdown` + `RmForceShutdown`）强制终止持有 Cookie 库锁的进程——指向用户主 profile 会**强杀用户的 Chrome**（标签页全丢）。实测兼容 yt-dlp 2026.7.4（monkeypatch `_open_database_copy` 仍有效），且只杀持有目标 profile 锁的进程。**默认不启用**；如需解锁，配合"下载专用 profile"使用。

15. **MV3 Service Worker 空闲 ~30s 会被 Chrome 挂起，WS 连接随之断开**：扩展无配置连接本机 SessionBridge，但 SW 挂起后 socket 仍可能半死，服务端发请求无响应（表现为"请求超时"而非"未连接"）。解法：`chrome.alarms` 每分钟唤醒重连 + popup 打开即发 `bridge_reconnect` 唤醒，服务端 ping 探测半死连接。每次连接在 loopback 边界内先以 `chrome-extension://` Origin 阻止普通网页，再下发只驻内存的 session token；断线清除、重连轮换，不依赖用户保存或输入连接凭据。Origin 可被恶意本地 raw socket 伪造，不能替代“不向非本机暴露”的安全边界。

16. **`chrome.permissions` 的 origin pattern 必须带路径**：`chrome.permissions.contains/request({origins: ["https://bilibili.com"]})` 会抛 `Invalid value for origin pattern ...: Empty path`，必须写成 `https://bilibili.com/*`。排查时扩展返回 `handler_error`（详见下条）。

17. **扩展处理异常要回传服务端**：SW 里 `handleMessage` 的异常若不 catch，服务端只能看到 20s 超时。onmessage 里 try/catch 并把 `{error: "handler_error", detail: str(e)}` 发回，联调效率天差地别（本问题就是这么定位的）。

18. **临时 Cookie 文件不能放在下载器扫描产物的目录**：`_collect_outputs` 扫 `tmp/{task_id}/` 顶层文件当下载产物，`session.cookies` 放进去会被误收并拿去 ffmpeg 转码（"Error opening input file session.cookies"）。临时会话文件放 `tmp/{task_id}/session/` 子目录。

19. **错误分类不要用裸词匹配**：`classify_error` 曾用 `"cookie" in msg` 匹配 auth——错误信息里的路径 `session.cookies` 直接误判成 auth。改为精确短语（"cookie file"/"cookie database"/"cookies are needed"/"sign in"…）。

20. **yt-dlp 的 POT Provider 插件必须放 `yt_dlp_plugins/extractor/` 目录**：yt-dlp 只注册了 extractor/postprocessor 两个 PluginSpec；`yt_dlp_plugins/pot/` 下的模块永远不会被导入。放在 extractor/ 下会在启动时被加载，`@register_provider` 随之注册到 POT 注册表。（2026.7.4 实测；bgutil 1.3.1 也是这么做的）

21. **MV3 权限三连坑（P2a/P3b 联调实测）**：
    - `chrome.permissions` 的 origin pattern **必须带路径**（`https://bilibili.com/*`，裸 origin 抛 "Empty path"）
    - 子域名不匹配裸域 pattern：`https://youtube.com/*` 不覆盖 `www.youtube.com`——每个域名要授权**两个 pattern**（裸域 + `https://*.域/*`）
    - `chrome.permissions.request` **必须在用户手势上下文（popup）调用**，经 Service Worker 中转报 "must be called during a user gesture"

22. **`chrome.scripting.executeScript` 默认隔离世界**：读页面主世界的 `window.ytcfg` 必须加 `world: "MAIN"`（否则静默返回 undefined，表现为"拿不到 Visitor Data"且无报错）。

23. **YouTube 登录墙的突破路径（实测）**：被 "Sign in to confirm" 拦截的 IP 上，**扩展铸造的 visitor_data + 当前会话 Cookie 即可绕过播放器登录墙**（qM4AWORRT48 实测下载成功）——不需要账号登录、不需要 PO Token。PO Token 只解决流级 403（GVS），按需生成即可。

24. **websocket-server 0.6.4 读大消息截断（P4 实测）**：`read_bytes()` 单次 `rfile.read(num)` + **不支持 continuation 帧**——Chrome 对 >16KB 的 WS 消息会分片发送（实测 ~131KB 文章 HTML 稳定触发），库只读第一帧 → 尾字节残缺 → `UnicodeDecodeError` → 连接死。修复：`_PathAwareHandler` 完整覆写 `read_next_message`（帧聚合 + 续帧 + 可靠循环读）。**教训：浏览器 WS 客户端发送大消息必然分片，服务端必须处理 continuation 帧。**

25. **WS 响应必须按 req_id 匹配（P4 实测）**：服务端每 5s ping 一次，扩展对 ping 也会回复（unsupported_message）——若回复恰在请求 pending 期间到达，单 pending 设计的 bridge 会把它误认成请求响应（表现为 `render_page 失败: unsupported_message`）。修复：请求带 `req_id`，扩展回显，`_on_message` 不匹配则归还 pending；扩展对 ping 直接忽略不回复。

26. **微信公众号正文只在可见标签页渲染（P4 实测）**：`chrome.tabs.create({active:false})` 后台标签页打开微信文章，30s 内 `innerText` 仍为空壳（懒加载抑制）；**前台手动打开正常**。修复：`render_page` 首次探测 pending 时 `tabs.update({active:true})` 激活后重试一次。教训：后台标签页渲染对"可见性敏感"的站点（公众号/部分 SPA）无效，需要激活重试。

27. **小红书 INITIAL_STATE 不是严格 JSON（P4 实测）**：`"pwaAddDesktopPrompt":undefined` —— 小红书在 SSR JSON 里嵌入 JS `undefined`，`json.loads` 直接失败。修复：解析前 `re.sub(r":undefined\b", ":null", ...)`。教训：大厂 SSR JSON 常混 JS 语法，raw_decode 前必须清洗。

28. **小红书无签名图片 URL 返回 32x32 占位图（P4 实测）**：`fe-platform.xhscdn.com/platform/...` 这类 `urlDefault` 源在无签名时返回 200 + 32×32 占位 PNG（**不是 403**）。任务级 `XhsClient` 下载图片后必须校验 MIME、大小与内容真实性，不能只看状态码；任一声明图片不合格即整任务失败。

29. **WS 大消息分片后的教训延伸（P4 实测）**：`_on_message` 收到完整消息后，JSON 响应必须回显 `req_id` 才能匹配请求——服务端 ping 触发扩展的 unsupported_message 回复会被误认成请求响应（详见 #25）。本轮补齐：扩展对 `type==="ping"` 直接忽略不回复（减少无效流量），响应统一 `reply.req_id = msg.req_id`。

30. **清理脚本必须精确匹配（运维教训）**：用正则 `\.([0-9a-f]{6,12})\.` 清理文章 task 前缀副本时**误删了 yt-dlp 的 format_id 变体文件**（`[BV...]30280.2fda40.m4a` 里的 `2fda40` 是格式 ID，不是任务前缀）。任务前缀固定 12 位 hex 且只出现在 `article./meta.` 文件名上——清理表达式必须锚定文件名前缀（`^(article|meta)\.`），禁止裸正则扫描所有文件。

31. **可选依赖绝不能在模块顶层无条件 import（全库审查实测）**：`import medialab` 的导入链是 `__init__ → service → article → bridge/oss`，`bridge.py` 顶部 `from websocket_server...` 和 `oss.py` 顶部 `import oss2` 都是 optional extras 里的包——缺装时整个包 `import` 直接失败（README 推荐装 `.[cloud]` 也会炸）。修复：延迟到真正使用点（`SessionBridgeServer.start()` / `OssClient.__init__`）再导入，缺包抛 `RuntimeError("pip install 'medialab[cloud/bridge]'")`。教训：**凡是声明为可选的依赖，模块顶层 import 一律禁止**；测试用 `sys.meta_path` 拦截模块验证（tests/test_imports.py）。

32. **单 pending 槽的 bridge 并发丢失 + 超时误删他人（审查实测）**：`SessionBridgeServer._pending` 原来是单个对象，下载服务默认 3 并发时第二个请求直接覆盖第一个；且第一个超时后 `self._pending = None` 会把第二个的等待者一起清掉（两个都超时）。修复：`_pending` 改为 `{req_id: _PendingRequest}` 字典，`_on_message` 按响应 req_id 精确 pop；超时只清自己的槽；连接整体失效（断线/被替换/ping 失败）才 `_fail_all_pending` 唤醒全部。教训：**共享可变等待槽 = 并发 bug；按请求 id 索引是最小解**（回归测试：test_bridge 并发双请求 + 超时不误删）。

33. **"最后一次 continue"是死代码（审查实测）**：`for attempt in range(max_risk + 1)` 循环里，最后一次失败时 `used_fmt = fallback; continue`——循环已没有下一轮，`continue` 什么都不做，降档重试**从未执行**（日志打了"降档重试"但 `_run_ydl` 只调了一次）。修复：拆成两个显式阶段（风控退避循环 → 最后一次失败才降档格式再调一次），并新增测试**断言第二次调用真的发生**（`TestFallbackFormat.test_risk_exhaustion_triggers_fallback_second_download`）。教训：**"循环里最后一次重试"类逻辑必须用调用次数断言防回归**。

34. **`asyncio.to_thread` 的线程无法被取消（审查确认）**：取消外层 asyncio task 只放弃等待，线程照跑——旧 `close()` 取消任务后立即 `store.close()`，后台线程可能写已关闭的 SQLite 连接。修复：`close()` 改为**优雅关闭**（gather 等所有任务自然完成再关库），移除"强制取消"假接口；事件跨线程统一 `loop.call_soon_threadsafe` 回 loop 线程。教训：**线程里的阻塞工作（yt-dlp/ffmpeg）是不可中断资源，生命周期只能等待，不能取消**。

35. **lxml Element 的真值判断已发 FutureWarning（审查实测）**：`_parse(content) or _empty()` 依赖"无子节点元素为 False"的布尔语义——测试运行时知乎/头条适配器各触发一次 FutureWarning（未来 lxml 恒为 True，行为会变）。修复：显式 `node is not None`。教训：**库类型 (lxml/requests 等) 的真值语义是隐式契约，见到 FutureWarning 立即改显式判断**。

36. **任务级信号量 = 卡脖子（并行化实测）**：原 `_sem`(Semaphore(3)) 是任务级——一个转写任务占满 1 槽 8-10 分钟（ASR 轮询等待为主），3 个转写任务把下载/抓取全堵死。修复：拆**阶段级信号量**（download/scrape/asr/refine 各 max_concurrent，互不占槽），`transcribe` 拆 `_transcribe_download → _transcribe_asr → _transcribe_refine` 三阶段函数，状态机/事件/close 不动。教训：**并发池的粒度要匹配"资源"而不是"任务"；等待型阶段（轮询/退避）绝不能占共享槽**。当时回归记录为 134 用例通过（历史数字）。

37. **`ThreadPoolExecutor` 按列表序取结果 = 保序 + 真并行（并行化实测）**：`futures = [ex.submit(fn, i, ...) for i in range(n)]` 后直接 `[fut.result() for fut in futures]`——结果天然按 i 排序（无需 `as_completed` + 手动排序），且仍全部并行（总耗时 ≈ 最慢一个）。教训：**提交序 = 收集序，是保序并行的最小写法**。

38. **httpx 与 urllib 的错误处理差异（迁移实测）**：`urllib.request.urlopen` 对 4xx/5xx 抛 `HTTPError`（异常即错误，错误体在 `e.read()`）；`httpx` 默认**不抛**，必须显式 `r.status_code >= 400` 分支（错误体 `r.text`）。`httpx.post(url, json=body, headers=..., timeout=...)` 自动 JSON 序列化 + `Content-Type: application/json`；代理用 `proxy="http://..."` 参数（httpx 0.27+ 单数 `proxy`，不是 `proxies` dict）。测试 mock：patch `httpx.post` 而非 urllib opener。

39. **`urllib` 迁移 httpx 时测试 mock 要跟着换（迁移实测）**：`test_refine.py` 原 mock `refiner._opener`（urllib opener 对象）；换 httpx 后改为 `mock.patch("medialab.refine.httpx.post", new=...)`，且断言从 `args[0].data`（urllib Request 对象）改为 `kwargs["json"]`（httpx 的 body dict）。教训：**换 HTTP 库必须同步换测试的 mock 层，否则测试会发真网络请求**（本次 401 就是这么暴露的）。

## 二、OSS 级

9. **V4 签名必须传 region**：`oss2.Bucket(..., region="cn-beijing")`，否则 `ClientError: The region should not be None in signature version 4`。

10. **签名 URL 的 206 响应**：`GET + Range` 返回 206 时 `Content-Length` 是分片大小（如 1），**总大小在 `Content-Range` 头**（`bytes 0-0/844964`）。大小验证从 Content-Range 取。

11. **AccessDenied "The bucket you access does not belong to you"** = AK 无权访问该 bucket（bucket 名错 或 RAM 用户未授权）。AK 能 ListBuckets 失败不代表 AK 无效——很多 RAM 用户只授权特定 bucket。排查顺序：bucket 名 → region → RAM 授权。

12. **上传显式 Content-Type**：oss2 按扩展名推断可能给错类型（.txt → text/plain），与我们自己的 URL 验证规则冲突。

## 三、平台级

> 注：条目 40–45 原样记录小红书专用 curl 方案与已删除的 Chrome render 路径。它们解释了
> v3 为什么改变，不是现役 API；现役通用方案见本文 51–56 和 `ARCHITECTURE.md`。

13. **B站/抖音 503 是"暂时性"的**：指数退避 30s 起步重试可恢复，不要立刻放弃；音频轨比视频轨更易被限。

14. **YouTube cookie 会被浏览器安全轮换**：导出后可能几小时就失效（不是过期时间问题，是 rotation）。yt-dlp 报 "cookies are no longer valid" 时让用户重新导出。Cookie 文件里的时间戳只是参考，**实际有效性以 yt-dlp 报错为准**。

15. **千问 filetrans 是纯异步模型**：任何同步调用都 403；轮询间隔 2-5s 即可（官方建议），不要 <1s 轮询。

40. **小红书“浏览器指纹”只能诚实标注近似**：扩展只导出当前 Chrome profile 的 cookies 与 UA，不发目标站请求。Python 使用 `curl_cffi` 0.16 的 `impersonate="chrome"`，当前近似 Chrome 146 的 TLS/HTTP 指纹，并保持预设自己成套的 UA/default headers；profile UA 只存为快照元数据。它不是本机 Chrome 151 的精确副本，不能对外宣称“完整复刻真实浏览器指纹”。

41. **小红书页面与资源要强制任务级单 Session、应用层单次语义**：每任务独立一个 `curl_cffi` Session；页面 GET 一次并用同一 HTML 判断图文/视频，图文每个去重图片、视频唯一选定 stream 各 GET 一次。不得 HEAD/probe/retry/fallback 或切换客户端；任何必需下载失败都让整任务失败。`allow_redirects` 可能在一次 curl 调用内部产生重定向，所以“一次”只指应用层调用。

42. **凭证刷新必须比重试更保守**：只有页面明确 401 或最终 URL 跳到 `/login` 才 generation 去重刷新；资源 401、裸 403、CAPTCHA、429 与 network 错误都不能证明 cookie 失效，不刷新。刷新后当前 URL 仍失败且不重试，新快照只给下一任务；刷新失败清掉已知 stale 快照。成功任务只提交最终 `XhsClient` 的 cookie jar，Broker 校验 cookie 域并以 generation+revision CAS 回写，防止旧任务覆盖新凭证。

43. **知乎 hydration 的标题在 questions 实体**：问题页 `initialState.entities` 里 `questions.{id}.title` 是字符串；`answers.*.title` 是嵌套 dict（拿不到标题）。遍历答案实体时标题会落空 → 全部落"知乎内容"同名目录互相覆盖。先取 questions 标题再遍历内容实体。

44. **懒加载占位图是 data: URI**：知乎/小红书正文里的 `<img src="data:image/svg+xml...">` 是懒加载占位（真实图在 `data-actualsrc`/`data-src` 或后续 img），转 markdown 时必须跳过 `data:` 开头，否则产物满屏 SVG 垃圾。

45. **render 模式会混入 UI 噪音**：扩展 Readability 把页面"X 人赞同了该回答/收起/分享/举报"等按钮文本当正文。适配器可提供 `clean_noise(text)` 钩子（`article._article_from_render` 自动调用），按平台正则清 UI 噪音。

## 四、运营级

16. **cookie 更新方式**：浏览器扩展 "Get cookies.txt LOCALLY" 导出 → 覆盖对应 txt 文件 → 服务下次任务自动用新文件（无需重启）。.gitignore 已排除所有 `*cookies*.txt`。

17. **YouTube cookie 工作流（重要）**：YouTube 的 cookie 会被 Google 安全机制轮换撤销，**时间戳不可信**（实测文件里 `__Secure-3PSID` 有效期到 2027 年，但依然被拦）。每次下载 YouTube 前必须提醒用户**手动重新导出**，导出方法用官方推荐的无痕窗口法：
    - 无痕窗口登录 YouTube → **只开一个标签页**到 `https://www.youtube.com/robots.txt` → 导出 cookie → **立即关闭无痕窗口**（session 从此不被浏览器触碰，才不会被轮换）
    - 导出时浏览器的出口 IP 必须与 yt-dlp 下载用的代理一致（IP 不一致会被 Google 判定 session 异地使用直接撤销）
    - `[cookies].youtube_refresh_hours`（默认 12h）控制"过时提醒"阈值：文件修改时间超过该值时 `initialize()` 会输出 `stale` 级别告警 + `cookie_expired` 事件
    - 实测确认：被 Google 标记的 IP 上，任何客户端（web/mweb/tv/web_embedded/android_vr）都无法绕过 `LOGIN_REQUIRED`，**换 IP 是唯一解**。曾尝试部署 PO Token 服务（bgutil-ytdlp-pot-provider），结论是它只解决流级 403、解决不了播放器登录墙，且增加常驻服务复杂度，**已移除**，不推荐

17. **OSS 计费提醒**：存储 ¥0.12/GB/月、外网流量 ¥0.5/GB。ASR 切片的 3h TTL + 10 批滚动是为了避免测试失败任务无限滞留产生费用。交付物默认**不传 OSS**（本地保留），省钱。

18. **长音频转写成本**：6 分钟视频 → 127 片 × 每片一次 ASR 调用。切片提交/轮询已并行化（`[asr].slice_concurrency` 默认 3，RPM 600 允许 3-5），1 小时音频约 8-10 分钟 → 3-4 分钟。

46. **qwen3.7-flash 长输入"假挂起" = 输出爆炸，不是网络问题**（批量 27 视频实测）：
    - 现象：长视频（>45min）refine 时请求 14-20 分钟无返回，`timeout_seconds=300` 都不触发；同一输入 stream 模式下 TTFT 只有 0.5s（**没有排队**），但输出字节流是 qwen-turbo 的 **4 倍**（复述/扩展原文）
    - 根因：模型在"改写文章"类长输入 prompt 上偶发生成巨量冗余输出（可达 10 万+ token）；非流式请求必须等全部输出完成，以 ~100 token/s 生成速度 ≈ 15-20 分钟 → 看起来"挂起"
    - 注意 httpx 的 `read` timeout 是**两次数据到达的间隔**而非总时长，无法用超时兜住"持续生成"的请求
    - 对策（已落地 refine.py）：① body 带 `max_tokens` 上限（`[refine].max_tokens` 默认 12000）截断爆炸输出；② 总输入 > `max_input_chars × shrink_threshold` 时单块自动压到 `max(4000, max_input_chars//3)`（大块输入更易触发，小块显著降低概率）；③ 服务层 `svc.transcribe(timeout=...)` 任务级超时兜底（超时标记 failed，宿主可跳过继续，不阻塞批次）
    - 实测：同一条 47min 转录（墨脱），修复前卡死 15-20 分钟，修复后 132s 完成
    - 备选：`[refine].model = "qwen-turbo"`——长文本翻译实测 7s（flash 需 35s+），输出克制，批量场景更稳

47. **venv 的 python.exe 是启动器 stub**：Windows 上 `Start-Process .venv\Scripts\python.exe` 返回的 pid 是 stub 进程（CPU 恒 0），它会派生真正的解释器进程（CPU 增长）。**杀 stub 会连带杀真实 worker**（进程树）；判断"是否在跑"要看 CPU 增长的那个，别把 stub+worker 当成双实例并发去杀。

## 五、性能数据（实测）

| 项 | 数据 |
|---|---|
| `import yt_dlp` 内存 | ~34 MB（Python 解释器主导，与 extractor 数量无关） |
| 按需加载 3 平台 extractor | +30ms / +3.2MB |
| 全量加载 938 个 extractor | +2057ms / +34MB（yt-dlp lazy 机制下不会发生） |
| 结论 | **无需删除 extractor**（lazy 加载运行时无感），白名单的目的只是行为可控 |

## 六、Echo Reader 收口新增（2026-08-10）

48. **小红书 HTML 可能给出 `http://` 或 protocol-relative CDN URL**：不能在业务层直接把它当不安全输入丢弃，也不能放宽最终网络安全检查。解法是仅对小红书已授权资源域先用 `normalize_xhs_resource_url` 升级为 HTTPS，再执行域、端口、私网地址与最终重定向校验。真实首帖只请求一次并暴露此问题；修复后按“不自动重试”要求未重跑。

49. **图片文件落盘不等于阅读器可见**：Echo 按 `article.md` 引用渲染图片；只下载到 `images/` 会得到“目录里有图、页面无图”。小红书严格路径必须在全部图片成功后按顺序写入本地 Markdown 引用，任何下载失败仍阻止发布半成品。

50. **会话测试也必须服从运行态目录合同**：`ExtensionSessionProvider` 曾固定写相对 `tmp/session-{job}.cookies`，单测直调时绕过 Service finally，质量门后留下真实 Cookie 文件。Provider 应解析配置的 `[paths].temp`，测试使用独立临时目录；不要靠 broad clean 处理敏感残留。

## 七、全平台浏览器网络通用化（2026-08-10）

51. **“一个模块能 import curl-cffi 0.16”不等于 yt-dlp 支持它**：旧 XHS 路径单独使用 0.16
    可以工作，但锁定的 yt-dlp 2026.7.4 只注册 0.10–0.15 handler，0.16 会被判 unsupported。
    通用化后锁 `curl-cffi==0.15.0`，文章用 `chrome146`，yt-dlp 用
    `ImpersonateTarget("chrome", "146")`。升级必须把 yt-dlp、curl-cffi 与真实 handler smoke
    当作一个兼容单元，不能只看包能否安装。

52. **凭据缓存必须活在宿主生命周期**：若 Broker 放在每个 `DownloadService` 里，而 HTTP 队列
    每个 job 都新建 Service，所谓“缓存”仍会每次请求扩展。Echo import service 现在在 FastAPI
    lifespan 创建一个 `CredentialBroker`，注入所有 Coordinator/Service；独立宿主也必须显式共享。

53. **空 Cookie 是可以缓存的真实结果**：公开站点可能授权域没有任何 Cookie。把空列表当错误会
    让每个任务重新询问扩展，恰好违背“取一次”。Broker 接受并缓存空快照；只有 XHS 策略明确要求
    至少一个 Cookie。

54. **扩展应只承担凭据，不要成为第二个内容网络栈**：Chrome Service Worker fetch、后台 render、
    Python httpx 与 curl 同时存在时，会产生不同 IP/header/TLS、重复页面请求和不可预测 fallback。
    v3 删除 fetch/render/Readability，只保留 `get_session` 与 YouTube 凭据 mint；页面与资源统一由
    Python curl/yt-dlp 请求。

55. **成功后的 Cookie 更新需要 generation + revision 双 CAS**：generation 防旧 auth 快照覆盖刷新
    结果；revision 防两个同代并发成功任务按完成顺序互相覆盖。回写前还要过滤授权域。缓存维护是
    best-effort，绝不能把已完成内容反转成失败。

56. **应用层单次与下载器可靠性要分开说**：文章页面和去重图片可以保证各一个应用层 curl 调用，
    但安全 redirect 仍可能产生多个 wire request；yt-dlp 还需要 manifest、fragment、transport retry
    与恢复下载。把“XHS 不盲重试”机械推广成“所有视频只发一个请求”会破坏成熟下载器正确性。
