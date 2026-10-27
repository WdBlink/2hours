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
    .panel-read-status {font: 12px/1.5 system-ui; color: var(--ink-soft, #555); margin: 0 0 8px; min-height: 1.5em;}
    #twohours-settings {width:min(900px,calc(100vw - 32px));height:min(720px,calc(100dvh - 32px));max-height:calc(100dvh - 32px);box-sizing:border-box;padding:0;border:1px solid var(--line);border-radius:8px;background:var(--paper);color:var(--ink);font:14px/1.65 system-ui;overflow:hidden;}
    #twohours-settings[open] {display:flex;flex-direction:column;}
    #twohours-settings::backdrop {background:#342e2570;}
    #twohours-settings h2 {margin:0;font-size:20px;font-weight:600;}
    #twohours-settings h3 {margin:0 0 20px;font-size:20px;font-weight:550;}
    #twohours-settings button, #twohours-settings select {font:inherit;color:inherit;background:var(--paper-raised);border:1px solid var(--line);border-radius:4px;padding:9px 12px;}
    #twohours-settings button:disabled {opacity: .55;}
    #twohours-settings>header {display:flex;justify-content:space-between;align-items:center;padding:18px 24px;border-bottom:1px solid var(--line-2);}
    .retention-actions {display:flex;gap:10px;align-items:center;flex-wrap:wrap;margin:16px 0;}
    #twohours-settings .settings-layout {display:grid;grid-template-columns:160px minmax(0,1fr);flex:1;min-height:0;}
    #twohours-settings .settings-nav {padding:20px 12px;background:var(--paper-muted);border-right:1px solid var(--line-2);}
    #twohours-settings .settings-nav button {display:flex;align-items:center;gap:10px;width:100%;margin:0 0 6px;background:transparent;border-color:transparent;text-align:left;}
    #twohours-settings .settings-nav button[aria-selected=true] {background:var(--paper-raised);border-color:var(--line);color:var(--accent);}
    #twohours-settings .settings-page {padding:24px 28px;overflow:auto;min-width:0;}
    #twohours-settings .settings-page>p {color:var(--ink-2);}
    #twohours-settings .settings-status {min-height:24px;margin:0;padding:12px 24px;border-top:1px solid var(--line-2);background:var(--paper-raised);}
    #twohours-settings label {display: block; margin-bottom: 6px;}
    #twohours-settings select {max-width:100%;min-height:44px;appearance:auto;padding-right:24px;}
    #twohours-settings li, #twohours-settings p {overflow-wrap: anywhere;}
    .info-help {display:inline-flex;vertical-align:middle;margin-left:6px;}
    #twohours-settings button.info-button {width:28px;height:28px;min-height:28px;padding:4px;border:0;background:transparent;color:var(--ink-3);}
    .info-tip {position:fixed;z-index:200;box-sizing:border-box;width:280px;max-width:calc(100vw - 48px);padding:12px 14px;background:var(--ink);color:var(--paper-raised);border-radius:5px;box-shadow:0 6px 24px #342e2520;font:13px/1.6 system-ui;}
    body.layout-review #twohours-bar .toolbar-icon {display:grid;place-items:center;width:44px;min-width:44px;height:44px;padding:10px;border:1px solid var(--line);border-radius:4px;background:var(--paper);}
    .toolbar-icon svg,.settings-nav svg,.info-button svg,#twohours-settings .panel-icon svg {width:22px;height:22px;fill:none;stroke:currentColor;stroke-width:1.6;stroke-linecap:round;stroke-linejoin:round;}
    .toolbar-icon:hover,#twohours-settings button:hover:not(:disabled) {background:var(--accent-soft);}
    .toolbar-icon:focus-visible,#twohours-settings button:focus-visible,#twohours-settings select:focus-visible {outline:2px solid var(--accent);outline-offset:3px;}
    @media(max-width:540px) {#twohours-settings .settings-layout {grid-template-columns:108px minmax(0,1fr)}#twohours-settings .settings-nav {padding:16px 6px}#twohours-settings .settings-nav button {padding:9px 6px;gap:6px}#twohours-settings .settings-page {padding:20px 16px}#twohours-settings>header {padding:16px}#twohours-settings .settings-status {padding:10px 16px}}
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
  const icons = {record: "M9 5a3 3 0 0 1 6 0v7a3 3 0 0 1-6 0ZM5 10v2a7 7 0 0 0 14 0v-2M12 19v3M8 22h8", review: "M8 4h12v17H4V4h4M9 2h6v4H9ZM8 11h8M8 15h8", settings: "M9 3h6l1 3 3 1 2 5-2 5-3 1-1 3H9l-1-3-3-1-2-5 2-5 3-1ZM15 12a3 3 0 1 1-6 0 3 3 0 0 1 6 0", storage: "M4 4h16v16H4ZM4 9h16M8 14h8", send: "M3 11 21 3l-8 18-3-8ZM10 13 21 3", info: "M12 16v-5M12 7h.01M21 12a9 9 0 1 1-18 0 9 9 0 0 1 18 0", close: "m6 6 12 12M18 6 6 18"};
  function actionLabel(element, label, path) {
    element.replaceChildren(iconButton(label, path).firstElementChild);
    element.setAttribute("aria-label", label); element.title = label; element.classList.add("toolbar-icon");
  }
  let helpCount = 0;
  function infoHelp(label, text) {
    const wrap = node("span"); wrap.className = "info-help";
    const button = iconButton(label, icons.info); button.classList.add("info-button");
    button.removeAttribute("title");
    const tip = node("span", text); tip.id = `info-tip-${++helpCount}`; tip.className = "info-tip"; tip.setAttribute("role", "tooltip"); tip.hidden = true;
    button.setAttribute("aria-describedby", tip.id); button.setAttribute("aria-expanded", "false");
    const show = visible => {tip.hidden = !visible; button.setAttribute("aria-expanded", String(visible)); if (visible) {const rect = button.getBoundingClientRect(); tip.style.left = `${Math.max(24, Math.min(rect.left, innerWidth - 304))}px`; tip.style.top = `${Math.min(rect.bottom + 8, innerHeight - 180)}px`;}};
    wrap.addEventListener("pointerenter", () => show(true)); wrap.addEventListener("pointerleave", () => {if (!wrap.contains(document.activeElement)) show(false);});
    button.addEventListener("focus", () => show(true)); button.addEventListener("blur", () => show(false)); button.addEventListener("click", () => show(true));
    button.addEventListener("keydown", event => {if (event.key === "Escape") {event.stopPropagation(); show(false);}});
    wrap.append(button, tip); return wrap;
  }
  window.addEventListener("resize", () => {document.querySelectorAll(".info-help").forEach(wrap => {wrap.querySelector(".info-tip").hidden = true; wrap.querySelector("button").setAttribute("aria-expanded", "false");});});
  async function request(path, body) {
    let response;
    try {response = await fetch(path, body === undefined ? {cache: "no-store"} : {
      method: "POST", headers: {"Content-Type": "application/json", "X-2hours-Token": config.token},
      body: JSON.stringify(body)
    });} catch {
      throw new Error("本地审阅服务未连接。请回到当前会话恢复服务，再刷新本页；未保存文字请留在本页，浏览器草稿会在相同地址恢复。");
    }
    const data = await response.json();
    if (!response.ok) throw new Error((data.error || "请求失败") + (response.status === 403 ? " 如服务刚重启，请刷新本页恢复连接。" : ""));
    return data;
  }
  const bar = document.getElementById("review-controls") || node("section");
  bar.id = "twohours-bar";
  bar.setAttribute("aria-label", "批注与审核");
  const record = node("button"); actionLabel(record, "开始口述批注", icons.record);
  record.type = "button";
  record.disabled = true;
  const status = node("span", "正在读取状态…");
  status.setAttribute("role", "status");
  const reviewLink = node("a"); actionLabel(reviewLink, "查看分发清单", icons.review);
  reviewLink.href = "#twohours-plan";
  const notesWindow = document.getElementById("notes-window");
  if (notesWindow) {
    actionLabel(reviewLink, "汇总意见", icons.review);
    reviewLink.addEventListener("click", event => {event.preventDefault(); notesBox.open = !currentReview; notesWindow.showModal(); loadNotes();});
    notesWindow.querySelector(".window-close").addEventListener("click", () => notesWindow.close());
  }
  bar.append(record, status, reviewLink);
  const toolbar = document.querySelector(".am-toolbar");
  if (toolbar) bar.append(toolbar);
  if (!bar.parentNode) document.body.prepend(bar);
  const statusSlot = document.getElementById("review-status");
  if (statusSlot) statusSlot.append(status);
  new ResizeObserver(() => document.documentElement.style.setProperty("--twohours-bar-height", `${bar.offsetHeight}px`)).observe(bar);
  const settingsButton = iconButton("设置", icons.settings); settingsButton.classList.add("toolbar-icon");
  settingsButton.setAttribute("aria-haspopup", "dialog"); settingsButton.setAttribute("aria-controls", "twohours-settings"); bar.append(settingsButton);
  const settingsWindow = node("dialog"); settingsWindow.id = "twohours-settings";
  settingsWindow.setAttribute("aria-labelledby", "twohours-settings-title");
  const settingsHeader = node("header"); const settingsTitle = node("h2", "设置"); settingsTitle.id = "twohours-settings-title";
  const settingsClose = iconButton("关闭设置", icons.close); settingsClose.addEventListener("click", () => settingsWindow.close());
  settingsHeader.append(settingsTitle, settingsClose);
  const settingsContent = node("div"); settingsContent.className = "settings-layout";
  const settingsStatus = node("p"); settingsStatus.className = "settings-status"; settingsStatus.setAttribute("role", "status");
  settingsWindow.append(settingsHeader, settingsContent, settingsStatus); document.body.append(settingsWindow);
  const fileSize = bytes => bytes >= 1024 ** 3 ? `${(bytes / 1024 ** 3).toFixed(1)} GB` : bytes >= 1024 ** 2 ? `${(bytes / 1024 ** 2).toFixed(1)} MB` : `${Math.ceil(bytes / 1024)} KB`;
  let settingsSection = "retention";
  function showSettings(preview) {
    settingsStatus.textContent = settingsSection === "dispatch" ? "保存仅影响后续提交，不会发送已有任务。" : "预览不会删除文件。";
    const nav = node("nav"); nav.className = "settings-nav"; nav.setAttribute("aria-label", "设置分类"); nav.setAttribute("role", "tablist"); nav.setAttribute("aria-orientation", "vertical");
    const page = node("section"); page.className = "settings-page"; page.id = "settings-page"; page.setAttribute("role", "tabpanel"); page.tabIndex = 0;
    const sections = [["retention", "录音留存", icons.storage], ["dispatch", "批注派发", icons.send]];
    for (const [key, label, icon] of sections) {
      const tab = iconButton(label, icon); tab.append(node("span", label)); tab.id = `settings-tab-${key}`;
      tab.setAttribute("role", "tab"); tab.setAttribute("aria-selected", String(key === settingsSection)); tab.setAttribute("aria-controls", page.id); tab.tabIndex = key === settingsSection ? 0 : -1;
      const activate = () => {settingsSection = key; showSettings(preview); document.getElementById(tab.id).focus();};
      tab.addEventListener("click", activate);
      tab.addEventListener("keydown", event => {if (["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key)) {event.preventDefault(); settingsSection = event.key === "Home" ? "retention" : event.key === "End" ? "dispatch" : key === "retention" ? "dispatch" : "retention"; showSettings(preview); document.getElementById(`settings-tab-${settingsSection}`).focus();}});
      nav.append(tab);
    }
    page.setAttribute("aria-labelledby", `settings-tab-${settingsSection}`); settingsContent.replaceChildren(nav, page);
    if (settingsSection === "dispatch") {
      const heading = node("h3", "批注派发"); heading.append(infoHelp("了解自动派发", "开启后，仅后续主动开始的口述和提交的文字可使用自动模式。目标不明确、材料变化或仍有问题的条目会单独待定。关闭会撤销尚未领取的自动派发；重新开启不追溯授权旧输入。"));
      const label = node("label", "派发方式"); label.htmlFor = "dispatch-mode";
      const select = node("select"); select.id = label.htmlFor; select.append(new Option("人工确认（默认）", "manual"), new Option("自动派发明确任务", "automatic")); select.value = preview.settings.dispatch_mode;
      const scope = node("p", "自动模式会把你后续主动提交的明确任务发到对应 Codex 会话，或在对应已核验项目中新建会话。不会逐项再次确认；宿主要求的授权与权限仍然有效。");
      const current = node("p", preview.settings.dispatch_mode === "automatic" ? "当前已开启自动派发。既有发送记录不受切换影响。" : "当前为人工确认。仅选择并保存自动模式后，后续输入才会生效。");
      const save = node("button", "保存派发方式"); save.type = "button";
      select.addEventListener("change", () => {settingsStatus.textContent = "派发方式尚未保存。";});
      save.addEventListener("click", async () => {save.disabled = true; select.disabled = true; settingsStatus.textContent = "正在保存派发方式…"; try {showSettings(await request("/settings", {dispatch_mode: select.value, revision: preview.settings.revision})); settingsStatus.textContent = "派发方式已保存；本次设置操作没有发送任务。";} catch (error) {settingsStatus.textContent = `保存尚未确认：${error.message}`; save.disabled = false; select.disabled = false;}});
      const actions = node("div"); actions.className = "retention-actions"; actions.append(save);
      page.append(heading, label, select, scope, current, actions); return;
    }
    const heading = node("h3", "录音留存"); heading.append(infoHelp("了解留存计算", "到期从文件最后修改时间计算。“处理并完成审核后”也会保护未处理、未审核及正在录制的材料。卡片已审阅不等于录音审核完成。"));
    page.append(heading, node("p", "默认一直保留。保存策略不会删除文件；清理仅在下方明确执行，不会后台自动运行。"));
    const label = node("label", "录音及原始音视频留存"); label.htmlFor = "retention-policy";
    const select = node("select"); select.id = label.htmlFor;
    for (const option of preview.options) select.append(new Option(option.label, option.value));
    select.value = preview.settings.retention;
    const save = node("button", "保存设置"); save.type = "button";
    const reload = node("button", "刷新清理预览"); reload.type = "button";
    const actions = node("div"); actions.className = "retention-actions"; actions.append(select, save, reload);
    page.append(label, actions,
      node("p", `清理范围：${preview.scope} 内各运行的 blurt 目录，仅 ${preview.media_names.join("、")}。不会清理外部项目、截图、转写、报告或审核记录。`),
      node("p", "未处理、未完成审核及正在录制的材料始终受保护。"));
    for (const warning of preview.warnings) page.append(node("p", warning));
    const eligible = preview.files.filter(file => file.eligible);
    page.append(node("h3", `清理预览 · ${eligible.length} 个文件 · ${fileSize(preview.eligible_bytes)}`));
    const details = node("details"); details.open = true; details.append(node("summary", `全部 ${preview.files.length} 个文件及保护原因`));
    const list = node("ul");
    for (const file of preview.files) {
      const expires = file.expires_at ? `到期 ${new Date(file.expires_at).toLocaleString()}` : "无到期时间";
      list.append(node("li", `${file.path} · ${fileSize(file.bytes)} · ${expires} · ${file.reason}`));
    }
    if (!preview.files.length) list.append(node("li", "清理范围内暂无音视频文件。"));
    details.append(list); page.append(details);
    const cleanup = node("button", `删除预览中 ${eligible.length} 个已到期文件（${fileSize(preview.eligible_bytes)}）`); cleanup.type = "button"; cleanup.disabled = !eligible.length;
    page.append(node("p", "删除不可恢复。执行前会再次核验；若移动时文件或审核状态发生变化，会保留到运行目录内的恢复位置，并显示路径。"), cleanup);
    const busy = value => {select.disabled = value; save.disabled = value; reload.disabled = value; cleanup.disabled = value || !eligible.length || select.value !== preview.settings.retention;};
    select.addEventListener("change", () => {busy(false); settingsStatus.textContent = "设置尚未保存，文件未改变。";});
    reload.addEventListener("click", loadSettings);
    save.addEventListener("click", async () => {
      busy(true); settingsStatus.textContent = "正在保存设置…";
      try {showSettings(await request("/settings", {retention: select.value, revision: preview.settings.revision})); settingsStatus.textContent = "设置已保存，未删除任何文件。";}
      catch (error) {settingsStatus.textContent = `设置未保存：${error.message}`; busy(false);}
    });
    cleanup.addEventListener("click", async () => {
      busy(true); settingsStatus.textContent = "正在核验并清理预览中的文件…";
      try {
        const result = await request("/cleanup", {preview_id: preview.preview_id});
        showSettings(result.preview);
        settingsStatus.textContent = `已删除 ${result.deleted.length} 个文件。${result.failed.length ? `另有 ${result.failed.length} 个未删除：${result.failed.map(file => `${file.path}（${file.error}）${file.recovery_path ? `。恢复位置：${file.recovery_path}，请先核对，勿覆盖现有同名文件` : ""}`).join("；")}` : "其余材料已保留。"}`;
      } catch (error) {settingsStatus.textContent = `清理未完成，请刷新预览核对：${error.message}`; reload.disabled = false;}
    });
  }
  async function loadSettings() {
    settingsContent.replaceChildren(); settingsStatus.textContent = "正在读取留存设置与清理预览…";
    try {showSettings(await request("/settings"));}
    catch (error) {
      settingsStatus.textContent = error.message;
      const retry = node("button", "重新读取"); retry.type = "button"; retry.addEventListener("click", loadSettings); settingsContent.append(retry);
    }
  }
  settingsButton.addEventListener("click", () => {settingsWindow.showModal(); loadSettings();});
  const planBox = node("section");
  planBox.id = "twohours-plan";
  planBox.append(node("h2", "等待整理你的意见"), node("p", "完成口述或提交文字意见后，AI 会把任务理解与执行去向放在同一份清单中。你只需在这里确认一次。"));
  (document.getElementById("notes-content") || document.body).append(planBox);

  const reviewRows = new Map();
  let savedState = {panels: {}};
  const notesBox = node("details");
  notesBox.id = "written-notes";
  if (notesWindow) planBox.before(notesBox);
  function paintCard(id, value) {
    const card = document.querySelector(`[data-panel="panel-${id}"]`);
    if (card) card.dataset.read = String(value.read);
    const label = card?.querySelector(".read-label");
    if (label) label.textContent = value.read ? "已审阅" : "待审阅";
    const comment = card?.querySelector(".comment-label");
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
      const [notesPreview, preferences] = await Promise.all([request("/review-state"), request("/dispatch-preferences")]);
      const automatic = preferences.dispatch_mode === "automatic";
      const context = notesWindow?.querySelector(".window-context");
      if (context && !currentReview && !shownPlan) context.textContent = automatic ? "保存意见 → 提交整理 → 自动派发明确任务" : "保存意见 → 提交整理 → 集中审核 · 最后确认一次";
      const entries = Object.entries(notesPreview.panels).filter(([, value]) => value.comment.trim());
      notesBox.replaceChildren(node("summary", `文字意见 · ${entries.length} 份`), node("p", automatic ? "已开启自动派发。本次新增或修改的明确任务可发到对应会话，或在已核验项目中新建会话；不会逐项再次确认。未变化旧项不会补授当前自动权限，可能留待人工处理。" : "保存卡片意见后提交给 AI 整理，再集中审核结果。无需录音也可完成。"));
      const submit = node("button", automatic ? `提交并按自动模式处理（${entries.length} 份）` : `提交给 AI 整理（${entries.length} 份）`);
      submit.type = "button";
      submit.className = "notes-submit";
      const previous = notesPreview.submitted?.items || [];
      const same = previous.length === entries.length && entries.every(([id, value]) => previous.some(item => item.id === `W-${id}` && item.quote === value.comment));
      const dirty = [...reviewRows.values()].filter(row => row.dirty).length;
      submit.disabled = !entries.length || same || dirty > 0;
      const result = node("p", dirty ? `还有 ${dirty} 份意见未保存，请先回到对应卡片保存。` : same && entries.length ? automatic ? "已提交，等待当前会话处理；自动派发结果与待定项将在这里显示。" : "已提交，等待当前会话整理。这里会显示理解与去向，供你一次确认。" : automatic ? "只有目标明确且无需澄清的授权条目可自动派发；宿主权限仍须满足。" : "此步只提交意见，不会分发任务。整理完成后再由你最终确认。");
      result.setAttribute("role", "status");
      submit.addEventListener("click", async () => {
        submit.disabled = true;
        try {
          await request("/submit-comments", {revisions: Object.fromEntries(Object.entries(notesPreview.panels).map(([id,v]) => [id,v.revision])), dispatch_opt_in: preferences.dispatch_consent?.id || null});
          await loadNotes();
        } catch (error) {result.textContent = error.message; submit.disabled = false;}
      });
      notesBox.append(submit, result);
      if (!entries.length) notesBox.append(node("p", "暂无已保存的文字意见。请打开一份详情，点击对话框图标填写并保存。"));
      for (const [id, value] of entries) {
        const card = node("article");
        const panel = document.getElementById(`panel-${id}`);
        card.append(node("h3", `${id} · ${panel.querySelector(".am-panel-head h2").textContent}`), node("blockquote", value.comment));
        notesBox.append(card);
      }
    } catch (error) {
      const retry = node("button", "重新连接"); retry.type = "button";
      retry.addEventListener("click", loadNotes);
      const message = node("p", error.message); message.setAttribute("role", "alert");
      notesBox.replaceChildren(node("summary", "文字意见 · 暂时无法连接"), message, retry);
      notesBox.open = true;
    }
  }
  if (notesWindow) {
    for (const id of config.panels) {
      const panel = document.getElementById(`panel-${id}`);
      const tools = node("div"); tools.className = "panel-tools";
      const edit = iconButton("写意见", "M5 4h14a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H9l-5 3v-3H3V6a2 2 0 0 1 2-2ZM7 9h10M7 13h7");
      edit.setAttribute("aria-expanded", "false");
      const read = iconButton("标记已审阅", "M21 12a9 9 0 1 1-9-9 9 9 0 0 1 9 9ZM8 12l3 3 5-6"); read.disabled = true;
      const readStatus = node("p", "正在读取审阅状态…"); readStatus.className = "panel-read-status";
      readStatus.id = `read-status-${id}`; readStatus.setAttribute("role", "status"); read.setAttribute("aria-describedby", readStatus.id);
      tools.append(edit, read); panel.querySelector(".am-panel-head").append(tools);
      const form = node("div"); form.className = "panel-comment"; form.hidden = true;
      const label = node("label", `对这份简报的意见 · ${id}`);
      const box = node("textarea"); box.rows = 3; box.maxLength = 10000; box.id = `comment-${id}`; label.htmlFor = box.id;
      const save = node("button", "保存意见"); save.type = "button"; save.disabled = true;
      const message = node("span", ""); message.setAttribute("role", "status");
      form.append(label, box, save, message); panel.querySelector(".am-panel-head").after(readStatus, form);
      const draftKey = `2hours:${config.run_id}:${id}`;
      const row = {read, box, message, readStatus, dirty: false}; reviewRows.set(id, row);
      const busy = value => {save.disabled = value; read.disabled = value;};
      edit.addEventListener("click", () => {form.hidden = !form.hidden; edit.setAttribute("aria-expanded", String(!form.hidden)); if (!form.hidden) box.focus();});
      box.addEventListener("input", () => {
        row.dirty = true; message.textContent = "未保存";
        try {localStorage.setItem(draftKey, box.value);} catch {message.textContent = "未保存；浏览器草稿不可用，请先保存再关闭";}
      });
      save.addEventListener("click", async () => {
        busy(true);
        const text = box.value;
        try {
          await saveRow(id, {comment: text});
          row.dirty = box.value !== text;
          if (!row.dirty) {try {localStorage.removeItem(draftKey);} catch {}}
          message.textContent = row.dirty ? "已保存上一版，当前输入仍未保存" : `已保存 · 到右上角“${reviewLink.getAttribute("aria-label")}”汇总提交`;
        } catch (error) {message.textContent = error.message;}
        finally {busy(false);}
      });
      read.addEventListener("click", async () => {
        busy(true); readStatus.textContent = "正在保存审阅状态…";
        try {
          if (!savedState.panels[id]) await loadReviewState();
          busy(true);
          const result = await saveRow(id, {read: !savedState.panels[id].read});
          readStatus.textContent = `${result.panels[id].read ? "已审阅" : "待审阅"} · 已保存。仅记录阅读状态，不分发任务。`;
        } catch (error) {readStatus.textContent = `审阅状态未保存：${error.message}`;}
        finally {busy(false);}
      });
      row.initialize = value => {
        if (!row.dirty) {
          box.value = value.comment;
          try {const draft = localStorage.getItem(draftKey); if (draft !== null && draft !== value.comment) {box.value = draft; row.dirty = true; message.textContent = "已恢复未保存的草稿";}} catch {}
        }
        if (box.value) {form.hidden = false; edit.setAttribute("aria-expanded", "true");}
        save.disabled = false; read.disabled = false; paintCard(id, value);
        readStatus.textContent = `${value.read ? "已审阅" : "待审阅"} · 可独立切换，无需填写文字意见。`;
      };
    }
    window.addEventListener("beforeunload", event => {
      if ([...reviewRows.values()].some(row => row.dirty) || reviewSaveError) {event.preventDefault(); event.returnValue = "";}
    });
  }

  async function loadReviewState() {
    savedState = await request("/review-state");
    for (const [id, row] of reviewRows) row.initialize(savedState.panels[id]);
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
  function showAutomatic(plan) {
    const key = JSON.stringify([plan.plan_id, plan.dispatch_status]);
    if (shownPlan === key) return;
    shownPlan = key; currentReview = null;
    planBox.classList.remove("approval-plan"); notesWindow?.classList.remove("unified-review");
    document.getElementById("notes-content")?.classList.remove("unified-content");
    actionLabel(reviewLink, "自动派发结果", icons.review);
    const title = document.getElementById("notes-title"); if (title) title.textContent = "自动派发结果";
    const context = notesWindow?.querySelector(".window-context"); if (context) context.textContent = "明确任务自动派发 · 有疑问的条目单独待定";
    planBox.replaceChildren(node("h2", "自动派发结果"), node("p", "这些任务来自你开启自动模式后主动提交的批注。发送回执不代表执行完成。"));
    if (!plan.dispatch_status.snapshot_current) planBox.append(node("p", "模式已关闭、授权已变更或材料已更新。未领取批次暂停；已有发送记录保留。"));
    const states = {pending: "等待宿主派发", sending: "发送结果待核验，请勿重复发送", sent: "已发送", failed: "发送失败，待当前会话处理"};
    for (const batch of plan.batches) {
      const state = plan.dispatch_status.batches.find(value => value.id === batch.id);
      const article = node("article"); article.append(node("h3", batch.target.title), node("p", states[state?.status] || "待核验"));
      for (const task of batch.tasks) article.append(node("p", task.objective));
      planBox.append(article);
    }
    if (plan.pending.length) {
      planBox.append(node("h3", `待定 ${plan.pending.length} 项`));
      for (const task of plan.pending) planBox.append(node("p", `${task.objective || task.task_id}：${task.reasons.join("；")}`));
      const manual = node("button", "交当前会话补充并人工确认"); manual.type = "button";
      const result = node("p", "只处理待定项。已有发送记录不会重发；已有派发尝试时会将待定项单独放入下一轮人工确认。"); result.setAttribute("role", "status");
      manual.addEventListener("click", async () => {manual.disabled = true; try {await request("/request-manual-review", {plan_id: plan.plan_id}); result.textContent = "已请求当前会话补充待定项；尚未新增任何发送。";} catch (error) {result.textContent = error.message; manual.disabled = false;}});
      planBox.append(manual, result);
    }
  }
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
    actionLabel(reviewLink, plan.approval ? "查看本轮决定" : `集中审核 · ${plan.tasks.length}`, icons.review);
    const notesTitle = document.getElementById("notes-title");
    if (notesTitle) notesTitle.textContent = plan.approval ? "本轮决定" : "集中审核";
    const context = notesWindow?.querySelector(".window-context"); if (context) context.textContent = plan.approval ? "理解与去向已确认 · 发送进展以回执为准" : "核对原话、任务与去向 · 最后确认一次";
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
    if (notesWindow && !Object.keys(savedState.panels).length) {
      try {await loadReviewState();}
      catch (error) {
        for (const row of reviewRows.values()) {row.read.disabled = false; row.readStatus.textContent = `审阅状态未载入：${error.message} 可点击审阅按钮重试。`;}
      }
    }
    try {
      const state = await request("/status");
      record.disabled = !state.can_record;
      actionLabel(record, state.dispatch_mode === "automatic" ? "开始口述批注（自动派发已开启）" : "开始口述批注", icons.record);
      const message = state.state === "recorded" && state.dispatch_mode === "automatic" ? "录制完成，等待当前会话整理；明确且已授权的任务将自动派发，其余单独待定。" : messages[state.state];
      status.textContent = startError || (state.dependencies?.length ? state.dependencies.join("；") : state.message || message);
      const plan = await request("/plan");
      if (plan?.authorization_mode === "automatic") showAutomatic(plan);
      else {
        const review = await request("/review-plan");
        if (!review && currentReview && !currentReview.approval) {reviewUnavailable = "资料已更新，等待当前会话重新整理；你的修改仍保留。"; updateReviewSummary();}
        if (!showReview(review) && !currentReview) showPlan(plan);
      }
      const progress = plan?.dispatch_status || await request("/dispatch-status");
      if (progress) {
        const counts = Object.fromEntries(["pending", "sending", "sent", "failed"].map(key => [key, progress.batches.filter(batch => batch.status === key).length]));
        status.textContent = counts.sending ? `有 ${counts.sending} 批发送结果待核验，请勿重复发送。` : counts.failed ? `已发送 ${counts.sent} 批，${counts.failed} 批失败待处理。` : counts.pending ? `已发送 ${counts.sent} 批，${counts.pending} 批等待派发。` : counts.sent ? `已发送 ${counts.sent} 批。任务进展以目标会话回执为准。` : "本轮审核已完成，没有待发送批次。";
        if (progress.pending?.length) status.textContent += ` ${progress.pending.length} 项待澄清。`;
        if (!progress.snapshot_current) status.textContent += " 材料或授权已变化，未领取批次暂停。";
      }
      if (state.dispatch_mode === "automatic") status.textContent += " 自动派发已开启，仅适用于后续主动提交。";
    } catch (error) {
      record.disabled = true;
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
