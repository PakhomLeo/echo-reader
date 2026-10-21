# MediaLab 现役架构

> 本文档描述当前组件合同。组件旧 HANDOFF/DELIVERY/ROADMAP/LESSONS/DECISIONS 是历史证据。

## 边界与数据流

MediaLab 是内容生产者，不依赖 Echo 内部代码。`DownloadService` 接收 URL，选择会话/适配器/
JS 策略/extractor，抓取正文和媒体、可选转写，最后向 delivery root 发布标准 item。平台
只选择获取方式，不先决定最终 article/media/mixed 类型。

## `submit(auto)`

1. 非 XHS 路径用任务级 Chrome 146 curl session 做一次静态 GET，从同一 HTML 提取正文、图片、
   canonical、HTML/OG/JSON-LD/oEmbed 与 DOM media/embed 证据。
2. JS 全局可用但未配置平台为 off：微信 `always`，知乎/头条 `on_miss`。renderer 用隔离
   Edge context、限域 Cookie、请求/字节/时间/滚动预算，只发现内容而不下载媒体。
3. 至多展开一个 JSON oEmbed，再用 yt-dlp 对原 URL 和候选 URL 做 metadata resolve。
4. 下载全部有限媒体，默认最多 50 个；超过直接失败，不截断无限列表。
5. 正文+媒体或多媒体发布为 `mixed`；摘要进 `source.description`，完整媒体进
   `media_items`，primary 同时映射兼容字段。

adapter 异常被记为 warning 并可转 generic extractor；page/JS/oEmbed/resolve/media 的部分失败保留
去重 warning。若 warning 表明新快照不完整，不允许它覆盖同 ID 的旧完整 item。

## XHS 专用合同

XHS 使用必需 Cookie 的任务级 client，一次页面 capture 同时解析正文、图片和视频。它支持
`__INITIAL_STATE__` 与 `__INITIAL_SSR_STATE__`，按 note ID 取正确水合对象，并将 `//`/`http://`
资源 URL 规范为 HTTPS 后再进入严格域咍/网络校验。

图片按 WB_PRV < WB_DFT < WB_FHD 选最高清、去重并校验内容签名和尺寸（任一边小于
64 px 失败）。视频按 EF7→EF6→EF5→EF4 只选一条 stream，不在同一任务中换流或伪装成
图文成功。页面、图片、视频复用同一凭据快照和 Cookie jar。

## 会话、URL 与 Cookie

Chrome 扩展只提供 `get_session` 和 YouTube token minting，不传输内容。`CredentialBroker` 按
`(platform, canonical_domains)` 缓存 generation/revision 快照；仅明确 auth 失效时刷新一次，
429/CAPTCHA/普通网络错误不刷新。任务成功后以 generation+revision CAS 回写内存 jar。

Chrome Cookie 在 curl import、Playwright 注入和 broker 回写前共用 normalization：非法 name/domain/path/
SameSite/expiration 不会透传，且 Cookie 域必须与目标 host 匹配。快照只在内存；yt-dlp
临时 Cookie 文件只在任务 tmp 并 finally 清理。

`youtu.be` 链接仅在 YouTube 凭据和会话身份中转为 watch 上下文，不改变内容稳定身份。
持久 URL 删除 fragment 与 known-key 凭据/追踪 query，但这不是通用秘密检测。

## 网络安全

Browser HTTP client 拒绝非 HTTPS、userinfo、非 443、IP literal、localhost/私网 host。安全
DNS 模式对页面和资源都执行应用层逐跳 redirect：每跳先校验 URL/允许域，解析并要求全部
DNS 答案为 global IP，再以 transfer-scoped `CURLOPT_RESOLVE` 将连接固定到该答案。传输禁止
连接复用，session 设 `trust_env=False`。

安全 DNS 固定与显式/环境代理不兼容，构造客户端或传输前会返回 typed network error，
不降级。Edge renderer 仍只有域与资源预算，不应被描述为具有同样 IP pin 的完整 SSRF sandbox。

## 发布、错误和 warning

新内容在 delivery root 内 staging，校验 manifest ID、安全相对路径和媒体存在性后才可原子
提交。`manifest.json` 自身也临时文件 replace。同 URL 重抓合并用户 article/图片和缺失媒体，
再切换完整快照。

失败 task 保留 `error_code`、`error_detail`、`error_source`、`error_retryable`；进度与结果保留去重
warnings。上游可依这些结构字段展示 typed error，不必解析异常文本。

## ASR 与模型

在线 ASR 保持下载→OSS→filetrans→可选 refine，但下载产物在质量门禁前只存于私有
staging。publish fence 只在任务仍有效时允许发布；timeout 关闭 fence 并写 typed lifecycle error，即使
不可强杀的底层线程稍后返回，也不会越过质量门禁发布。

本地 ASR 使用固定 revision 的 OpenVINO Whisper-small INT8 且只运行于 Intel `GPU`。Windows
spawn 单 worker 跨 job 复用 pipeline，单并发 semaphore 串行 GPU。VAD/model 时间戳策略有确定性
质量门禁，首选失败只换另一策略一次，两次都失败不发布转录。

模型 `asset-manifest.json` 记录 schema/model ID/revision 和所有必需文件的大小+摘要。下载在临时
目录验证完成后原子替换，进程内 lock 与跨进程文件锁防止并发写。同 revision 重启直接复用
已验证快照；损坏、缺 manifest 或 revision 变更才重建。

## 验证边界

单元/集成测试覆盖 redirect + DNS pin、代理 fail-closed、Cookie normalization、XHS/YouTube 上下文、
typed errors/warnings、online publish fence、模型 manifest/跨进程锁/重启复用与原子发布。这些测试不能
代替真实第三方平台的当日 Cookie/风控验证；真实结果只以带日期报告为证。
