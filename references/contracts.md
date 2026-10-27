# 2hours 文件合同

一次运行一个私有目录。只向浏览器提供生成的报告、必要状态与分发预览，不提供目录浏览或原始会话。`manifest.json` 与 `report.md` 在录制前冻结；修改后须新建运行。所有时间有时区。

## 报告来源

`manifest.json` 由当前宿主完成证据读取后写入。下面是结构示例，示例 ID 不能用于真实派发。

```json
{
  "run_id": "20261023-090000",
  "scope": {"start": "2026-10-22T00:00:00+08:00", "end_exclusive": "2026-10-23T00:00:00+08:00", "timezone": "Asia/Shanghai"},
  "sources": [{"id": "S1", "provider": "codex", "session_id": "observed-session-id", "canonical_path": "/absolute/session.jsonl", "read_ranges": ["lines 20-80; 09:00–10:00"], "status": "complete"}],
  "panels": [{"id": "A1", "title": "实验结果与下一步", "source_ids": ["S1"]}],
  "coverage": {"read": 1, "failures": [], "skipped": []},
  "targets": [{"key": "T1", "type": "thread", "thread_id": "observed-session-id", "host_id": "local", "title": "工具返回的原始会话标题", "source_ids": ["S1"]}]
}
```

Cubox 来源使用 `provider: cubox`、`card_id`、`url`、`status: full_text|metadata_only|failed`。线程目标来自 Codex 原生工具的核验结果；目录路径不是 project ID。项目目标使用 `type: project`、`project_id`、`title`、`source_ids`，表示在该项目中新建会话，审核时必须明确显示“新建会话”。

每个报告二级标题使用 `## A1 标题` 等 answer-me-with-html 显式 panel ID；与 `panels` 一一对应。卡片上的 ID 是视频定位依据。页面焦点日志只有辅助意义，不可用 `meta.started_at` 直接减去墙钟来推断视频时间，因为选择窗口、倒计时、暂停及重录会改变时基。

## 理解与路由

Blurt 原生输出放在 `blurt/items.json`，可保留 `reviewed: false` 生成待审草案；Two Hours 不另开 Blurt 审核页，也不改写其审核标志。文字意见由用户在页面提交到 `written-items.json`，条目 ID 为 `W-<panel ID>`，保留 quote、panel_ids 和 confirmed 状态。两种输入可以单独或同时存在；未提交的 review-state.json 不参与派发。原话、帧和问题保持原样，用户更正另存。删除项不得派发。新路由写入 `routes.json`：

```json
{"tasks":[{"id":"D1","item_ids":["I-001"],"panel_ids":["A1"],"target_key":"T1","objective":"明确结果","acceptance":"可检查的验收条件","constraints":"用户要求的边界"}]}
```

`routes.json` 不是批准。未知目标、未解决问题、低置信度来源应留在报告中，不填造目标。任务的 `target_key` 必须来自 manifest 已核验目标，`panel_ids` 与 `item_ids` 必须存在。相同目标的任务合并成一次消息，减少并行发送对同一会话的反复打断。

若口述后需要新增或调整执行去向，用原生工具核验后在 `routes.json` 增加可选 `targets` 数组，提供完整的本轮目标表（结构同 manifest.targets）。此时它替代录制前的目标建议，不修改冻结 manifest；目标表与任务一起进入批准快照。source_ids 仍引用已冻结的报告来源。

`dispatch.py prepare-review --run RUN` 读取输入生成 `review-plan.json`。`review_id` 绑定 manifest、报告、原始条目和路由快照。`GET /review-plan` 提供目标表和逐项草案，原始证据在 `source_items`；`evidence_images` 提供经过限制的本机图片 URL，不暴露目录浏览或任意本地文件。

统一审核提交格式：

```json
{"review_id":"页面收到的草案标识","tasks":[{"id":"D1","selected":true,"target_key":"T1","objective":"用户核对或修改后的目标","constraints":"范围与限制","acceptance":"验收条件","answers":{"I-001":"对原话的更正或待答问题的回答"}}]}
```

`POST /approve-review` 校验草案版本、完整任务集合和目标白名单。默认不选中，未选项暂缓；选中任务的未决问题需要回答。最终确认将更正、选择和原始快照保存在 `review-confirmation.json`，再生成 `dispatch-plan.json` 与 `approval.json`，输出 `approved` 事件。全部暂缓也可完成，批准的批次列表为空，无需发送。这个动作同时确认理解与分发去向，不再要求第二次批准。相同提交幂等；确认后要改变内容须开新一轮。

执行方案的 `plan_id` 绑定最终确认与原始材料，`batches` 包含 `id`、`target`、`tasks`、`prompt`。同一目标合并一次发送。旧运行的 `dispatch.py prepare` 与 `/approve` 保留兼容，但新运行使用统一审核入口，不能把草案当成可执行方案。

## 宿主执行与回执

