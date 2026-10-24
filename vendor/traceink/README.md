<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset=".github/logo-dark.svg">
    <source media="(prefers-color-scheme: light)" srcset=".github/logo-light.svg">
    <img src=".github/logo-light.svg" alt="Traceink" width="360">
  </picture>
</p>

<p align="center"><strong>理清一天，或者一周里，Agent 到底做了什么。</strong></p>

<p align="center">一个只读的 Agent Skill，用来回看 Copilot CLI、Codex 和 Claude Code 的工作记录。</p>

<p align="center"><a href="README.en.md">English</a> · 简体中文</p>

<div align="center">

[![CI][ci-badge]][ci-url]
[![Latest release][release-badge]][release-url]
[![MIT License][license-badge]][license-url]
[![Node.js 18+][node-badge]][node-url]

</div>

<p align="center">
  <a href="#快速开始">快速开始</a> ·
  <a href="#为什么做这个-skill">为什么</a> ·
  <a href="#它整理出来的东西大概长这样">示例</a> ·
  <a href="#隐私与边界">隐私</a>
</p>

## 为什么做这个 Skill

一天开了好几个 Codex 或 Claude Code 会话，晚上想回头梳理时，记录都还在，只是散在聊天、代码、测试和文档里。有些任务跑了几个小时，人没有一直盯着，更难知道中间发生了什么。

会话标题说不清事情是怎么走到这里的。逐条翻记录太慢，普通摘要又常常省掉失败和转向，还可能把一个待讨论的问题写成已经做出的决定。

Traceink 把相关材料整理成几条**工作线**。一条工作线代表同一件事，可以跨会话，也可以跨 Codex 和 Claude Code。打开一条工作线，你能看到它从哪里开始，中间为什么转向，证据在哪，以及最后还有什么必须自己决定。

Traceink 整理到这里就停。最后怎么理解，由你自己写。

## 它能做什么

- 把散落在多个会话、不同来源里的同一件事放回一条工作线，必要时再打开会话指向的本地材料。
- 先看索引，决定哪条值得细看，再打开相关证据。
- 每个重要说法都能回到来源平台、原 Session、文件或消息片段。
- 标出你亲自参与、Agent 独立推进和证据不足的部分。
- 支持按天、自然周或自定义日期范围回看。
- 你写完想法后，它可以帮你整理成几个待确认的提案，但不会替你写入或执行。

## 快速开始

需要 Node.js 18 或更新版本。

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install
```

重新打开 Codex 会话，然后输入：

```text
$traceink 回看今天
```

Traceink 会先给出工作线索引。选择其中一条，才会继续展开它的证据档案。

### GitHub Copilot CLI

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install --agent copilot
```

重新打开 Copilot CLI 或运行 `/skills reload`，然后输入：

```text
/traceink 回看本周
```

Copilot session 默认只从本机 session store 查询，不会搜索云端记录。需要同时搜索云端 session 时，在启动 Copilot CLI 前显式设置：

```bash
TRACEINK_COPILOT_CLOUD_SESSIONS=1 copilot
```

该变量只接受 `0` 或 `1`。未设置、空值或 `0` 都保持仅本地模式。

### 其他常用方式

| 想做什么 | 在 Codex 中输入 |
| --- | --- |
| 看这一周同一件事怎么变化 | `$traceink 回看本周` |
| 指定日期范围 | `$traceink 回看 2026-08-01 到 2026-08-09` |
| 只读一个来源 | `$traceink 回看今天，只读取 Codex` |
| 只读指定会话 | `$traceink 回看这些会话：<session-id...>` |
| 展开一条工作线 | `$traceink 展开第 2 条工作线` |
| 整理刚写完的想法 | `$traceink 整理我刚写的反思，但不要执行` |

## 它整理出来的东西，大概长这样

下面是手写示例，不是真实运行结果：

