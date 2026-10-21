# 配置目录

根集成 CLI 从当前仓库的 `config/secrets.toml` 读取 MediaLab 的 OSS/ASR 密钥。创建方式：

```powershell
cd <repo-root>
Copy-Item .\components\medialab\src\medialab\config\secrets.toml.example .\config\secrets.toml
```

`secrets.toml` 已被 Git 忽略。不要把真实密钥写入示例或文档。

内容目录不在这里配置密钥：根启动入口默认把 `LIBRARY_ROOT` 与 MediaLab `paths.delivery` 指向
`<repo-root>\content`。可用启动脚本的 `-LibraryRoot` 或 `LIBRARY_ROOT` 显式覆盖；不会自动
读取或迁移旧根 `library/`、`<original-medialab-dir>\delivery`。
