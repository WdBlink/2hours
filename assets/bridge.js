(() => {
  "use strict";
  const config = JSON.parse(document.getElementById("twohours-config").textContent);
  const style = document.createElement("style");
  style.textContent = `
    #twohours-bar, #twohours-plan {font: 15px/1.6 system-ui; color: var(--ink, #222); background: var(--paper, #fff); border: 1px solid var(--line-2, #ccc); padding: 16px; margin: 12px; border-radius: 8px;}
    #twohours-bar {position: sticky; top: 0; z-index: 100; display: flex; align-items: center; gap: 12px; flex-wrap: wrap;}
    #twohours-bar button, #twohours-plan button {font: inherit; padding: 8px 16px; cursor: pointer; color: inherit; background: transparent; border: 1px solid currentColor; border-radius: 4px;}
    #twohours-bar a {color: inherit; text-underline-offset: 3px;}
    #twohours-bar .am-toolbar {position: static; margin-left: auto; flex-wrap: wrap;}
    #twohours-plan pre {white-space: pre-wrap; overflow-wrap: anywhere;}
    .am-panel, #twohours-plan {scroll-margin-top: calc(var(--twohours-bar-height, 90px) + 12px);}
    #twohours-bar button:disabled, #twohours-plan button:disabled {cursor: default; opacity: .55;}
    #twohours-plan article {border-top: 1px solid var(--line-2, #ccc); margin-top: 16px; padding-top: 12px;}
    #twohours-plan blockquote {white-space: pre-wrap; border-left: 3px solid #888; padding-left: 12px;}
    .twohours-panel-id {font: bold 13px/1 system-ui; display: inline-flex; align-items: center; justify-content: center; padding: 4px 8px; border: 1px solid currentColor;}
    @media print {#twohours-bar, #twohours-plan {display: none;}}
  `;
  document.head.append(style);
  function node(tag, text) {
    const el = document.createElement(tag);
    if (text !== undefined) el.textContent = String(text);
    return el;
  }
  function iconButton(label, path) {
    const button = node("button");
    button.type = "button";
    button.className = "panel-icon";
    button.setAttribute("aria-label", label);
    button.title = label;
    const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
    svg.setAttribute("viewBox", "0 0 24 24");
    svg.setAttribute("aria-hidden", "true");
    const shape = document.createElementNS(svg.namespaceURI, "path");
    shape.setAttribute("d", path);
    svg.append(shape);
    button.append(svg);
    return button;
  }
  async function request(path, body) {
    const response = await fetch(path, body === undefined ? {cache: "no-store"} : {
      method: "POST", headers: {"Content-Type": "application/json", "X-2hours-Token": config.token},
      body: JSON.stringify(body)
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error || "请求失败");
    return data;
  }
  const bar = document.getElementById("review-controls") || node("section");
  bar.id = "twohours-bar";
  bar.setAttribute("aria-label", "口述批注");
  const record = node("button", "开始口述批注");
  record.type = "button";
  record.disabled = true;
  const status = node("span", "正在读取状态…");
  status.setAttribute("role", "status");
  const reviewLink = node("a", "查看分发清单");
  reviewLink.href = "#twohours-plan";
  const notesWindow = document.getElementById("notes-window");
  if (notesWindow) {
    reviewLink.textContent = "集中审核";
    reviewLink.addEventListener("click", event => {event.preventDefault(); notesWindow.showModal(); loadNotes();});
    notesWindow.querySelector(".window-close").addEventListener("click", () => notesWindow.close());
  }
  bar.append(record, status, reviewLink);
  const toolbar = document.querySelector(".am-toolbar");
  if (toolbar) bar.append(toolbar);
  if (!bar.parentNode) document.body.prepend(bar);
  const statusSlot = document.getElementById("review-status");
  if (statusSlot) statusSlot.append(status);
  new ResizeObserver(() => document.documentElement.style.setProperty("--twohours-bar-height", `${bar.offsetHeight}px`)).observe(bar);
  const planBox = node("section");
  planBox.id = "twohours-plan";
  planBox.append(node("h2", "等待整理你的意见"), node("p", "完成口述或提交文字意见后，AI 会把任务理解与执行去向放在同一份清单中。你只需在这里确认一次。"));
  (document.getElementById("notes-content") || document.body).append(planBox);

  const reviewRows = new Map();
  let savedState = {panels: {}};
  let notesPreview = null;
  const notesBox = node("details");
  notesBox.id = "written-notes";
  if (notesWindow) planBox.before(notesBox);
  function paintCard(id, value) {
    const card = document.querySelector(`[data-panel="panel-${id}"]`);
    if (!card) return;
    card.dataset.read = String(value.read);
    const label = card.querySelector(".read-label");
    if (label) label.textContent = value.read ? "已审阅" : "待审阅";
    const comment = card.querySelector(".comment-label");
    if (comment) comment.textContent = value.comment.trim() ? "有意见" : "";
    const row = reviewRows.get(id);
    if (row) {
      const label = value.read ? "改为待审阅" : "标记已审阅";
      row.read.setAttribute("aria-label", label);
      row.read.setAttribute("aria-pressed", String(value.read));
      row.read.title = label;
    }
  }
  async function saveRow(id, update) {
    const value = savedState.panels[id];
    if (!value) throw new Error("状态尚未载入，请稍后重试");
    const result = await request("/review-state", {panel_id: id, revision: value.revision, ...update});
    savedState.panels[id] = result.panels[id];
    paintCard(id, result.panels[id]);
    return result;
  }
  async function loadNotes() {
    notesBox.replaceChildren(node("summary", "文字批注"), node("p", "正在读取已保存的意见…"));
    try {
      notesPreview = await request("/review-state");
      const entries = Object.entries(notesPreview.panels).filter(([, value]) => value.comment.trim());
      notesBox.replaceChildren(node("summary", `文字批注 · ${entries.length} 份`), node("p", "提交给 AI 整理，与口述合并后，在下方一次核对理解和去向。"));
      for (const [id, value] of entries) {
        const card = node("article");
        const panel = document.getElementById(`panel-${id}`);
        card.append(node("h3", `${id} · ${panel.querySelector(".am-panel-head h2").textContent}`), node("blockquote", value.comment));
        notesBox.append(card);
      }
      if (!entries.length) notesBox.append(node("p", "暂无已保存的文字意见。请打开一份详情，点击“写意见”。"));
      const submit = node("button", `提交文字意见（${entries.length} 项）`);
      submit.type = "button";
      const previous = notesPreview.submitted?.items || [];
      const same = previous.length === entries.length && entries.every(([id, value]) => previous.some(item => item.id === `W-${id}` && item.quote === value.comment));
      submit.disabled = !entries.length || same;
      const result = node("p", same && entries.length ? "这份文字批注已提交，等待当前会话整理。" : "保存不会发送任务；执行前会显示具体目标供你确认。");
      result.setAttribute("role", "status");
      submit.addEventListener("click", async () => {
        submit.disabled = true;
        try {
          await request("/submit-comments", {revisions: Object.fromEntries(Object.entries(notesPreview.panels).map(([id,v]) => [id,v.revision]))});
          result.textContent = "已提交文字批注，当前会话将与口述结果一起整理。";
        } catch (error) {result.textContent = error.message; submit.disabled = false;}
      });
      notesBox.append(submit, result);
    } catch (error) {notesBox.append(node("p", error.message));}
  }
  if (notesWindow) {
    for (const id of config.panels) {
      const panel = document.getElementById(`panel-${id}`);
      const tools = node("div"); tools.className = "panel-tools";
      const edit = iconButton("写意见", "M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2ZM7 9h10M7 13h7");
      edit.setAttribute("aria-expanded", "false");
      const read = iconButton("标记已审阅", "M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9ZM8 12l3 3 5-6"); read.disabled = true;
      tools.append(edit, read); panel.querySelector(".am-panel-head").append(tools);
      const form = node("div"); form.className = "panel-comment"; form.hidden = true;
      const label = node("label", `对这份简报的意见 · ${id}`);
      const box = node("textarea"); box.rows = 3; box.maxLength = 10000; box.id = `comment-${id}`; label.htmlFor = box.id;
      const save = node("button", "保存意见"); save.type = "button"; save.disabled = true;
      const message = node("span", ""); message.setAttribute("role", "status");
      form.append(label, box, save, message); panel.querySelector(".am-panel-head").after(form);
      const draftKey = `2hours:${config.run_id}:${id}`;
      const row = {read, box, message, dirty: false}; reviewRows.set(id, row);
      edit.addEventListener("click", () => {form.hidden = !form.hidden; edit.setAttribute("aria-expanded", String(!form.hidden)); if (!form.hidden) box.focus();});
      box.addEventListener("input", () => {
        row.dirty = true; message.textContent = "未保存";
        try {localStorage.setItem(draftKey, box.value);} catch {message.textContent = "未保存；浏览器草稿不可用，请先保存再关闭";}
      });
      save.addEventListener("click", async () => {
        save.disabled = true;
        const text = box.value;
        try {
          await saveRow(id, {comment: text});
          row.dirty = box.value !== text;
          if (!row.dirty) {try {localStorage.removeItem(draftKey);} catch {}}
          message.textContent = row.dirty ? "已保存上一版，当前输入仍未保存" : "已保存 · 可在“集中审核”中汇总提交";
        } catch (error) {message.textContent = error.message;}
        finally {save.disabled = false;}
      });
      read.addEventListener("click", async () => {
        read.disabled = true;
        try {await saveRow(id, {read: !savedState.panels[id].read});}
        catch (error) {form.hidden = false; edit.setAttribute("aria-expanded", "true"); message.textContent = error.message;}
        finally {read.disabled = false;}
      });
      row.initialize = value => {
        box.value = value.comment;
        try {const draft = localStorage.getItem(draftKey); if (draft !== null && draft !== value.comment) {box.value = draft; row.dirty = true; message.textContent = "已恢复未保存的草稿";}} catch {}
        if (box.value) {form.hidden = false; edit.setAttribute("aria-expanded", "true");}
        save.disabled = false; read.disabled = false; paintCard(id, value);
      };
    }
    request("/review-state").then(state => {
      savedState = state;
      for (const [id, row] of reviewRows) row.initialize(state.panels[id]);
    }).catch(error => {status.textContent = error.message;});
    window.addEventListener("beforeunload", event => {
      if ([...reviewRows.values()].some(row => row.dirty) || reviewSaveError) {event.preventDefault(); event.returnValue = "";}
    });
  }

  let startError = "";
  record.addEventListener("click", async () => {
    startError = "";
    record.disabled = true;
    status.textContent = "正在启动 Blurt，请在弹出的窗口选择录制区域…";
    try { await request("/record", {}); }
    catch (error) { startError = error.message; status.textContent = startError; }
  });
  for (const id of config.panels) {
    const panel = document.getElementById(`panel-${id}`);
    if (!panel) continue;
    const badge = panel.querySelector(".am-panel-id") || node("span");
    badge.textContent = id;
    badge.classList.add("twohours-panel-id");
    if (!badge.parentNode) panel.prepend(badge);
    let lastFocus = 0;
    for (const [event, kind] of [["click", "click"], ["focusin", "focus"]]) {
      panel.addEventListener(event, () => {
        if (kind === "focus" && Date.now() - lastFocus < 500) return;
        lastFocus = Date.now();
        request("/focus", {panel_id: id, kind}).catch(() => {});
      });
    }
  }

  let shownPlan = null;
  function showPlan(plan) {
    if (!plan) {
      if (shownPlan !== null) planBox.replaceChildren(node("h2", "分发清单"), node("p", "批注已更新，等待当前会话重新整理执行目标。"));
      shownPlan = null;
      return;
    }
    const approved = new Set(plan.approved_batch_ids || []);
    const viewKey = JSON.stringify([plan.plan_id, [...approved].sort()]);
    if (shownPlan === viewKey) return;
    shownPlan = viewKey;
    planBox.replaceChildren(node("h2", "确认分发清单"), node("p", "选择要执行的批次，再点击“确认并分发”。未勾选的批次不会获准发送。"));
    const selected = new Set();
    const submit = node("button", "确认并分发（0 项）");
    submit.type = "button";
    submit.disabled = true;
    const result = node("p");
    result.setAttribute("role", "status");
    if (approved.size) result.textContent = `已有 ${approved.size} 个批次获准分发，发送结果以当前会话回执为准。其余批次仍可选择。`;
    for (const batch of plan.batches) {
      const card = node("article");
      const label = node("label");
      const check = node("input");
      check.type = "checkbox";
      check.checked = approved.has(batch.id);
      check.disabled = approved.has(batch.id);
      const target = batch.target;
      const destination = target.type === "project" ? "新建会话 · 项目" : "继续原会话";
      label.append(check, document.createTextNode(` ${batch.id} · ${destination}：${target.title || target.thread_id || target.project_id}`));
      if (approved.has(batch.id)) label.append(document.createTextNode("（已批准）"));
      card.append(label);
      for (const task of batch.tasks || []) {
        card.append(node("h3", task.objective || task.title || task.id));
        for (const item of task.source_items || []) {
          if (item.quote) card.append(node("blockquote", `原话：${item.quote}`));
          if (item.answer) card.append(node("p", `审核更正：${item.answer}`));
        }
        if (task.quote) card.append(node("blockquote", `原话：${task.quote}`));
        if (task.constraints) card.append(node("p", `范围与约束：${task.constraints}`));
        if (task.acceptance) card.append(node("p", `验收：${task.acceptance}`));
      }
      const details = node("details");
      details.append(node("summary", "查看完整执行指令"), node("pre", batch.prompt || ""));
      card.append(details);
      check.addEventListener("change", () => {
        if (check.checked) selected.add(batch.id); else selected.delete(batch.id);
        submit.disabled = selected.size === 0;
        submit.textContent = `确认并分发（${selected.size} 项）`;
      });
      planBox.append(card);
    }
    submit.addEventListener("click", async () => {
      submit.disabled = true;
      try {
        const approval = await request("/approve", {plan_id: plan.plan_id, batch_ids: [...selected]});
        showPlan({...plan, approved_batch_ids: approval.batch_ids});
      } catch (error) {
        result.textContent = error.message;
        submit.disabled = selected.size === 0;
      }
    });
    planBox.append(submit, result);
  }
  let currentReview = null;
  let reviewDraft = [];
  let reviewIndex = 0;
  let reviewSaveError = false;
  let reviewChanged = false;
  let newerReview = null;
  let reviewBusy = false;
  let reviewUnavailable = "";
  let reviewUI = null;
  const draftKey = id => `2hours:${config.run_id}:approval:${id}`;
  const itemQuestions = item => Array.isArray(item.questions) ? item.questions : item.questions ? [item.questions] : [];
  const questionText = question => typeof question === "string" ? question : question?.question || question?.text || JSON.stringify(question);
  function defaultTask(task) {
    return {id: task.id, selected: Boolean(task.selected), target_key: task.target_key,
      objective: task.objective || "", constraints: task.constraints || "", acceptance: task.acceptance || "",
      answers: Object.fromEntries((task.source_items || []).map(item => [item.id, task.answers?.[item.id] || item.answer || ""]))};
  }
  function taskProblems(task, original) {
    const problems = [];
    if (!task.objective.trim()) problems.push("补充任务指令");
    if (!task.acceptance.trim()) problems.push("补充验收标准");
    if (!currentReview.targets.some(target => target.key === task.target_key)) problems.push("选择执行去向");
    if ((original.source_items || []).some(item => item.status === "deleted")) problems.push("原始意见已撤回，请暂缓此项");
    if ((original.source_items || []).some(item => (itemQuestions(item).length || item.confidence === "low") && !task.answers[item.id]?.trim())) problems.push("回答待澄清问题");
    return problems;
  }
  function saveReviewDraft() {
    reviewChanged = true;
    try {
      localStorage.setItem(draftKey(currentReview.review_id), JSON.stringify({tasks: reviewDraft, active_index: reviewIndex}));
      reviewSaveError = false;
    } catch {reviewSaveError = true;}
    updateReviewSummary();
  }
  function updateReviewSummary() {
    if (!reviewUI) return;
    const selected = reviewDraft.filter(task => task.selected);
    const unresolved = reviewDraft.filter((task, i) => task.selected && taskProblems(task, currentReview.tasks[i]).length);
    const approved = Boolean(currentReview.approval);
    reviewUI.count.textContent = `${selected.length} 项${approved ? "获准执行" : "选入执行"} · ${reviewDraft.length - selected.length} 项暂缓`;
    reviewUI.draft.textContent = approved ? "最终决定已保存，发送进展以会话回执为准。" : reviewUnavailable || (newerReview ? "清单已更新；请载入新版后确认。旧稿仍保存在浏览器。" : reviewSaveError ? "浏览器无法保存草稿，请勿刷新或关闭页面。" : unresolved.length ? `${unresolved.length} 项还需补充，点左侧任务继续核对。` : reviewChanged ? "修改已暂存在此浏览器，收起后可继续。" : "核对原意与去向；未选中的任务本次不分发。");
    reviewUI.submit.textContent = approved ? "已完成本轮审核" : selected.length ? `确认并分发 ${selected.length} 项` : "全部暂缓，完成审阅";
    reviewUI.submit.disabled = approved || reviewBusy || reviewUnavailable || Boolean(newerReview) || Boolean(unresolved.length);
    reviewUI.reload.hidden = !newerReview;
    reviewDraft.forEach((task, i) => {
      const row = reviewUI.rows[i];
      const target = currentReview.targets.find(candidate => candidate.key === task.target_key);
      const problems = taskProblems(task, currentReview.tasks[i]);
      row.button.setAttribute("aria-current", i === reviewIndex ? "step" : "false");
      row.title.textContent = task.objective.trim() || "待补充的任务";
      row.target.textContent = target?.title || "待选择执行去向";
      row.state.textContent = !task.selected ? "暂缓" : problems.length ? "待补充" : approved ? "已确认" : "本轮执行";
      row.state.dataset.state = !task.selected ? "deferred" : problems.length ? "question" : "selected";
    });
  }
  function renderReviewTask(focus = false) {
    const original = currentReview.tasks[reviewIndex];
    const task = reviewDraft[reviewIndex];
    if (!task) return;
    const approved = Boolean(currentReview.approval) || reviewBusy;
    const page = reviewUI.page;
    page.replaceChildren();
    page.scrollTop = 0;
    const heading = node("header"); heading.className = "approval-page-heading";
    const folio = node("span", `${String(reviewIndex + 1).padStart(2, "0")} / ${String(reviewDraft.length).padStart(2, "0")} · ${(original.panel_ids || []).join(" · ") || "口述与批注"}`); folio.className = "approval-eyebrow";
    const title = node("h3", original.source_items?.[0]?.title || `任务 ${reviewIndex + 1}`); title.tabIndex = -1;
    heading.append(folio, title); page.append(heading);
    const source = node("section"); source.className = "approval-source";
    source.append(node("h4", "你的原话与现场"));
    for (const item of original.source_items || []) {
      const evidence = node("div"); evidence.className = "approval-evidence";
      if (item.quote) evidence.append(node("blockquote", item.quote));
      evidence.append(node("small", `${item.input_method === "written" ? "文字批注" : "口述记录"} · ${item.id}`));
      if (item.evidence_images?.length) {
        const frames = node("details"); frames.append(node("summary", `查看画面证据 · ${item.evidence_images.length} 张`));
        for (const [index, frame] of item.evidence_images.entries()) {
          if (typeof frame.url !== "string" || !frame.url.startsWith("/evidence/")) continue;
          const figure = node("figure");
          const img = node("img"); img.src = frame.url; img.loading = "lazy"; img.alt = frame.caption || `录制画面 ${index + 1}`;
          figure.append(img, node("figcaption", frame.caption || `画面 ${index + 1}`)); frames.append(figure);
        }
        evidence.append(frames);
      }
      source.append(evidence);
    }
    if (!original.source_items?.length) source.append(node("p", "此项没有附带原话，请结合报告核对。"));
    page.append(source);
    const understanding = node("section"); understanding.className = "approval-fields approval-understanding";
    function field(labelText, key, rows) {
      const group = node("div"); group.className = "approval-field";
      const label = node("label", labelText);
      const input = node("textarea"); input.rows = rows; input.value = task[key]; input.maxLength = 10000; input.disabled = approved;
      input.id = `approval-${task.id}-${key}`; label.htmlFor = input.id;
      input.addEventListener("input", () => {task[key] = input.value; saveReviewDraft();});
      group.append(label, input); understanding.append(group);
    }
    field("任务指令 · 可直接修改", "objective", 2);
    field("范围与约束", "constraints", 1);
    field("完成的标准", "acceptance", 1);
    page.append(understanding);
    const questions = (original.source_items || []).filter(item => itemQuestions(item).length || item.confidence === "low");
    if (questions.length) {
      const section = node("section"); section.className = "approval-questions approval-fields";
      section.append(node("h4", "还有一点需要你说明"));
      for (const item of questions) {
        const label = node("label", itemQuestions(item).map(questionText).join("\n") || "这段理解把握较低，请补充或更正你的意思。");
        const answer = node("textarea"); answer.rows = 2; answer.maxLength = 10000; answer.value = task.answers[item.id] || ""; answer.disabled = approved;
        answer.id = `approval-${task.id}-answer-${item.id}`; label.htmlFor = answer.id;
        answer.addEventListener("input", () => {
          for (const related of reviewDraft) if (Object.hasOwn(related.answers, item.id)) related.answers[item.id] = answer.value;
          saveReviewDraft();
        });
        section.append(label, answer);
      }
      page.append(section);
    }
    const destination = node("section"); destination.className = "approval-destination approval-fields";
    const targetLabel = node("label", "执行去向"); targetLabel.htmlFor = `approval-${task.id}-target`;
    const select = node("select"); select.id = targetLabel.htmlFor; select.disabled = approved;
    select.append(new Option("请选择已核验的会话或项目", ""));
    for (const target of currentReview.targets) select.append(new Option(`${target.type === "project" ? "项目新会话" : "继续原会话"} · ${target.title || target.key}`, target.key));
    select.value = task.target_key;
    const targetDetail = node("p"); targetDetail.className = "approval-target-detail";
    function showTarget() {
      const target = currentReview.targets.find(candidate => candidate.key === task.target_key);
      targetDetail.textContent = target ? `${target.type === "project" ? "在项目中新建执行会话" : "接续已有会话的上下文"} · ${target.thread_id || target.project_id || target.key}` : "只显示报告中已核验的执行目标。";
    }
    select.addEventListener("change", () => {task.target_key = select.value; showTarget(); saveReviewDraft();});
    showTarget(); destination.append(targetLabel, select, targetDetail);
    const choice = node("label"); choice.className = "approval-choice";
    const checkbox = node("input"); checkbox.type = "checkbox"; checkbox.checked = task.selected; checkbox.disabled = approved;
    const choiceCopy = node("span"); choiceCopy.append(node("strong", "纳入本轮执行"), node("small", "未选中即暂缓；最终确认前不会发送。"));
    checkbox.addEventListener("change", () => {task.selected = checkbox.checked; saveReviewDraft();});
    choice.append(checkbox, choiceCopy); destination.append(choice); understanding.after(destination);
    if ((original.source_items || []).some(item => item.status === "deleted")) destination.append(node("p", "这项引用的原始意见已撤回，请保持暂缓，交由当前会话重新整理。"));
    const paging = node("nav"); paging.className = "approval-paging"; paging.setAttribute("aria-label", "逐项审阅");
    for (const [label, index] of [["← 上一项", reviewIndex - 1], ["下一项 →", reviewIndex + 1]]) {
      const button = node("button", label); button.type = "button"; button.disabled = index < 0 || index >= reviewDraft.length;
      button.addEventListener("click", () => {reviewIndex = index; renderReviewTask(true); if (!currentReview.approval) saveReviewDraft();}); paging.append(button);
    }
    page.append(paging); updateReviewSummary();
    if (focus) title.focus({preventScroll: true});
  }
  function showReview(plan, force = false) {
    if (!plan) return false;
    reviewUnavailable = "";
    if (currentReview?.review_id === plan.review_id && !force) {
      if (JSON.stringify(currentReview.approval) === JSON.stringify(plan.approval)) {updateReviewSummary(); return true;}
    } else if (currentReview && reviewChanged && !force && !currentReview.approval) {
      newerReview = plan; updateReviewSummary(); return true;
    }
    currentReview = plan; newerReview = null; reviewChanged = false; reviewSaveError = false;
    reviewDraft = plan.tasks.map(defaultTask); reviewIndex = 0;
    if (!plan.approval) {
      try {
        const saved = JSON.parse(localStorage.getItem(draftKey(plan.review_id)));
        if (Array.isArray(saved?.tasks)) {
          reviewDraft.forEach(task => {
            const entry = saved.tasks.find(candidate => candidate.id === task.id);
            if (!entry) return;
            for (const key of ["objective", "constraints", "acceptance", "target_key"]) if (typeof entry[key] === "string") task[key] = entry[key];
            task.selected = entry.selected === true;
            for (const id of Object.keys(task.answers)) if (typeof entry.answers?.[id] === "string") task.answers[id] = entry.answers[id];
          });
          reviewIndex = Math.max(0, Math.min(Number.isInteger(saved.active_index) ? saved.active_index : 0, reviewDraft.length - 1)); reviewChanged = true;
        }
      } catch {reviewSaveError = true;}
    } else {try {localStorage.removeItem(draftKey(plan.review_id));} catch {}}
    document.getElementById("notes-content")?.classList.add("unified-content");
    notesWindow?.classList.add("unified-review");
    planBox.classList.add("approval-plan");
    planBox.replaceChildren();
    const body = node("div"); body.className = "approval-body";
    const rail = node("nav"); rail.className = "approval-rail"; rail.setAttribute("aria-label", "待审核任务");
    const railTitle = node("p", `${reviewDraft.length} 项决定`); railTitle.className = "approval-rail-title"; rail.append(railTitle);
    const page = node("article"); page.className = "approval-page";
    const rows = reviewDraft.map((task, i) => {
      const button = node("button"); button.type = "button"; button.className = "approval-nav-item";
      const number = node("span", String(i + 1).padStart(2, "0")); number.className = "approval-number";
      const copy = node("span"); copy.className = "approval-nav-copy";
      const title = node("strong"); const target = node("small"); const state = node("span"); state.className = "approval-item-state";
      copy.append(title, target, state); button.append(number, copy);
      button.addEventListener("click", () => {reviewIndex = i; renderReviewTask(true); if (!plan.approval) saveReviewDraft();});
      button.addEventListener("keydown", event => {
        if (!["ArrowDown", "ArrowUp", "Home", "End"].includes(event.key)) return;
        event.preventDefault();
        const next = event.key === "Home" ? 0 : event.key === "End" ? rows.length - 1 : Math.max(0, Math.min(i + (event.key === "ArrowDown" ? 1 : -1), rows.length - 1));
        reviewIndex = next; renderReviewTask(); rows[next].button.focus();
      });
      rail.append(button); return {button, title, target, state};
    });
    body.append(rail, page);
    const footer = node("footer"); footer.className = "approval-footer";
    const summary = node("div"); const count = node("strong"); const draft = node("span"); draft.setAttribute("role", "status"); summary.append(count, draft);
    const actions = node("div"); actions.className = "approval-actions";
    const reload = node("button", "载入新版"); reload.type = "button"; reload.hidden = true; reload.addEventListener("click", () => showReview(newerReview, true));
    const submit = node("button"); submit.type = "button"; submit.className = "approval-submit";
    actions.append(reload, submit); footer.append(summary, actions);
    const errorBox = node("p"); errorBox.className = "approval-error"; errorBox.setAttribute("role", "alert"); errorBox.hidden = true;
    planBox.append(body, errorBox, footer);
    reviewUI = {rows, page, count, draft, reload, submit};
    submit.addEventListener("click", async () => {
      reviewBusy = true; updateReviewSummary(); errorBox.hidden = true;
      page.querySelectorAll("input, textarea, select").forEach(input => {input.disabled = true;});
      const submittedTasks = structuredClone(reviewDraft);
      try {
        const approval = await request("/approve-review", {review_id: currentReview.review_id, tasks: submittedTasks});
        const finalTasks = currentReview.tasks.map((task, i) => ({...task, ...submittedTasks[i]}));
        showReview({...currentReview, tasks: finalTasks, approval}, true);
      } catch (error) {errorBox.textContent = `尚未确认：${error.message}`; errorBox.hidden = false;}
      finally {reviewBusy = false; if (!currentReview.approval) page.querySelectorAll("input, textarea, select").forEach(input => {input.disabled = false;}); updateReviewSummary();}
    });
    renderReviewTask();
    reviewLink.textContent = plan.approval ? "查看本轮决定" : `集中审核 · ${plan.tasks.length}`;
    return true;
  }
  const messages = {
    idle: "可先阅读报告；点击按钮后才会启动录制。",
    recording: "请在 Blurt 浮条点击“完成”结束录制。可边阅读边口述、按住 ⌃ 拖动标注。",
    recorded: "录制完成，正在等待当前会话整理；理解与去向将在“集中审核”中一次确认。",
    cancelled: "录制已取消。",
    failed: "录制失败，请查看当前会话和 recording.log。",
    existing: "本轮已有录制记录，请在当前会话继续处理；重新录制请新建运行。"
  };
  async function refresh() {
    try {
      const state = await request("/status");
      record.disabled = !state.can_record;
      status.textContent = startError || (state.dependencies?.length ? state.dependencies.join("；") : state.message || messages[state.state]);
      const review = await request("/review-plan");
      if (!review && currentReview && !currentReview.approval) {reviewUnavailable = "资料已更新，等待当前会话重新整理；你的修改仍保留。"; updateReviewSummary();}
      if (!showReview(review) && !currentReview) showPlan(await request("/plan"));
    } catch (error) {
      status.textContent = `连接提示：${error.message}`;
      if (currentReview && !currentReview.approval) {
        reviewUnavailable = `暂时无法核验清单版本：${error.message}。修改仍保留，恢复后可继续。`;
        updateReviewSummary();
      }
    }
    setTimeout(refresh, 2500);
  }
  refresh();
})();