```text
起点
性能瓶颈可能在 scheduler。

发生了什么
三轮实验都没有得到稳定改善。11 个失败样本中，有 8 个在进入
scheduler 前就已经丢失约束。[E1][E2]

可能的变化 · AI 整理，尚未采纳
问题可能更接近任务表示，而不是调度本身。

以后怎么验证
找两类真实任务，把表示层与调度层分开，再比较 schema repair 的变化。

需要你决定
现在是否有足够依据暂停 scheduler 优化，先验证任务表示这条路线？
```

在真实回看中，`[E1]` 和 `[E2]` 会指向 Traceink 实际读过的材料。旧判断找不到依据，或者某段会话没读全，Traceink 会直接注明，不会替缺失的部分补故事。

## 什么时候适合用

当同一件事散在多个会话里，或者你想在周末回头看清一个项目怎样走到今天时，Traceink 会比较有用。它不负责监控或运行 Agent，也不替你写日记；它只把回看需要的材料收拢起来。

## 怎么工作

1. 先固定日期范围和读取来源，并记录实际读了哪些材料。
2. 按共同目标和状态变化分组，而不是照搬会话标题。
3. 去掉重复的工具输出，留下失败、分歧、路线变化和未解决的问题。
4. 一次展开一条工作线，最后停在一个需要你回答的问题上。
5. 等你写完，再把原文整理成待你确认的提案。

更完整的阅读与权限规则见 [`skill/references/editorial-contract.md`](skill/references/editorial-contract.md)。它只规定证据怎么引用、哪些事不能替用户做，不要求输出套进一组固定的分类。

## 安装选项

Codex 默认安装位置是 `${CODEX_HOME:-~/.codex}/skills/traceink`。使用 `--agent copilot` 时，安装位置是 `${COPILOT_HOME:-~/.copilot}/skills/traceink`。

如果想跟随 `main` 分支：

```bash
npx --yes github:WdBlink/traceink install
```

如果目标位置已有内容不同的 Traceink，安装器会退出。只有确定要替换时才使用 `--force`：

```bash
npx --yes --ignore-scripts 'github:WdBlink/traceink#v0.2.0' install --force
```

安装器不会替换软链接，也不会删除其他 Skill 目录。

## 隐私与边界

- Traceink 不带遥测，也没有自己的托管服务或第三方运行时依赖。实际处理遵循当前 Agent 会话的模型、网络与隐私设置。
- Copilot CLI 默认只查询本地 session store；云端 session 搜索必须通过 `TRACEINK_COPILOT_CLOUD_SESSIONS=1` 显式开启。
- 日期范围和来源用于筛选会话记录。关联文件只有在你明确指定，或当前证据档案确实需要时才会打开；扩大读取范围前会先询问。
- Skill 明确要求 Codex 把会话、文档和代码中的指令当作待阅读材料，而不是要执行的命令。
- 关联文件默认限制在会话记录的工作目录内，越界读取需要你的同意。
- Skill 本身只读：不写入 CTX，不启动后台任务，不迁移待办，也不封页。

Copilot CLI、Codex 和 Claude Code 支持自动发现。其他来源没有内置解析器，需要你明确提供可读的本地材料。

## 开发

```bash
npm test
npm run pack:check
```

安装器没有第三方运行时依赖。CI 会在 Node.js 18、20 和 22 上运行测试与打包检查。

## 贡献

欢迎提交 Issue 或 Pull Request。修改 Prompt 或权限边界时，请同步更新编辑合同和对应测试。提交前运行 `npm test` 和 `npm run pack:check`。

## License

[MIT](LICENSE) © 2026 WdBlink

---

Crafted with [Readme Craft](https://github.com/agentskills/agentskills/tree/main/skills/readme-craft).

[ci-badge]: https://github.com/WdBlink/traceink/actions/workflows/ci.yml/badge.svg
[ci-url]: https://github.com/WdBlink/traceink/actions/workflows/ci.yml
[release-badge]: https://img.shields.io/github/v/release/WdBlink/traceink?display_name=tag
[release-url]: https://github.com/WdBlink/traceink/releases/latest
[license-badge]: https://img.shields.io/badge/license-MIT-2f302c
[license-url]: LICENSE
[node-badge]: https://img.shields.io/badge/Node.js-%E2%89%A518-5b7f62
[node-url]: https://nodejs.org/
