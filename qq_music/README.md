# QQ 音乐点歌

ElainaBot 插件：QQ 音乐搜索点歌、真实音源播放、完整 LRC 歌词、个人/群/全局歌词开关、Web 管理面板。

- **版本**：1.5.0
- **许可**：MIT（见 `LICENSE`）
- **依赖**：无额外 pip 包

## 快速开始

1. 将本文件夹以 **`qq_music`** 名称放入 `plugins/`
2. 重载插件
3. 群内发送 `#音乐帮助` 或打开 Web **QQ音乐** 面板

详细说明见 **[USAGE.md](./USAGE.md)**。

## 发布包内容

```
qq_music/
├── main.py
├── service.py
├── settings.py
├── web_panel.py
├── panel.html
├── assets/bg/day.jpg
├── assets/bg/night.jpg
├── data/settings.json.example
├── data/README.md
├── LICENSE
├── README.md
├── USAGE.md
```

运行时会在 `data/` 生成 `settings.json`、`group_remarks.json`，无需手动创建。
