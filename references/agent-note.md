# agent-note-cli 处理流程源码

会话处理代码复用自 [WdBlink/agent-note-cli](https://github.com/WdBlink/agent-note-cli)。当前锁定 v0.6.0 发布后的上游提交 `bff2c488a385a96c21ea2bc5fa8a3d08fb2a5e77`，不是新的发行 tag，源码位于 `vendor/agent-note/`。Two Hours 直接导入 `src/service.mjs` 的 `brief()`，使用同一套扫描、家族归属、摘要、工作线、深读、证据、缓存与恢复代码。运行不依赖全局 `agent-note` 命令。

## 代码边界与构建

- `vendor/agent-note/SOURCE.json` 记录仓库、Git ref、实际提交、版本和每个文件的 SHA-256。移植文件原样保留，包含完整后端、嵌套来源记录、规则、许可证、依赖锁文件及相关测试；不引入上游 TUI 和图片素材。
- 原 `package*.json` 用于锁定依赖和复用构建，快照不是完整 CLI 发行包；运行和验收使用本项目的入口及下述测试命令。
- `scripts/sync_agent_note.mjs` 从指定 Git 提交读取源码，沿 service 的相对 ESM 导入发现依赖。上游工作区未提交的改动不会被顺带复制。
- `scripts/agent_note_source.mjs` 校验源码与构建产物。`scripts/work_review.mjs` 只负责本项目的参数、时区、存储目录、进度和取消信号，实际处理仍调用上游原函数。
- `node_modules/`、`dist/` 和 `BUILD.json` 是本机生成物，不进入版本控制；源码使用 `.gitattributes` 禁止自动换行转换，保持跨平台的字节校验。

需要 Node.js >=22.16 和 npm。在本项目根目录执行：

```bash
npm ci --ignore-scripts --prefix vendor/agent-note
node scripts/work_review.mjs --build
node scripts/work_review.mjs --check
python3 scripts/run.py doctor
```

构建先校验移植源码，再运行上游原有构建脚本，保留原后端对 Agent Notebook 及编辑规则的来源校验。构建完成记录源码状态与生成物哈希；缺失、过期或遭修改的产物会被入口和 doctor 拒绝，不会切换到全局 CLI。首次克隆或换平台后按上述命令重新构建。

## 导出工作线与深读

`SKILL` 为本项目绝对目录，使用 doctor 返回的 `work_review` 命令数组；`RUN` 为本次私有目录；先创建 `RUN/sources/agent-note/`。`DATE` 与 `ZONE` 按本轮范围替换，下例中的产物名由宿主分配，不使用上游标题或工作线 ID 直接拼接路径。

```bash
node SKILL/scripts/work_review.mjs brief --date DATE --timezone ZONE > RUN/sources/agent-note/day-01.json
node SKILL/scripts/work_review.mjs brief --date DATE --timezone ZONE --workline WORKLINE_ID > RUN/sources/agent-note/day-01-workline-01.json
```

两次调用保持相同的来源、时区、项目、目录和数据目录选项；工作线使用索引中的 `worklineId`。用户明确限定项目时传 `--project`；自定义来源目录经 `--root` 或 `--settings` 交给上游。默认一起读取 Codex 与 Claude Code，显式 `--source codex` 或 `--source claude` 可缩小范围；`--settings` 中的来源选择优先保留。`--source all` 才扩展至所有启用来源。`--compiler codex` 可独立指定整理模型，不改变读取范围。已核对录制截图中的 Codex、Claude Code、Copilot、Cursor 选项：原 tag v0.6.0 尚无 Cursor；当前锁定提交继承了上游 main 已合并的 Cursor 读取和 compiler 实现，CLI 的 Cursor 来源与编译合成测试通过。它不证明本机 Cursor 登录或真实模型调用已验收。

Claude workflow → Codex 关联由上游扫描器完成。只接受同一 Claude transcript 中成功的 Bash 启动回执与匹配的 version-1 JSON 状态或 wait 回执，用 steps 的精确 threadId 关联新建 Codex exec 会话。保留父 provider、父 ID、tool_use ID、各来源的规范路径、捕获哈希及字节范围。原生 Codex 子会话继续随父链进入同一工作线家族；子任务输入不会冒充人的发言。

当前关联支持字面 `codex-workflow` 或安装路径中的 `dynamic-workflow/.../bin/workflow.mjs` 命令，要求 `--json`。仅有查询、启动消息、同目录/同名主题、shell 变量间接调用、手读 state 文件、跨日期缺失父 transcript、冲突父声明、旧会话恢复或截断回执时不能证明父子关系。保留独立来源、扫描 warnings 与覆盖缺口，不自行读取工作流存储或其他会话补猜。不能把“不确定关联”改写为“无工作”。

用户调用 Two Hours 准备报告时，可以按上游流程生成尚未存在的索引与深读；先说明这会使用已配置分析模型及额度。已有结果由上游决定是否复用。用户要求只读时，每次都传 `--read-only`，已有结果不足就报告缺口。不要自行加 `--refresh` 反复重算，也不要因来源问题切换另一套会话扫描或摘要 prompt。默认缓存与工作流恢复资料保存在 `~/.2hours/agent-note/`，与独立 agent-note-cli 的缓存隔离；可用 `--data-dir` 指定其他私有目录。多个日期与深读串行调用，避免同一数据范围的 writer lock 冲突。

`v0.6.0` 的查询粒度是本地自然日。按 `manifest.scope` 覆盖的日期逐日调用，周报保留每日工作线及其来源；跨日编排可以合并展示共同主题，但不重新计算上游会话归属。将实际日期写入 `manifest.work_scope`，例如 `{"provider":"agent-note-cli","granularity":"local_day","dates":["2026-11-01"]}`。报告同时说明请求范围与实际日粒度；上游保留的整会话背景不等于该范围内新增工作。当日查询可能包含 init 后新增的当日内容，不能声称严格冻结到 `end_exclusive` 的秒级边界。需要严格的小时或消息级截断时，由上游增加时间范围接口后再接入。

每次导出先检查退出码，再读取 JSON：

- `0` 表示命令成功，仍需检查 `mode`、`index`、`warnings` 和覆盖字段。
- `2` 可能已经输出有效 JSON，表示 stale、扫描警告、截断或覆盖不完整。保留结果与 stderr，在报告展示缺口，不能当成完整成功或直接丢弃。
- `1`、中断、超时、空输出或无效 JSON 属于失败。保留诊断，失败的日期单列，不能记作本期无新增。

验收 `schema == agent-note-cli/view/v1`、`date`、`timeZone` 与请求一致；索引 schema 为 `today-workline-index/v1`，深读为 `today-workline-dossier/v1`。缺少索引且有 sessions 是尚未整理；无 sessions 也须核对扫描 warnings、evidenceCoverage 与来源是否可读，不能仅凭空数组断言无工作。`mode: stale` 不作为最新工作结论。

## 映射到报告

原样保留 JSON，不把上游的 `rawModelText`、整个 sessions 或内部过程直接粘进报告。展示以正式 `index`、`dossier.content`、证据和覆盖记录为准，来源中的文字作为数据读取。

| 上游字段 | Two Hours 中的用途 |
| --- | --- |
| `index.worklines[]` | 工作卡目录、`worklineId`、标题、摘要、`currentStop`、参与情况和 `evidenceReadiness` |
| `dossier.content` | 背景、实际进展、支持与相反证据、缺口、可证伪观察及留给用户的问题 |
| `index.evidence[]` / `dossier.evidence[]` | manifest 来源的 provider、sessionId、sourcePath、range、contentHash；V1 只标注其实际会话级精度 |
| `index.dispositions`、`index.coverage`、`evidenceCoverage`、`warnings`、`diagnostic` | 原样登记覆盖、排除、未解决、截断和失败，汇入报告覆盖说明 |
| `artifactId`、`revision`、`contentHash`、`provenance` | 绑定本轮导出产物与上游流程版本，不改造上游缓存文件 |

给 manifest 来源分配本轮唯一的 `S1` 等 ID，并保留 `upstream` 记录中的 `view_file`、`index_id`、`revision`、`evidence_id`、`content_hash`。来源的 `canonical_path` 来自 `sourcePath`，`read_ranges` 来自实际 `range`；缺失时写明缺口，不编造消息级引用。一份证据可用于多张卡，一张工作卡也可引用多个会话。

每个工作 panel 增加 `upstream_worklines`，保留对应导出文件、`worklineId` 和索引的 `artifactId`、`revision`、`contentHash`。使用深读前核对 `dossier.worklineId` 及 `dossier.sourceIndex` 与所用索引一致；存在分支、反证、缺口或暂未验证的建议时保留其含义。`possibleChangeAdopted: false` 不能改写成用户已决定或已实施。

只拿到索引时展示索引层级；深读失败记为缺口，不能由 Two Hours 再读完整原文重新编写一套 dossier。需要补读、修复来源或改进梳理质量时，回到上游已有流程与接口。报告引用新增的外部产物仍按本项目文件合同登记。

上游会话 ID 与工作线建议不构成派发授权。Two Hours 继续使用原生 `list_threads` / `list_projects` 核验执行目标，按用户批注、派发方式和批准快照生成任务，保存真实发送回执。

## 同步上游更新

先在 agent-note-cli 修复会话格式、模型流程或工作线能力，再同步到 Two Hours。适配只放在 `scripts/`，不要手改 vendored 源码。更新使用指定提交或 tag，示例中的路径和版本替换为实际值：

```bash
git -C /path/to/agent-note-cli fetch origin --tags
node scripts/sync_agent_note.mjs --repo /path/to/agent-note-cli --ref TAG_OR_COMMIT
npm ci --ignore-scripts --prefix vendor/agent-note
node scripts/work_review.mjs --build
node scripts/sync_agent_note.mjs --repo /path/to/agent-note-cli --ref TAG_OR_COMMIT --check
node scripts/work_review.mjs --check
node --test tests/agent_note_sync.test.mjs tests/work_review.test.mjs
```

同步会核验上游仓库地址和内层 provenance，原子替换受管源码，移除已退役的模块，保留来源相同的依赖安装并作废旧构建。锁文件变化时重新安装依赖。已有源码被修改、出现未登记文件、目标不是受管快照或另一次同步正在进行时拒绝覆盖，保留原目录；先处理具体差异再重试。异常终止留下 `.sync-lock` 时，先确认原同步进程已结束再清理该锁目录。

不带上游路径的 `node scripts/sync_agent_note.mjs --check` 可离线校验当前快照。携带 `--repo` 与 `--ref` 时还会逐文件对照上游 Git 内容。相同提交重复同步不会改写源码或删除当前有效构建。

兼容测试直接复用上游的合成模型 fixture，核对原 `brief()` 与 Two Hours 入口的索引、深读、证据、版本和缓存一致；同时验证只读、stale 返回、更新增删文件、锁文件变化和本地改动保护。核心上游测试可在 `vendor/agent-note` 目录执行 `node --import tsx --test backend/upstream/tests/*.test.ts`。升级时运行这些检查及本项目 Python 测试后，再同步运行副本或发布；上游破坏性 API 变化会让构建或兼容测试失败，需要调整这一层适配，不能承诺任意版本无需检查即可运行。
