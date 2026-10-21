# 前端产品基线

> 现役基线：三栏阅读、添加内容、持久 imports 页、切库与安装版退出已落地。

## 主要用户流

1. 用户在全局“添加内容”粘贴或从 `.txt/.csv` 输入 1–50 个 HTTPS URL。
2. 选择 `auto`、`download` 或 `transcribe`；转写后端依能力选 online/local，用户明确选择不被
   异步预检覆盖。
3. `/imports` 显示服务端分页、counts、`active_count`、队列/阶段/ETA、typed error、warning、
   质量与重复信息。
4. Echo rescan 成功后 job 才为 `succeeded` 且可打开；索引失败保留 inactive
   `pending_index` 和“仅重建索引”。
5. 用户在导航→内容列表→阅读器中搜索/筛选、阅读、播放、编辑文章或校对转录。

## 信息架构

```text
工作区导航             内容列表                    阅读器
添加内容               搜索 / 类型筛选             来源与标题
全部 / 类型            条目元数据                  文章 / 时间线
导入任务               加载 / 空 / 错误             主媒体 + 附加媒体
切库 / 刷新 / 退出     /imports 状态                 编辑与保存
```

桌面端保留三栏，窄屏逐层聚焦为列表或阅读器。主播放器和 mini player 共享同一播放状态；
额外媒体通过 `media_items` 展示，不丢失 mixed item 中的附件。

## 行为合同

- 只允许 queued 取消；running 原生工作不伪造可取消。
- retry 使用服务端持久请求；reindex 只对带安全 `item_path` 的 inactive `pending_index`。
- warning 与 error 分开：warning 表示已保留的部分成功/风险，typed error 显示来源、可重试性
  和任务上下文，前端不从文本猜错误类型。
- 安装版显示“退出 Echo Reader”。有活动导入时后端返回 `409 imports_active`，界面不隐藏或
  强制中止工作。
- 切库成功后刷新当前列表与导入上下文；过程中的 overlap/活动导入/回滚错误使用服务端结果，
  不先行更改本地状态。

## 尚未实现的产品能力

Inbox/Later/Archive、已读/收藏、标签/文件夹/RSS、全文索引、保存搜索、高亮/边注/回链、
阅读进度和跨设备同步、批量归档/合并、命令面板仍需要新的持久模型和 API，不应仅靠前端
local state 假装完成。

## 验收基线

- 1280×720 下选择媒体条目后，首屏可看到文章或时间线开头。
- 1–50 URL 批次可提交、刷新和重启恢复；警告、错误、索引失败不被显示成普通成功。
- 搜索、筛选、切库、刷新、编辑、媒体播放和安装版退出互不回归。