`dispatch.py claim --run RUN --batch ID` 检查快照与人工批准或下述有效自动授权，先持久化 `sending` 再返回原生工具参数。thread 目标用 `send_message_to_thread`；project 目标用 `create_thread`，指定已核验 project ID 和 local 环境。当前宿主执行工具，脚本不伪造宿主接口。

调用成功后将原始工具响应保存为 JSON 文件，通过 `dispatch.py receipt --run RUN --batch ID --response FILE` 登记 `sent`。明确失败登记 `failed`；结果不明保留 `sending`，先查目标会话，不能盲目重发。`sent` 只表示工具确认发送，不表示任务完成。未发送批次可恢复；报告或批准后条目变化会使旧批准失效。

一次运行不会自动新建定时任务，不会打开历史内容中的任意链接，不会修改 Cubox 卡片。跨会话收尾、次日结果回收仍由宿主用 read_thread / wait_threads 验证实际状态。

## 可选自动派发

默认人工流程不变。用户明确保存自动模式后，`settings.json` 增加 `dispatch_mode: automatic` 及 `dispatch_consent: {id, enabled_at}`。关闭后再开启会产生新 id，不设置额外的固定时间失效规则。设置本身不授权任何既有输入，也不会发送消息。

桥只在用户主动提交文字或完成其主动开始的口述时写入 `input-authorizations.json`。`inputs` 按 `written-items.json` 或 `blurt/recording.mp4` 分键，保留 `authorization_mode: automatic`、`opt_in_id`、`opt_in_at`、`submitted_at`、`sha256`、文件 `signature` 及提交时已知 `targets`。音视频只在提交时流式计算一次 SHA-256，后续核验设备、inode、大小、mtime 和 ctime，避免持续读取大录音。文字还在 `items` 中按条目 ID 保留授权与条目 SHA-256；未变化的已提交条目不获得新授权。原始 Blurt `reviewed: false` 不改写，不生成假的人工 `confirmed_at`、`approval.json` 或 `review-confirmation.json`。

`GET /dispatch-preferences` 返回当前模式及选择记录。文字提交必须包含与该响应一致的 `dispatch_opt_in`，人工模式为 `null`。`POST /submit-comments` 继续要求完整 revisions，模式变化或版本变化时拒绝提交。口述模式取开始录制时的用户选择；没有持久化绑定或完成后材料已变的录音只能待定，不能补造授权。

宿主整理 `items` 和 `routes` 后，只有用户目的地明确且真实核验过时，才给任务写 `target_basis: explicit`。每个自动任务还须有完整 objective/acceptance、有效条目和面板引用、高 confidence、无 questions，并且不是 deleted/note。一个条目被路由到多个目标时待定，不猜测用户意图。

提交后新增的目标仍可放入 `routes.targets`，不改冻结 manifest。新增目标同时要求任务的 `target_quote` 是 source_items.quote 中确实出现的原话片段，目标包含核验记录。例如下面仅为结构示例：

```json
{"key":"T2","type":"project","project_id":"observed-project-id","title":"工具返回的项目名称","source_ids":["S1"],"verification":{"tool":"list_projects","id":"observed-project-id","verified_at":"2026-10-27T09:30:00+08:00"}}
```

thread 目标对应 `list_threads` 和 thread_id。核验记录必须在这次提交之后，id 与目标一致。`verification` 是宿主真实工具调用的审计记录，填写字段本身不证明目标存在。宿主必须确认原话确实明确要求该项目或会话，不能用关键词猜测补齐；代码只核验引用与记录的一致性。缺少目标依据、原话、核验、授权或存在歧义的任务单独 pending，其余有效任务可形成批次。

推荐执行顺序为 `prepare-auto --run RUN`、`status --run RUN`，再对尚未领取的批次执行既有 `claim`、真实宿主工具及 `receipt`。自动关闭或没有有效当前选择时，prepare-auto 返回错误且不创建计划；已开启但输入无授权时可以生成仅含 pending 的空计划。自动计划明确带 `authorization_mode: automatic`、`opt_in_id`、`pending` 和 `review_id`，snapshot 绑定原始输入、路由与授权文件，plan_id 绑定这些内容。提示词明确其未经逐项人工审核。`claim` 在设置锁及运行锁内重新核验模式、选择标识和材料，不能把产品记录当作绕过原生宿主授权的依据。

`GET /dispatch-status` 提供真实 sending/sent/failed/pending 状态。关闭自动后仍可登记已经领取批次的真实回执；结果不明保留 sending，恢复时先核验目标，不重复 claim。`POST /request-manual-review {plan_id}` 持久化待定项请求并输出 `manual_review_requested` 事件，不发送任务。无派发尝试时，宿主可运行 prepare-review，旧自动计划移除并进入原人工表单；已有派发尝试时原计划不可改，只把 pending 带到新运行人工确认，防止已发项重发。
