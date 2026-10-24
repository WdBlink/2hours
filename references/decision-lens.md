# 决策审阅 lens

在 Blurt 原生 item 字段上增加以下字符串/列表字段，供 Two Hours 理解与路由草案使用：

- `decision_type`: approve / revise / research / defer / note。按实际意思分类，不强制每段话成为任务。
- `panel_ids`: 录屏画面中确认的 A1 等来源 ID，可多个，不确定时为空。
- `target_suggestion`: 人能识别的来源会话或项目名称；不能靠此字段直接发送。
- `objective`: 希望达到的结果。
- `acceptance`: 如何判定该结果达到；用户没说时标明是 Agent 建议。
- `constraints`: 用户限定的范围、时间、资源与明确不做的部分。

保留 quote、time、frames、confidence、questions。只靠模糊语气或页面焦点无法确定来源时 confidence=low 并提出具体问题。不要为消除问题而编造目标。最终以 Two Hours 统一审核中提交的 answer 和任务改写为准；未解决歧义的条目留待定，原始 items.json 保留作证据。

routes.json 是经整理的执行候选，不代替用户批准。defer/note 默认不派发；如果用户明确要求向某会话传达暂停，任务目标应准确写成“暂停该范围”，不可扩大为删除项目或终止其他工作。
