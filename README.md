# Two Hours · 2hours

![Version](https://img.shields.io/badge/version-0.2.0-CC785C)
![License](https://img.shields.io/badge/original_code-Proprietary-blueviolet)

把散落的工作进展和阅读材料放进一份报告，集中审阅、口述圈画，再一次确认任务理解与执行去向。Agent 将独立任务送回各自的 Codex 会话，让工作继续推进。

## 一次审阅

```text
$2hours 回看昨天到现在的 Codex 工作和 Cubox 收藏，生成集中审阅报告。
$2hours 回看上周，准备本周集中审阅。
$2hours 继续 /绝对路径/已有运行目录 中尚未分发的任务。
```

1. Agent 按 Traceink 的证据合同读取工作记录，结合 Cubox 正文，生成一屏最多 8 张卡的议题看板。
2. 点击卡片打开详情。可以写意见，也可以点击「开始口述批注」，选择窗口后边看、边说、边圈画。
3. 完成录制后，Agent 整理原话、截图、AI 理解和目标会话，在 **Two Hours 同一页**呈现统一审核。
4. 修改或暂缓条目，最后确认一次。Agent 使用 Codex 原生工具分发，逐批保存真实回执。

默认范围是昨天；自然周按当地周一至周日计算。录屏不会随报告预览自动启动。发送成功只表示任务已送达，不表示下游工作已完成。

## 安装

需要 Python 3.10+、Node.js 20+、uv。口述录制另需 ffmpeg、Blurt 支持的录屏环境及麦克风权限；macOS 原生录制器需要 Xcode Command Line Tools。当前版本在 macOS 上验证，其他平台未做完整录制验证。

从独立仓库安装一个 Skill 即可，四个组件的源码与所需资源已收入 `vendor/`，无需分别安装四份全局技能：

```bash
# 目标目录已存在时先保留原安装，不要覆盖未提交修改。
git clone --branch v0.2.0 https://github.com/WdBlink/2hours.git "${CODEX_HOME:-$HOME/.codex}/skills/2hours"
cd "${CODEX_HOME:-$HOME/.codex}/skills/2hours"

# 使用上游安装器获取固定版本的 Cubox 可执行文件；也可复用 PATH 中已有版本。
node vendor/cubox/cli/scripts/install.js
python3 scripts/run.py doctor
uv run vendor/blurt/scripts/doctor.py
```

Cubox 安装器通过 GitHub HTTPS 下载官方 v1.0.10 构建，不需要本机 Go；它不提供额外的校验和检查。源代码同包附带，也可按上游 Makefile 自行编译。首次使用 Cubox 时运行 `vendor/cubox/cli/bin/cubox-cli auth login`；不要把 token 写进聊天或仓库。

重新打开 Codex 会话后调用 `$2hours`。当前宿主需要提供 `list_threads`、`read_thread`、`send_message_to_thread` 和 `create_thread` 等原生工具。缺少工具时仍可生成报告，但不能伪装已分发。

版本更新通过 Git tag 管理：在确认本地改动已保留后 `git fetch --tags`，再切换到选定版本。**唯一维护源是本仓库**，安装目录是运行副本；运行资料独立存于 `~/.2hours/runs/`。

## 无私人数据的预览

在仓库目录执行：

```bash
DEMO_RUN=$(mktemp -d)
cp examples/review/* "$DEMO_RUN/"
python3 scripts/run.py render --run "$DEMO_RUN"
python3 scripts/review_bridge.py --run "$DEMO_RUN" --open
```

示例的 8 张卡均为虚构内容，目标表为空。服务只监听 `127.0.0.1`，报告、文字意见和录音保存在本机；使用同源检查和随机 token，没有任意命令接口。普通 `file://` HTML 不具备启动录制或提交审核的能力。

## 验证

```bash
python3 -B -m unittest discover -s tests -v
node --check assets/bridge.js
node --check assets/board.js
node --test vendor/traceink/test/*.test.mjs
```

测试覆盖时间范围、来源映射、看板生成、录制退出分支、统一审核、批准快照和重复派发防护。测试替身不能证明新设备的录屏权限、ASR 质量或 Codex 宿主连接可用；第一次录制前仍需运行 Blurt doctor。

## 边界

这是按需 Skill，尚不是常驻调度器。当前 Codex 回合需等待录音和审核，本地服务需持续运行；结束回合、应用退出或主机休眠后不能保证自动唤醒。定时 loop 应由用户另行配置 Codex 原生 automation。

最终确认同时授权理解结果与目标会话；未选项暂缓。确认绑定来源快照，后续改动进入下一轮；已有发送回执不会自动重放。真实会话、收藏正文、录音、截图和访问凭据不属于本仓库或 release。

## 致谢与源码

| 项目 | 锁定版本 / 提交 | 用途 |
| --- | --- | --- |
| [Blurt](https://github.com/AGIHunt/blurt) | 0.5.0 · `53171c4` | 录屏、口述转录与圈画证据 |
| [Traceink](https://github.com/WdBlink/traceink) | 0.2.0 · `ecadc29` | 会话证据与工作线阅读合同 |
| [Cubox CLI](https://github.com/OLCUBO/cubox-cli) | 1.0.10 · `6eb4e7b` | 收藏与正文读取 |
| [answer-me-with-html](https://github.com/QingYunA/answer-me-with-html) | 0.5.7 · `fa42f5c` | Markdown 排版与 HTML renderer |

四者均按原 MIT 许可集成，各自目录保留完整许可证和 `SOURCE.json`。Traceink 包含本地修订的阅读合同；AM 包含可重建源码、锁文件、renderer bundle 及字体/内嵌依赖许可。鸣谢集中放在这里，原有版权和许可声明保留。

Two Hours 自有代码暂沿用原先的 Proprietary 状态，未授予新的开源许可；上述第三方组件继续分别适用其原许可证。
