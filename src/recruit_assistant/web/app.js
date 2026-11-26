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
let updatePollTimer = null;
let notificationQueue = [];
let notificationOpen = false;
let customMatchRequirements = "";
let templateDraftId = "";
const handledNotificationIds = new Set();
const fieldIds = [
  "platform",
  "keywords", "job_name", "company_name", "current_city", "expected_city",
  "maimai_keywords", "maimai_keyword_mode", "maimai_city", "maimai_education", "maimai_experience", "maimai_graduation_year",
  "maimai_company", "maimai_gender", "maimai_age_min", "maimai_age_max", "maimai_greeting",
  "experience", "recruitment_type", "active_status", "job_status",
  "job_hop_frequency", "age_min", "age_max", "gender_requirement", "language_requirement",
  "graduation_year", "ai_provider", "deepseek_api_key", "company_model_api_key", "deepseek_model",
  "candidate_limit", "maimai_page_limit", "maimai_auto_communicate", "ai_template_id", "match_requirements",
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
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || "操作失败");
  return data;
}
async function loadState(keepForm = false) {
  const res = await fetch("/api/state");
  state = await res.json();
  processNotifications();
  activeTaskId = state.active_task_id || (state.tasks[0] && state.tasks[0].id) || "";
  renderTasks();
  renderResults();
  renderLogs();
  renderUpdateProgress(state.update_status || {});
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
  const count = document.getElementById("taskCount");
  if (count) count.textContent = String((state.tasks || []).length);
  box.innerHTML = (state.tasks || []).map(task => `
    <div class="task ${task.id === activeTaskId ? "active" : ""}" onclick="selectTask('${escapeJs(task.id)}')">
      <div class="task-topline"><div class="task-name">${escapeHtml(task.name)}</div><span class="task-dot ${task.enabled ? "on" : ""}" title="${task.enabled ? "已启用定时" : "未启用定时"}"></span></div>
      <div class="task-meta">
        <span class="task-platform">${escapeHtml(platformMeta((task.config || {}).platform).name)}</span>
        · ${task.enabled ? "已启用" : "未启用"} · ${(task.times || []).join(", ") || "未设时间"}<br>
        <span class="task-template">${escapeHtml(templateName(task))}</span>
      </div>
      <div class="task-actions">
        <button type="button" class="task-action" onclick="event.stopPropagation(); renameTask('${escapeJs(task.id)}')">重命名</button>
        <button type="button" class="task-action" onclick="event.stopPropagation(); duplicateTask('${escapeJs(task.id)}')">复制</button>
        <button type="button" class="task-action" onclick="event.stopPropagation(); toggleTask('${escapeJs(task.id)}', ${!task.enabled})">${task.enabled ? "停用" : "启用"}</button>
      </div>
    </div>
  `).join("");
}
function templateName(task) {
  const id = task?.config?.ai_template_id;
  const template = (state.ai_templates || []).find(item => item.id === id);
  return template ? template.name : "本任务自定义提示词";
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
  applyAiProviderUI(false);
}
function applyAiProviderUI(resetModel = false) {
  const provider = document.getElementById("ai_provider")?.value || "company";
  const model = document.getElementById("deepseek_model");
  const useCompany = provider === "company";
  setPanelVisible("companyModelKeyPanel", useCompany);
  setPanelVisible("deepseekKeyPanel", !useCompany);
  const label = document.getElementById("aiModelLabel");
  if (label) label.textContent = useCompany ? "公司模型" : "DeepSeek 模型";
  if (!model) return;
  if (resetModel) {
    model.value = useCompany ? "minimax" : "deepseek-chat";
  } else if (!model.value || (useCompany && model.value.startsWith("deepseek-")) || (!useCompany && model.value === "minimax")) {
    model.value = useCompany ? "minimax" : "deepseek-chat";
  }
}
function renderJobSelect(cfg = null) {
  const jobSelect = document.getElementById("selected_chat_job");
  if (!jobSelect) return;
  const savedJob = cfg === null
    ? JSON.parse(jobSelect.selectedOptions[0]?.dataset.job || "null")
    : cfg.selected_chat_job || null;
  const selectedJobKey = cfg === null ? jobSelect.value : jobKey(savedJob);
  const jobs = [...(state.jobs || [])];
  if (savedJob && !jobs.some(job => jobKey(job) === selectedJobKey)) jobs.unshift(savedJob);
  jobSelect.innerHTML = `<option value="">自动选择第一个职位</option>` + jobs.map(job => {
    const key = jobKey(job);
    const label = formatJobLabel(job);
    const suffix = savedJob === job && !(state.jobs || []).some(item => jobKey(item) === key) ? "（已保存）" : "";
    return `<option value="${escapeHtml(key)}" data-job="${escapeHtml(JSON.stringify(job))}"${key === selectedJobKey ? " selected" : ""}>${escapeHtml(label + suffix)}</option>`;
  }).join("");
}
function fillForm(task) {
  if (!task) return;
  const cfg = {...state.defaults, ...(task.config || {})};
  document.getElementById("enabled").checked = !!task.enabled;
  scheduleTimes = normalizeTimes(task.times || []);
  renderScheduleTimes();
  for (const id of Object.keys(state.options || {})) fillSelect(id, cfg[id]);
  customMatchRequirements = cfg.ai_template_id ? "" : (cfg.match_requirements || "").trim();
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
  renderTemplateSelect(cfg);
  applyTemplateUI();
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
  cfg.platform = currentPlatform();
  cfg.ai_template_id = document.getElementById("ai_template_id")?.value || "";
  if (cfg.ai_template_id) {
    const template = (state.ai_templates || []).find(item => item.id === cfg.ai_template_id);
    if (template) cfg.match_requirements = template.prompt;
  } else {
    cfg.match_requirements = document.getElementById("match_requirements")?.value || customMatchRequirements || state.defaults.match_requirements;
  }
  cfg.candidate_limit = Number(cfg.candidate_limit || 1);
  cfg.maimai_page_limit = Number(cfg.maimai_page_limit || 1);
  cfg.school_types = Array.from(document.querySelectorAll("#school_types input:checked")).map(x => x.value);
  cfg.education = Array.from(document.querySelectorAll("#education input:checked")).map(x => x.value);
  cfg.current_industries = normalizeIndustries(currentIndustries);
  cfg.expected_industries = normalizeIndustries(expectedIndustries);
  cfg.current_functions = normalizeIndustries(currentFunctions);
  cfg.expected_functions = normalizeIndustries(expectedFunctions);
  const selectedJobKey = document.getElementById("selected_chat_job").value;
  const savedJob = JSON.parse(document.getElementById("selected_chat_job").selectedOptions[0]?.dataset.job || "null");
  cfg.selected_chat_job = cfg.platform === "liepin"
    ? ((state.jobs || []).find(job => jobKey(job) === selectedJobKey)
      || (savedJob && jobKey(savedJob) === selectedJobKey ? savedJob : null))
    : null;
  return {
    id: activeTaskId || undefined,
    name: activeTask()?.name || "未命名任务",
    enabled: document.getElementById("enabled").checked,
    times: scheduleTimes,
    config: cfg
  };
}
function renderTemplateSelect(cfg = {}) {
  const select = document.getElementById("ai_template_id");
  if (!select) return;
  const selected = cfg.ai_template_id || "";
  select.innerHTML = `<option value="">本任务自定义提示词</option>` + (state.ai_templates || []).map(item =>
    `<option value="${escapeHtml(item.id)}"${item.id === selected ? " selected" : ""}>${escapeHtml(item.name)}</option>`
  ).join("");
}
function applyTemplateUI() {
  const select = document.getElementById("ai_template_id");
  const prompt = document.getElementById("match_requirements");
  const hint = document.getElementById("templateHint");
  if (!select || !prompt) return;
  const template = (state.ai_templates || []).find(item => item.id === select.value);
  prompt.disabled = false;
  if (template) {
    prompt.value = template.prompt || "";
    if (hint) hint.textContent = `${template.description || "已绑定模板"} 修改本框后会转为本任务自定义，不影响共享模板。`;
  } else {
    prompt.value = customMatchRequirements || "";
    if (hint) hint.textContent = "当前任务独立使用下面的提示词，不会影响其他任务。";
  }
}
function onPromptInput() {
  const select = document.getElementById("ai_template_id");
  const prompt = document.getElementById("match_requirements");
  if (!select || !prompt) return;
  if (select.value) {
    customMatchRequirements = prompt.value;
    select.value = "";
    applyTemplateUI();
  } else {
    customMatchRequirements = prompt.value;
  }
}
function onTemplateChange() {
  const prompt = document.getElementById("match_requirements");
  if (prompt && !prompt.disabled && prompt.value.trim() && document.getElementById("ai_template_id")?.value) {
    customMatchRequirements = prompt.value.trim();
  }
  applyTemplateUI();
}
function openTemplateManager() {
  document.getElementById("templateManager").classList.add("open");
  const selected = document.getElementById("ai_template_id")?.value;
  templateDraftId = selected || (state.ai_templates?.[0]?.id || "");
  renderTemplateManager();
}
function closeTemplateManager(event) {
  if (event && event.target && event.target.id !== "templateManager") return;
  document.getElementById("templateManager").classList.remove("open");
}
function renderTemplateManager() {
  const list = document.getElementById("templateManagerList");
  if (!list) return;
  list.innerHTML = (state.ai_templates || []).map(item => `
    <button type="button" class="template-list-item ${item.id === templateDraftId ? "active" : ""}" onclick="selectTemplateDraft('${escapeJs(item.id)}')">
      <strong>${escapeHtml(item.name)}</strong><span>${escapeHtml(item.description || "暂无用途说明")}</span>
    </button>`).join("") || `<div class="hint">还没有模板，请新建一个。</div>`;
  const item = (state.ai_templates || []).find(value => value.id === templateDraftId);
  document.getElementById("templateName").value = item?.name || "";
  document.getElementById("templateDescription").value = item?.description || "";
  document.getElementById("templatePrompt").value = item?.prompt || "";
  const hint = document.getElementById("templateEditorHint");
  if (hint) hint.textContent = item ? `${templateUsageCount(item.id)} 套任务正在使用此模板。更新后立即生效。` : "新模板保存后即可绑定到任务。";
}
function templateUsageCount(id) {
  return `${(state.tasks || []).filter(task => task.config?.ai_template_id === id).length}`;
}
function selectTemplateDraft(id) {
  templateDraftId = id;
  renderTemplateManager();
}
function newTemplateDraft() {
  templateDraftId = "";
  document.getElementById("templateName").value = "";
  document.getElementById("templateDescription").value = "";
  document.getElementById("templatePrompt").value = "";
  document.getElementById("templateEditorHint").textContent = "新模板保存后即可绑定到任务。";
  document.querySelectorAll(".template-list-item").forEach(item => item.classList.remove("active"));
}
async function saveTemplateDraft() {
  const name = document.getElementById("templateName").value.trim();
  const prompt = document.getElementById("templatePrompt").value.trim();
  if (!name || !prompt) { alert("请填写模板名称和提示词。\n\n提示词至少要包含你的筛选标准和通过条件。"); return; }
  const selectedTemplateId = document.getElementById("ai_template_id")?.value || "";
  const result = await api("/api/ai-templates/save", {id: templateDraftId || undefined, name, description: document.getElementById("templateDescription").value.trim(), prompt});
  templateDraftId = result.template.id;
  await loadState(true);
  renderTemplateManager();
  renderTemplateSelect({ai_template_id: selectedTemplateId});
  applyTemplateUI();
  alert("模板已保存。已绑定该模板的任务会在下次运行时使用最新提示词。");
}
async function deleteTemplateDraft() {
  const item = (state.ai_templates || []).find(value => value.id === templateDraftId);
  if (!item) return;
  if (!confirm(`确定删除模板“${item.name}”？`)) return;
  await api("/api/ai-templates/delete", {id: item.id});
  templateDraftId = state.ai_templates.find(value => value.id !== item.id)?.id || "";
  await loadState(true);
  renderTemplateSelect(activeTask()?.config || {});
  applyTemplateUI();
  renderTemplateManager();
}
async function renameTask(id) {
  const task = (state.tasks || []).find(item => item.id === id);
  if (!task) return;
  const name = prompt("请输入新的配置名称", task.name || "");
  if (name === null || !name.trim()) return;
  await api("/api/tasks/save", {id, name: name.trim(), enabled: task.enabled, times: task.times, config: task.config});
  await loadState(true);
}
async function duplicateTask(id) {
  const result = await api("/api/tasks/duplicate", {id});
  activeTaskId = result.task.id;
  await loadState(false);
}
async function toggleTask(id, enabled) {
  await api("/api/tasks/toggle", {id, enabled});
  await loadState(true);
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
function jobKey(job) {
  if (!job) return "";
  if (job.job_id) return `id:${job.job_id}`;
  if (job.href) return `href:${job.href}`;
  return `label:${formatJobLabel(job)}`;
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
function processNotifications() {
  for (const notification of (state.notifications || [])) {
    const id = String(notification.id ?? "");
    if (!id || handledNotificationIds.has(id)) continue;
    handledNotificationIds.add(id);
    notificationQueue.push(notification);
  }
  while (handledNotificationIds.size > 200) {
    handledNotificationIds.delete(handledNotificationIds.values().next().value);
  }
  showNextNotification();
}
function showNextNotification() {
  if (notificationOpen || !notificationQueue.length) return;
  const notification = notificationQueue.shift();
  const overlay = document.getElementById("notificationOverlay");
  const modal = document.getElementById("notificationModal");
  if (!overlay || !modal) return;
  document.getElementById("notificationTitle").textContent = notification.title || "任务状态";
  document.getElementById("notificationMessage").textContent = notification.message || "操作已结束。";
  document.getElementById("notificationTime").textContent = notification.time ? `时间：${notification.time}` : "";
  modal.className = `notification-modal ${["success", "warning", "error"].includes(notification.level) ? notification.level : "info"}`;
  overlay.classList.add("open");
  notificationOpen = true;
}
function closeNotification(event) {
  if (event && event.target && event.target.id !== "notificationOverlay") return;
  const overlay = document.getElementById("notificationOverlay");
  if (!overlay) return;
  overlay.classList.remove("open");
  notificationOpen = false;
  setTimeout(showNextNotification, 0);
}
async function onPlatformChange() {
  applyPlatformUI();
  await openPlatform();
}
async function openPlatform() {
  await saveTask();
  await api("/api/platform/open", {platform: currentPlatform()});
  setTimeout(() => loadState(true), 500);
}
async function refreshJobs() {
  await api("/api/jobs/refresh", {platform: currentPlatform()});
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
  renderUpdateProgress(installResult.status || {phase: "checking", message: "正在准备更新", percent: 0});
  startUpdatePolling();
}
function startUpdatePolling() {
  if (updatePollTimer) clearInterval(updatePollTimer);
  updatePollTimer = setInterval(async () => {
    try {
      const result = await fetch("/api/update/status").then(res => res.json());
      const status = result.status || {};
      renderUpdateProgress(status);
      if (!["checking", "downloading", "installing"].includes(status.phase)) {
        clearInterval(updatePollTimer);
        updatePollTimer = null;
      }
    } catch (err) {
      clearInterval(updatePollTimer);
      updatePollTimer = null;
    }
  }, 500);
}
function renderUpdateProgress(status) {
  const box = document.getElementById("updateProgress");
  const fill = document.getElementById("updateProgressFill");
  const text = document.getElementById("updateProgressText");
  if (!box || !fill || !text) return;
  const phase = status.phase || "idle";
  const visible = ["checking", "downloading", "installing", "error"].includes(phase);
  box.classList.toggle("hidden", !visible);
  const percent = Math.max(0, Math.min(100, Number(status.percent || 0)));
  fill.style.width = `${percent}%`;
  if (phase === "checking") text.textContent = status.message || "正在检查更新";
  else if (phase === "downloading") text.textContent = status.message || `正在下载更新：${percent}%`;
  else if (phase === "installing") text.textContent = status.message || "下载完成，正在安装";
  else if (phase === "error") text.textContent = status.message || "更新失败";
  else text.textContent = "";
}
function renderResults() {
  const mapComm = {done:"已确认", sent_no_chat_tab:"已发送", already_communicated:"已沟通", failed:"失败", sent:"已发送", test:"测试", skipped:"未启用", skipped_limit:"超过上限", skipped_ai:"AI 跳过", not_matched:"AI 不匹配", unconfirmed:"未确认"};
  const mapResume = {requested:"已索要", already_requested:"已索要", already_available:"已可看", not_found:"未找到会话", failed:"索要失败", not_started:"未触发"};
  const mapPhone = {requested:"电话已索要", already_requested:"电话已索要", already_available:"电话可查看", clicked:"电话未确认", not_found:"未找到会话", failed:"电话失败", unavailable:"未找到电话按钮", unconfirmed:"电话申请未确认", not_started:"未触发"};
  document.getElementById("results").innerHTML = (state.results || []).map(item => `
    <tr>
      <td>${escapeHtml(candidateIndex(item))}</td>
      <td>${escapeHtml(item.name || "")}</td>
      <td>${escapeHtml(candidateSummary(item))}</td>
      <td><span class="tag ${item.match === true ? "ok" : (item.match === false ? "bad" : "mid")}">${item.decision === "unread" ? "未读取" : item.match === true ? "匹配" : (item.match === false ? "不匹配" : "已提取")}</span></td>
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
  if (item.decision === "unread") return "已跳过";
  const action = [mapComm[item.communicate_status] || item.communicate_status || "", mapPhone[item.phone_request_status] || "", mapResume[item.resume_request_status] || ""]
    .filter(Boolean).join(" / ");
  if (action) return action;
  if (item.page_number) return `已提取第${item.page_number}页`;
  if (item.next_action === "communicate") return "建议沟通";
  if (item.next_action === "skip") return "跳过";
  return item.next_action || "";
}
function candidateActionTitle(item) {
  return [item.communicate_note, item.phone_request_note, item.resume_request_note, communicationTraceText(item.communication_trace), listText("优势", item.strengths), listText("风险", item.risks)]
    .filter(Boolean).join("；");
}
function communicationTraceText(trace) {
  if (!Array.isArray(trace) || !trace.length) return "";
  return trace.map(item => [item.stage, item.status, item.message].filter(Boolean).join("：")).join("；");
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
