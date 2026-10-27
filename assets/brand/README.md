# Two Hours 标识

用户于 2026-10-25 选定 B「2h」标识。它以单线轮廓直接组成 `2h`，用同一个符号连接图形界面和未来终端界面。配色沿用现有纸面看板。

- `mark.svg` 是单色标识，可通过 `currentColor` 适配深浅背景。
- `app-icon.svg` 是原生应用图标源，墨色 `#1c2532` 底、纸白 `#f7f7f5` 字形。
- `icon.png` 为 1024px 应用图标。
- `MenuIconTemplate.png` 和 `MenuIconTemplate@2x.png` 为 16px、32px 黑色透明模板，菜单栏根据系统主题着色。
- 纯文本场合使用 `2h`，产品全名使用 `Two Hours`。这不代表已实现 TUI。

在仓库根目录重建 PNG：

```sh
uv run --with playwright python scripts/build_brand.py
```

需要已有 Chromium；缺少时运行 `uv run --with playwright playwright install chromium`。SVG 无字体、外链和运行脚本，PNG 由本机浏览器确定性渲染。此次原创标识沿用仓库 MIT License。
