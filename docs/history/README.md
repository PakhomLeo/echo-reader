# 历史文档索引

这里不是第二套现役架构，而是保留“当时遇到了什么、为什么作出决定、如何解决”的检索入口。
当前状态统一以 [交付文档 v4](../HANDOFF.md) 为准。

## 项目演进

- [2026-08-10 开发与问题解决记录](2026-08-10-development-record.md)：从两个来源项目到当前
  monorepo、前端、导入服务、小红书链路与真实 E2E 的时间线。
- [迁移记录](../migration.md)：复制白名单、排除项和来源完整性凭据。
- [前端产品基线](../frontend-product.md)：参考成熟阅读器后的布局决策和仍需数据模型支持的能力。

## MediaLab 历史证据

| 文档 | 保留用途 | 当前替代/补充 |
|---|---|---|
| [DECISIONS](../../components/medialab/docs/DECISIONS.md) | 决策、grill 结论、API 实测事实 | 现役实现看 MediaLab ARCHITECTURE/源码 |
| [LESSONS](../../components/medialab/docs/LESSONS.md) | 代码、OSS、平台、运营、性能问题与解法全集 | 新增问题继续追加，不重编号旧条目 |
| [ROADMAP](../../components/medialab/docs/ROADMAP.md) | P0–P4 计划与阶段验收历史 | 当前开放项看根 HANDOFF §11 |
| [HANDOFF](../../components/medialab/docs/HANDOFF.md) | 历次组件交接、模块地图和旧验收 | 当前跨组件状态看根 HANDOFF |
| [DELIVERY](../../components/medialab/docs/DELIVERY.md) | 累积阶段交付记录 | 当前使用看组件 README/ARCHITECTURE |

`LESSONS.md` 的历史编号在不同分类中有重复，不能用“第 17 条”作为稳定唯一 ID。引用时同时写章节
与标题，例如“运营级 / YouTube cookie 工作流”。未来如增加机器索引，应加新复合 ID，不改旧编号。

## Echo 历史证据

| 文档 | 保留用途 | 当前替代/补充 |
|---|---|---|
| [PROJECT_PLAN v1.1](../../components/echo/PROJECT_PLAN.md) | 原 standalone Echo 的冻结需求和八阶段计划 | 当前产品状态看根 HANDOFF/前端基线 |
| [Echo HANDOFF](../../components/echo/docs/HANDOFF.md) | Rust、React、媒体、测试踩坑及原组件交付 | 当前 monorepo 状态看根 HANDOFF |

这些文档会提到原 `<original-echo-dir>`、旧测试数字和原仓库 Git 历史。它们是历史上下文，不是当前
`echo-reader` 工作树的事实。

## 清场说明

2026-08-10 的完整汇报保留了运行服务、`.runtime` DB/log、`out/session-job1.cookies` 测试残留，
以及 target/dist/cache/dependency 目录供用户复核。根 `library/*` 同样被 Git 忽略，dry-run 证明 broad
`git clean -X` 会把真实资料列入目标，所以该命令仍永久禁止。

用户随后明确确认清场。执行前按端口、命令行、父子进程和绝对路径重新确认归属并停止 backend、
import 与 bridge；只对 18 个预览过且互不嵌套的 ignored 目标执行精确白名单清理。实际删除
24,244 个文件、2,960,600,569 bytes（约 2.96 GB）：`.runtime`、`.venv`、Rust `target`、前端
`node_modules/dist/test-results`、测试临时库、pytest/pycache/egg-info、根 `out/tmp`。80-byte Cookie
残留没有读取，随精确 `out` 目标删除。

清场后 3000、3100、3101、19876 均无监听，仓库内无运行进程，也没有上述生成残留。真实
`library/`、Echo `library.example`、`config/`、MediaLab `cookies/.gitkeep`、源码文档、锁文件、
`.git` 与两个来源目录未删除；来源哈希仍匹配。由于 `.venv`、`node_modules` 和 `target` 已删除，
需要先运行 `scripts/setup.ps1` 才能再次构建和测试。仓库仍没有首个 Git commit，这一点不是清场
完成的替代品。
