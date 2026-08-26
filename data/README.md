# 运行时数据目录

插件加载后会在此目录（或框架分配的数据目录）写入：

- `settings.json` — 统计、点歌记录、用户/群歌词设置
- `group_remarks.json` — 群备注（名称、QQ 群号）

首次安装可参考 `settings.json.example`；发布包已带空数据结构。

**勿将含真实用户 openid、昵称的 `settings.json` 上传到插件市场。**
