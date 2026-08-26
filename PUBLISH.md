# 开源与上架（Elaina 市场）

1. 把本目录推到自己的 GitHub（根目录有 `main.py`，勿提交真实 `settings.json`）。
2. Fork [Elaina-plugins](https://github.com/ElainaCore/Elaina-plugins)，在 `plugins.json` **末尾**追加：

```json
{
  "name": "qq_music",
  "type": "complete",
  "author": "飞行漂绒",
  "description": "QQ 点歌：个人/群点歌、多歌源、LRC、Web 面板。第三方音源，请遵守版权，建议自用娱乐。",
  "version": "1.9.8",
  "category": "娱乐",
  "github": "https://github.com/你的用户名/你的仓库",
  "branch": "main",
  "tags": ["点歌", "音乐", "Web面板"]
}
```

3. 提 PR。交流群见市场仓库 README。
