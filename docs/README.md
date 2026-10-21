# 文档索引与权威归属

现役文档描述当前代码合同；`history/` 和组件旧 HANDOFF/DELIVERY/ROADMAP 只保留
当时的问题、解法和实测证据。冲突时先看本索引指定的现役权威，再看源码。

## 建议阅读顺序

1. [当前交付文档](HANDOFF.md)：当前状态、已解决问题和剩余风险。
2. [安装与本机运行](installation.md)：Windows 安装版、持久路径、迁移与退出。
3. [架构说明](architecture.md)与[集成合同](integration.md)：进程、数据面、控制面和状态。
4. [开发指南](development.md)：源码运行、打包与验证。

## 现役权威矩阵

| 主题 | 权威文档或代码 |
|---|---|
| 当前交付状态、已解决问题、剩余风险 | [HANDOFF](HANDOFF.md) |
| 安装、持久路径、单实例、退出、选择性迁移 | [安装与本机运行](installation.md)与 `src/echo_reader/desktop.py` |
| 进程、组件边界、数据面与控制面 | [架构](architecture.md) |
| item、imports schema v2、切库、错误/警告、发布与网络 | [集成合同](integration.md) |
| Echo HTTP API 与精确代理路由 | [Echo API](../components/echo/docs/api.md) 与 `backend/src/routes/` |
| MediaLab 探测、会话、URL、DNS 固定、ASR 与发布 | [MediaLab 架构](../components/medialab/docs/ARCHITECTURE.md) |
| Echo 读写文件格式 | [file-format](../components/echo/docs/file-format.md) 与 [producer-guide](../components/echo/docs/producer-guide.md) |
| 前端现役产品流和未实现能力 | [前端产品基线](frontend-product.md) |
| 源码设置、独立运行、构建包和验证 | [开发指南](development.md) |
| SessionBridge 消息协议 | [扩展 README](../components/medialab/browser-extension/README.md) 与 `bridge.py` |
| 配置密钥 | [config README](../config/README.md)；真实值不进 Git |
| 来源迁移和仓库完整性 | [迁移记录](migration.md) |
| 许可与第三方声明 | [MIT LICENSE](../LICENSE)、[第三方声明](../THIRD_PARTY_NOTICES.md) 与组件 licenses |

## 历史证据

[历史索引](history/README.md) 管理旧计划、交接、决策和问题解决记录。不删除其问题/方案历史，
也不把它们的“待办”复制到当前风险列表。平台代码覆盖不等于实时访问成功；如未在
本轮重跑，就继续引用对应日期报告并明确时点。
