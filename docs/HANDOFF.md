# Echo Reader 当前交付文档

> 状态：现役、唯一跨组件交付入口
>
> 基准日期：2026-08-12（Asia/Shanghai）

## 当前结论

Echo Reader 已有可构建的 Windows 本机网页应用架构：Python 宿主管理持久数据、导入服务、
SessionBridge、迁移和生命周期；Rust 子进程在动态 loopback origin 上同源托管 React、Echo API
和导入白名单代理。安装目录可替换，资料库与所有其他持久资产位于
Documents/LOCALAPPDATA。

接手顺序：根 `AGENTS.md`、`components/medialab/AGENTS.md`、[文档索引](README.md)、
[安装文档](installation.md)、[架构](architecture.md)、[集成合同](integration.md)。历史 HANDOFF/
DELIVERY/ROADMAP 和带日期报告仅是当时证据。

## 本轮已闭环的问题

- 安装版不再暴露独立 3000/3100 浏览器 origin；一个动态 Rust origin 承载 UI/API，Python
  上游为私有动态 loopback 服务。精确代理路由、Host/Origin/Sec-Fetch-Site 边界、body 上限、
  截止、不跟随重定向和禁用环境代理已实现。公开 session cookie、独立内部 token、Python
  直连鉴权和 Windows 独占端口把普通端口扫描与重绑隔离在会话外。
- 因 bridge 19876 固定，Windows mutex + 带 boot ID 的 instance record 实现了每机器单实例和
  再次启动打开现有实例。
- `AppPaths` 区分 library/state/cache/config/logs/tmp；`app.json` 持久切库。切库已有持久根
  overlap 验证、活动导入 guard、串行扫描与失败回滚。
- imports schema v2 保存相对 `item_path`；v1 由 store 升级。`--migrate-from` 只复制指定持久
  资产，允许替换首次启动产生的精确空 DB，拒绝非空冲突、活跃边车和未来 schema；
  只 staging 实际触达根 + state，durable journal 保证该根子集切换可恢复。
- 宿主关闭仅在无 active job 时接受，导入服务和 Rust 子进程都有宽限/硬截止；Rust
  父进程 watchdog 防止孤儿网关。
- 媒体网络已从重定向后验证改为每跳 URL 校验 + 公网 DNS 预解析 + 当次传输 IP pin；
  代理与安全 pin 不兼容时明确 fail closed。
- URL 会话上下文、Cookie normalization、XHS 资源规范、`youtu.be` YouTube 凭据身份已
  统一。MediaLab typed errors 和 manifest/probe/media warnings 会保留到 job/API/UI。
- 在线 ASR 使用 publish fence，timeout/质量失败后底层迟到也不发布。本地模型用内容
  manifest、原子替换和跨进程锁，已验证同 revision 重启复用。
- `build-package.ps1` 产出 standalone bundle/zip 并可编译 Inno Setup installer；`test-package.ps1`
  验证冻结 worker、动态端口、持久重启、退出、bridge 释放和 watchdog。

## 现役稳定合同

共享文件数据面、Echo `/api/v1` 控制面、一 URL/一稳定 item、staging 快照发布、持久
七状态 job、索引成功才 succeeded、重复只警告不合并、Cookie 不持久落盘，以及
online/local ASR 均不绕过质量门禁。详细字段和边界只在[集成合同](integration.md)、
[Echo API](../components/echo/docs/api.md)和[MediaLab 架构](../components/medialab/docs/ARCHITECTURE.md)维护。

## 剩余风险与不得过度声明的结论

- 源码已采用 MIT 许可证，只把 `ffmpeg` / `ffprobe` 当独立程序调用；公开分发 installer / standalone
  zip 仍需随包提供第三方许可证原文，并在发布页附上包内 FFmpeg 及其静态链接库的对应源码（见
  `THIRD_PARTY_NOTICES.md`、`installer/licenses/FFMPEG_SOURCE.txt`）。
- 不得把目前固定的 BtbN "lgpl" FFmpeg 称为纯 LGPL 构建：它经 chromaprint 静态链接了 FFTW 3.3.11
  （GPL-2.0-or-later，`avformat-62.dll` 内有 `fftw_*` 符号和 `fftw-3.3.11` 字符串），另静态链接了
  LAME、twolame、soxr、libbluray、libgme、libssh、openal-soft、libplacebo、fribidi、libiconv、
  aribb24、gmp、zvbi（LGPL）和 SRT、libzmq（MPL-2.0）。`build-package.ps1` 的 `Assert-LgplFfmpeg`
  发现 FFTW 即失败；在完成下面的“FFmpeg 自编精简构建”待办之前不发布 Windows 安装包和程序束。
- 安全 DNS pin 刻意不支持代理或环境代理；这是 fail-closed 功能边界，不是仍开放的绕过
  实现。需要代理的未来方案必须重新建立连接目标保证。
- Edge renderer 有 HTTPS/host/resource 预算，但不等于 curl 路径的 transfer-scoped DNS pin；不得
  把整个浏览器渲染器宣传为完整 SSRF sandbox。
- 真实第三方平台及当前 Cookie/风控的成功只以对应日期报告为证。如本轮未重跑微信、
  XHS、知乎/头条、YouTube/Bilibili 等真实样本，不宣称已用当前构建重新通过。
- Windows 安装包在未使用有效代码签名证书完成真实签名/SmartScreen 验证前，不宣称商业分发
  安装体验已验收。

## 待办：FFmpeg 自编精简构建（方案 a）

Windows 安装包和程序束恢复发布前必须完成，完成前不发布：

1. 用 BtbN/FFmpeg-Builds 的脚本自己编译 FFmpeg（以当前固定的 `autobuild-2026-07-31-14-10` 为基础），
   关闭 chromaprint（去掉 `50-chromaprint.sh` / 不带 `--enable-chromaprint`），从而不再引入 FFTW。
2. 只保留 Echo Reader 实际用到的编码器和组件：编码器 libopenh264、aac、libmp3lame、libopus、flac、
   pcm_s16le（见 `medialab/audio.py`、`publishing.py`、`funasr_asr.py`），导入所需的解码器、
   demuxer/muxer 和 `ffprobe`；其余外部库不启用，保持不带 `--enable-gpl` / `--enable-nonfree`。
3. 对仍静态链接的 LGPL / MPL-2.0 库，逐个随包提供许可证原文，并在 GitHub Release 附上对应源码和
   构建脚本；更新 `installer/licenses/FFMPEG_SOURCE.txt`、`THIRD_PARTY_NOTICES.md`、`mise.toml`
   的固定资产和 SHA-256。
4. 用 `scripts/build-package.ps1` 验证 `Assert-LgplFfmpeg`（含 FFTW 检查）通过，再按
   [安装文档](installation.md#打包形式与许可) 在干净的 Windows 上验收。

## 当前验证状态

2026-08-12 当前工作树已通过 `scripts/verify.ps1`。同一代码状态重新生成的 0.1.0 standalone
bundle/zip 与 Inno installer 已通过冻结 worker、会话鉴权、四轮启动/重启、持久数据、宿主强杀
watchdog、clean install、覆盖升级清理旧 payload、uninstall 保留用户数据验证。真实第三方平台未
用该最终安装包重跑，因此平台成功率仍只引用带日期报告。

## 验证入口

```powershell
.\scripts\verify.ps1
.\scripts\verify.ps1 -E2E
.\scripts\build-package.ps1 -Version <version>
.\scripts\test-package.ps1 -BundleRoot <bundle-directory>
```

仓库门禁、包烟测和真实平台验证是三种不同证据，不互相替代。
