# QQ 音乐点歌

面向 ElainaBot v2 的 QQ 机器人点歌插件。

支持个人 / 群点歌、多歌源、LRC 歌词、语音或文件发送，以及 Web 管理面板。

| 项目 | 说明 |
| --- | --- |
| 版本 | 1.9.5 |
| 许可 | [MIT](./LICENSE) |
| 运行环境 | ElainaBot v2 |
| 额外依赖 | 无 |

> **说明**：音源来自第三方接口，不保证可用与完整播放；请遵守当地法律与版权要求，建议仅自用娱乐。MIT 只覆盖本仓库代码。

## 快速开始

1. 将本文件夹命名为 `qq_music`，放入 `plugins/`。
2. 重载插件或重启机器人。
3. 群内：`#点歌 稻香` → `#听1`
4. Web 侧边栏打开 **QQ音乐**（含「关于」说明）。

完整指令见 [USAGE.md](./USAGE.md)。上架市场见 [PUBLISH.md](./PUBLISH.md)。

## 功能

- `#点歌` / `#听N`（个人）· `#群点歌` / `#群听N`（群共享）
- 多歌源：第三方、官方搜索、落月、网易云
- 歌词开关（个人 / 群 / 全局）
- Web：歌源、统计、记录、用户与群设置（含日/夜背景 UI）

## 目录

```text
qq_music/
├── main.py / service.py / sources.py / official_qq.py
├── settings.py / web_panel.py / panel.html
├── data/settings.json.example · data/README.md
├── assets/bg/          # 面板日/夜背景图
├── LICENSE · README.md · USAGE.md · .gitignore
```

运行时会生成 `data/settings.json` 等。
