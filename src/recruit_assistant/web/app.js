let state = null;
let activeTaskId = "";
let scheduleTimes = [];
let currentIndustries = [];
let expectedIndustries = [];
let currentFunctions = [];
let expectedFunctions = [];
let optionPickerKind = "industry";
let optionPickerTarget = "current";
let optionPickerCategory = "";
let optionPickerDraft = [];
let refreshJobsTimer = null;
const fieldIds = [
  "platform",
  "port", "keywords", "job_name", "company_name", "current_city", "expected_city",
  "maimai_keywords", "maimai_keyword_mode", "maimai_city", "maimai_education", "maimai_experience", "maimai_graduation_year",
  "maimai_company", "maimai_gender", "maimai_age_min", "maimai_age_max", "maimai_greeting",
  "experience", "recruitment_type", "active_status", "job_status",
  "job_hop_frequency", "age_min", "age_max", "gender_requirement", "language_requirement",
  "graduation_year", "deepseek_api_key", "deepseek_model", "candidate_limit", "maimai_page_limit", "maimai_auto_communicate", "match_requirements",
  "use_keywords_ai_words", "use_job_ai_words", "use_company_ai_words", "auto_communicate",
  "request_resume_after_communicate", "request_phone_after_communicate"
];

function optionHtml(values, selected = "") {
  return values.map(v => `<option value="${escapeHtml(v)}"${v === selected ? " selected" : ""}>${escapeHtml(v || "不设置")}</option>`).join("");
}
function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, m => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
}
async function api(path, payload) {
  const res = await fetch(path, {method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload || {})});
  return await res.json();
}
async function loadState(keepForm = false) {
  const res = await fetch("/api/state");
  state = await res.json();
  activeTaskId = state.active_task_id || (state.tasks[0] && state.tasks[0].id) || "";
  renderTasks();
  renderResults();
  renderLogs();
  document.getElementById("status").textContent = state.running
    ? (state.stop_requested ? `正在停止：${state.running_task}` : `运行中：${state.running_task}`)
    : "准备就绪";
  const versionEle = document.getElementById("appVersion");
  if (versionEle) versionEle.textContent = `版本 ${state.app_version || "--"}`;
  const stopButton = document.getElementById("stopTaskButton");
  if (stopButton) {
    stopButton.disabled = !state.running || !!state.stop_requested;
    stopButton.textContent = state.stop_requested ? "正在停止" : "停止";
  }
  if (!keepForm) fillForm(activeTask());
  else {
    renderJobSelect();
    applyPlatformUI();
  }
}
function activeTask() {
  return (state.tasks || []).find(t => t.id === activeTaskId) || state.tasks[0];
}
function currentPlatform() {
  const ele = document.getElementById("platform");
  return (ele && ele.value) || (activeTask()?.config?.platform) || "liepin";
}
function platformMeta(platform = currentPlatform()) {
  return (state.platforms || []).find(item => item.key === platform) || {key: "liepin", name: "猎聘", jobs_supported: true};
}
function renderTasks() {
  const box = document.getElementById("taskList");
  box.innerHTML = (state.tasks || []).map(task => `
    <div class="task ${task.id === activeTaskId ? "active" : ""}" onclick="selectTask('${task.id}')">
      <div class="task-name">${escapeHtml(task.name)}</div>
      <div class="task-meta">
        ${task.enabled ? "已启用" : "未启用"} · ${(task.times || []).join(", ") || "未设时间"}<br>
        ${escapeHtml(task.last_status || "未运行")}
      </div>
    </div>
  `).join("");
}
function fillSelect(id, selected) {
  document.getElementById(id).innerHTML = optionHtml(state.options[id] || [""], selected || "");
}
function renderCheckboxGroup(id, values, selectedValues = []) {
  const box = document.getElementById(id);
  if (!box) return;
  const selected = Array.isArray(selectedValues) ? selectedValues : String(selectedValues || "").split(/[,，、;]/).map(x => x.trim()).filter(Boolean);
  box.innerHTML = (values || []).filter(Boolean).map(label => {
    const checked = selected.includes(label) ? "checked" : "";
    return `<label class="check"><input type="checkbox" value="${escapeHtml(label)}" ${checked}>${escapeHtml(label)}</label>`;
  }).join("");
}
function setPanelVisible(id, visible) {
  const ele = document.getElementById(id);
  if (ele) ele.classList.toggle("hidden", !visible);
}
function applyPlatformUI() {
  const platform = currentPlatform();
  const meta = platformMeta(platform);
  const isLiepin = platform === "liepin";
  const refreshButton = document.querySelector('.toolbar button[onclick="refreshJobs()"]');
  if (refreshButton) {
    refreshButton.disabled = !meta.jobs_supported;
    refreshButton.textContent = isLiepin ? "刷新职位" : "刷新职位";
    refreshButton.title = meta.jobs_supported ? "刷新当前渠道职位" : "脉脉职位刷新后续接入";
  }
  setPanelVisible("liepinPanel", isLiepin);
  setPanelVisible("liepinOtherPanel", isLiepin);
  setPanelVisible("liepinAiPanel", isLiepin);
  setPanelVisible("maimaiAiPanel", platform === "maimai");
  setPanelVisible("maimaiPanel", platform === "maimai");
  document.getElementById("pageTitle").textContent = (activeTask()?.name || "自动搜索与沟通") + ` · ${meta.name}`;
}
function renderJobSelect(cfg = null) {
  const jobSelect = document.getElementById("selected_chat_job");
  if (!jobSelect) return;
  const currentLabel = jobSelect.value || "";
  const configLabel = cfg && cfg.selected_chat_job ? formatJobLabel(cfg.selected_chat_job) : "";
  const selectedJobLabel = currentLabel || configLabel;
  jobSelect.innerHTML = `<option value="">自动选择第一个职位</option>` + (state.jobs || []).map(job => {
    const label = formatJobLabel(job);
    return `<option value="${escapeHtml(label)}"${label === selectedJobLabel ? " selected" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
}
function fillForm(task) {
  if (!task) return;
  const cfg = {...state.defaults, ...(task.config || {})};
  document.getElementById("taskName").value = task.name || "";
  document.getElementById("enabled").checked = !!task.enabled;
  scheduleTimes = normalizeTimes(task.times || []);
  renderScheduleTimes();
  for (const id of Object.keys(state.options || {})) fillSelect(id, cfg[id]);
  renderCheckboxGroup("education", state.options.education || [], cfg.education || []);
  renderJobSelect(cfg);
  const schools = ["211", "985", "双一流", "海外留学"];
  document.getElementById("school_types").innerHTML = schools.map(label => {
    const checked = (cfg.school_types || []).includes(label) ? "checked" : "";
    return `<label class="check"><input type="checkbox" value="${label}" ${checked}>${label}</label>`;
  }).join("");
  for (const id of fieldIds) {
    const ele = document.getElementById(id);
    if (!ele) continue;
    if (ele.type === "checkbox") ele.checked = !!cfg[id];
    else if (id === "match_requirements" && (cfg[id] || "").trim() === (state.defaults.match_requirements || "").trim()) ele.value = "";
    else ele.value = cfg[id] ?? "";
  }
  currentIndustries = normalizeIndustries(cfg.current_industries || []);
  expectedIndustries = normalizeIndustries(cfg.expected_industries || []);
  currentFunctions = normalizeIndustries(cfg.current_functions || []);
  expectedFunctions = normalizeIndustries(cfg.expected_functions || []);
  renderOptionViews();
  applyPlatformUI();
}
function readForm() {
  const cfg = {};
  for (const id of fieldIds) {
    const ele = document.getElementById(id);
    if (!ele) continue;
    cfg[id] = ele.type === "checkbox" ? ele.checked : ele.value;
  }
  cfg.port = Number(cfg.port || 9225);
  cfg.platform = currentPlatform();
  cfg.candidate_limit = Number(cfg.candidate_limit || 1);
  cfg.maimai_page_limit = Number(cfg.maimai_page_limit || 1);
  cfg.school_types = Array.from(document.querySelectorAll("#school_types input:checked")).map(x => x.value);
  cfg.education = Array.from(document.querySelectorAll("#education input:checked")).map(x => x.value);
  cfg.current_industries = normalizeIndustries(currentIndustries);
  cfg.expected_industries = normalizeIndustries(expectedIndustries);
  cfg.current_functions = normalizeIndustries(currentFunctions);
  cfg.expected_functions = normalizeIndustries(expectedFunctions);
  const jobLabel = document.getElementById("selected_chat_job").value;
  cfg.selected_chat_job = cfg.platform === "liepin"
    ? ((state.jobs || []).find(job => formatJobLabel(job) === jobLabel) || null)
    : null;
  return {
    id: activeTaskId || undefined,
    name: document.getElementById("taskName").value.trim() || "未命名任务",
    enabled: document.getElementById("enabled").checked,
    times: scheduleTimes,
    config: cfg
  };
}
function normalizeIndustries(values) {
  const raw = Array.isArray(values) ? values : String(values || "").split(/[，,、;；]/);
  const result = [];
  for (const item of raw) {
    const value = String(item || "").trim();
    if (value && !result.includes(value)) result.push(value);
  }
  return result.slice(0, 5);
}
function renderOptionViews() {
  renderOptionView("current_industries", currentIndustries, "industry");
  renderOptionView("expected_industries", expectedIndustries, "industry");
  renderOptionView("current_functions", currentFunctions, "function");
  renderOptionView("expected_functions", expectedFunctions, "function");
}
function renderOptionView(id, values, kind) {
  const box = document.getElementById(`${id}_view`);
  if (!box) return;
  box.innerHTML = values.length
    ? values.map(value => `<span class="pick-chip">${escapeHtml(value)}<button type="button" title="删除" onclick="removePickedOption('${kind}', '${id.startsWith("current") ? "current" : "expected"}', '${escapeJs(value)}')">×</button></span>`).join("")
    : `<span class="hint">不设置</span>`;
}
function escapeJs(value) {
  return String(value ?? "").replace(/\\/g, "\\\\").replace(/'/g, "\\'");
}
function removePickedOption(kind, type, value) {
  if (kind === "industry" && type === "current") currentIndustries = currentIndustries.filter(item => item !== value);
  else if (kind === "industry") expectedIndustries = expectedIndustries.filter(item => item !== value);
  else if (type === "current") currentFunctions = currentFunctions.filter(item => item !== value);
  else expectedFunctions = expectedFunctions.filter(item => item !== value);
  renderOptionViews();
}
function openIndustryPicker(type) {
  openOptionPicker("industry", type);
}
function openFunctionPicker(type) {
  openOptionPicker("function", type);
}
function openOptionPicker(kind, type) {
  optionPickerKind = kind;
  optionPickerTarget = type;
  optionPickerDraft = normalizeIndustries(
    kind === "industry"
      ? (type === "current" ? currentIndustries : expectedIndustries)
      : (type === "current" ? currentFunctions : expectedFunctions)
  );
  const categories = Object.keys(getOptionGroups(kind));
  optionPickerCategory = categories[0] || "";
  document.getElementById("industryPickerTitle").textContent = type === "current" ? "请选择当前行业" : "请选择期望行业";
  if (kind === "function") {
    document.getElementById("industryPickerTitle").textContent = type === "current" ? "请选择当前职能" : "请选择期望职能";
  }
  document.getElementById("industryPicker").classList.add("open");
  renderOptionPicker();
}
function closeIndustryPicker(event) {
  if (event && event.target && event.target.id !== "industryPicker") return;
  document.getElementById("industryPicker").classList.remove("open");
}
function getOptionGroups(kind) {
  return kind === "function" ? (state.function_groups || {}) : (state.industry_groups || {});
}
function renderIndustryPicker() {
  renderOptionPicker();
}
function renderOptionPicker() {
  const groups = getOptionGroups(optionPickerKind);
  const categories = Object.keys(groups);
  if (!optionPickerCategory && categories.length) optionPickerCategory = categories[0];
  document.getElementById("industryCats").innerHTML = categories.map(category => (
    `<button type="button" class="industry-cat ${category === optionPickerCategory ? "active" : ""}" onclick="selectIndustryCategory('${escapeJs(category)}')">${escapeHtml(category)}</button>`
  )).join("");
  const values = groups[optionPickerCategory] || [];
  document.getElementById("industryTags").innerHTML = values.map(value => {
    const selected = optionPickerDraft.includes(value);
    return `<button type="button" class="industry-tag ${selected ? "selected" : ""}" onclick="toggleIndustry('${escapeJs(value)}')">${escapeHtml(value)}</button>`;
  }).join("");
  document.getElementById("industrySelected").innerHTML = `
    <span class="muted">已选（${optionPickerDraft.length}/5）</span>
    ${optionPickerDraft.map(value => `<span class="pick-chip">${escapeHtml(value)}<button type="button" title="删除" onclick="toggleIndustry('${escapeJs(value)}')">×</button></span>`).join("")}
  `;
}
function selectIndustryCategory(category) {
  optionPickerCategory = category;
  renderOptionPicker();
}
function toggleIndustry(value) {
  if (optionPickerDraft.includes(value)) {
    optionPickerDraft = optionPickerDraft.filter(item => item !== value);
  } else if (optionPickerDraft.length < 5) {
    optionPickerDraft.push(value);
  }
  renderOptionPicker();
}
function confirmIndustryPicker() {
  if (optionPickerKind === "industry" && optionPickerTarget === "current") currentIndustries = normalizeIndustries(optionPickerDraft);
  else if (optionPickerKind === "industry") expectedIndustries = normalizeIndustries(optionPickerDraft);
  else if (optionPickerTarget === "current") currentFunctions = normalizeIndustries(optionPickerDraft);
  else expectedFunctions = normalizeIndustries(optionPickerDraft);
  renderOptionViews();
  closeIndustryPicker();
}
function normalizeTimes(values) {
  const raw = Array.isArray(values) ? values : String(values || "").split(/[，,;；]/);
  const result = [];
  for (const item of raw) {
    const value = String(item || "").trim();
    if (!/^\d{1,2}:\d{2}$/.test(value)) continue;
    const [h, m] = value.split(":").map(Number);
    if (h < 0 || h > 23 || m < 0 || m > 59) continue;
    const normalized = `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
    if (!result.includes(normalized)) result.push(normalized);
  }
  return result.sort();
}
function renderScheduleTimes() {
  const box = document.getElementById("timeList");
  box.innerHTML = scheduleTimes.length
    ? scheduleTimes.map(time => `<span class="time-chip">${escapeHtml(time)}<button type="button" title="删除" onclick="removeScheduleTime('${time}')">×</button></span>`).join("")
    : `<span class="hint">还没有添加运行时间</span>`;
}
function addScheduleTime() {
  const input = document.getElementById("timePicker");
  const value = normalizeTimes([input.value])[0];
  if (!value) return;
  if (!scheduleTimes.includes(value)) scheduleTimes.push(value);
  scheduleTimes = normalizeTimes(scheduleTimes);
  input.value = "";
  renderScheduleTimes();
}
function removeScheduleTime(time) {
  scheduleTimes = scheduleTimes.filter(item => item !== time);
  renderScheduleTimes();
}
function formatJobLabel(job) {
  return job.label || [job.title, job.city, job.salary].filter(Boolean).join(" | ");
}
async function selectTask(id) {
  activeTaskId = id;
  await api("/api/tasks/active", {id});
  await loadState(false);
}
async function newTask() {
  const payload = readForm();
  payload.id = undefined;
  payload.name = "新配置 " + new Date().toLocaleTimeString("zh-CN", {hour12:false}).slice(0,5);
  payload.enabled = false;
  const res = await api("/api/tasks/save", payload);
  activeTaskId = res.task.id;
  await loadState(false);
}
async function deleteTask() {
  if (!activeTaskId || !confirm("确定删除当前配置？")) return;
  await api("/api/tasks/delete", {id: activeTaskId});
  await loadState(false);
}
async function saveTask() {
  const res = await api("/api/tasks/save", readForm());
  activeTaskId = res.task.id;
  await loadState(false);
}
async function runTask() {
  await saveTask();
  await api("/api/tasks/run", {id: activeTaskId});
  setTimeout(() => loadState(true), 500);
}
async function stopTask() {
  const button = document.getElementById("stopTaskButton");
  if (button) {
    button.disabled = true;
    button.textContent = "正在停止";
  }
  await api("/api/tasks/stop", {});
  const startedAt = Date.now();
  const timer = setInterval(async () => {
    await loadState(true);
    if (!state.running || Date.now() - startedAt > 30000) clearInterval(timer);
  }, 1000);
}
async function onPlatformChange() {
  applyPlatformUI();
  await openPlatform();
}
async function openPlatform() {
  await saveTask();
  const port = Number(document.getElementById("port").value || 9225);
  await api("/api/platform/open", {port, platform: currentPlatform()});
  setTimeout(() => loadState(true), 500);
}
async function refreshJobs() {
  const port = Number(document.getElementById("port").value || 9225);
  await api("/api/jobs/refresh", {port, platform: currentPlatform()});
  if (refreshJobsTimer) clearInterval(refreshJobsTimer);
  const startedAt = Date.now();
  refreshJobsTimer = setInterval(async () => {
    await loadState(true);
    if (!state.running || Date.now() - startedAt > 60000) {
      clearInterval(refreshJobsTimer);
      refreshJobsTimer = null;
      await loadState(true);
    }
  }, 1000);
}
async function checkUpdate() {
  const result = await api("/api/update/check", {});
  await loadState(true);
  if (!result.ok) {
    alert(result.error || "检查更新失败");
    return;
  }
  if (!result.update_available) {
    alert(`当前已是最新版本：${result.current_version}`);
    return;
  }
  if (!result.has_windows_asset) {
    alert("发现新版本，但 Release 里没有 Windows exe 文件。");
    return;
  }
  const yes = confirm(`发现新版本 ${result.latest_version}，当前版本 ${result.current_version}。\n\n是否现在下载并安装？`);
  if (!yes) return;
  const installResult = await api("/api/update/install", {});
  if (!installResult.ok) {
    alert(installResult.error || "安装更新失败");
    return;
  }
  alert("更新已下载，软件将自动退出并重启。");
}
function renderResults() {
  const mapComm = {done:"已确认", sent_no_chat_tab:"已发送", already_communicated:"已沟通", failed:"失败", sent:"已发送", test:"测试"};
  const mapResume = {requested:"已索要", already_requested:"已索要", already_available:"已可看", not_found:"未找到会话", failed:"索要失败"};
  const mapPhone = {requested:"电话已索要", already_requested:"电话已索要", already_available:"电话可查看", clicked:"电话未确认", not_found:"未找到会话", failed:"电话失败"};
  document.getElementById("results").innerHTML = (state.results || []).map(item => `
    <tr>
      <td>${escapeHtml(candidateIndex(item))}</td>
      <td>${escapeHtml(item.name || "")}</td>
      <td>${escapeHtml(candidateSummary(item))}</td>
      <td><span class="tag ${item.match === true ? "ok" : (item.match === false ? "bad" : "mid")}">${item.match === true ? "匹配" : (item.match === false ? "不匹配" : "已提取")}</span></td>
      <td>${escapeHtml(item.score ?? (item.page_number ? `第${item.page_number}页` : 0))}</td>
      <td title="${escapeHtml(candidateActionTitle(item))}">${escapeHtml(candidateAction(item, mapComm, mapPhone, mapResume))}</td>
      <td>
        <div>${escapeHtml(item.reason || "")}</div>
        ${candidateTags(item)}
      </td>
    </tr>
  `).join("");
}
function candidateIndex(item) {
  return item.global_candidate_index || item.index || item.page_candidate_index || "";
}
function candidateSummary(item) {
  const liepin = [item.job_position, item.location || item.job_cities].filter(Boolean).join(" / ");
  if (liepin) return liepin;
  return [firstLine(item.basic_info), firstLine(item.expectation)].filter(Boolean).join(" / ");
}
function candidateAction(item, mapComm, mapPhone, mapResume) {
  const action = [mapComm[item.communicate_status] || item.communicate_status || "", mapPhone[item.phone_request_status] || "", mapResume[item.resume_request_status] || ""]
    .filter(Boolean).join(" / ");
  if (action) return action;
  if (item.page_number) return `已提取第${item.page_number}页`;
  if (item.next_action === "communicate") return "建议沟通";
  if (item.next_action === "skip") return "跳过";
  return item.next_action || "";
}
function candidateActionTitle(item) {
  return [item.communicate_note, item.phone_request_note, item.resume_request_note, listText("优势", item.strengths), listText("风险", item.risks)]
    .filter(Boolean).join("；");
}
function candidateTags(item) {
  const parts = [listText("优势", item.strengths), listText("风险", item.risks)].filter(Boolean);
  return parts.length ? `<div class="result-detail">${parts.map(escapeHtml).join("；")}</div>` : "";
}
function listText(label, values) {
  return Array.isArray(values) && values.length ? `${label}：${values.join("、")}` : "";
}
function firstLine(value) {
  return String(value || "").split(/\n/).map(x => x.trim()).find(Boolean) || "";
}
function renderLogs() {
  const box = document.getElementById("logs");
  box.innerHTML = (state.logs || []).map(log => `<div class="log-line"><span class="muted">${escapeHtml(log.time)}</span> ${escapeHtml(log.message)}</div>`).join("");
  box.scrollTop = box.scrollHeight;
}
setInterval(() => loadState(true), 2500);
loadState(false);
