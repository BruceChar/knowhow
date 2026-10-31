const state = {
  user: null,
  dashboard: null,
  knowledge: [],
  gaps: [],
  team: [],
  roles: [],
  teams: [],
  lastInteraction: null,
};

const pageMeta = {
  today: ["TODAY", "今日工作台"],
  ask: ["SALES COPILOT", "销售工作台"],
  knowledge: ["COMPANY BRAIN", "公司知识"],
  review: ["REVIEW INBOX", "待处理"],
  team: ["TEAM", "团队"],
};

const kindLabels = {
  faq: "标准问答",
  product: "产品知识",
  policy: "公司规则",
  playbook: "场景手册",
  case: "客户案例",
  sop: "内部 SOP",
};

const statusLabels = {
  draft: "待审核",
  published: "已发布",
  archived: "已归档",
  open: "待处理",
  resolved: "已解决",
  ignored: "已忽略",
};

const roleLabels = {
  owner: "企业负责人",
  steward: "知识负责人",
  sales: "业务员",
  admin: "管理员",
};

const permissionLabels = {
  "ask.use": "使用销售问答",
  "knowledge.view": "查看知识",
  "knowledge.suggest": "提交知识草稿",
  "knowledge.edit": "编辑知识",
  "knowledge.publish": "发布知识",
  "gap.manage": "处理知识缺口",
  "dashboard.view": "查看经营看板",
  "team.manage": "管理团队成员",
  "role.manage": "管理角色权限",
  "data.all": "查看全部团队数据",
};

const $ = (selector, root = document) => root.querySelector(selector);
const $$ = (selector, root = document) => [...root.querySelectorAll(selector)];

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[char]));
}

async function api(path, options = {}) {
  const headers = {
    "Content-Type": "application/json",
    "X-Knowhow-CSRF": "1",
    ...(options.headers || {}),
  };
  const response = await fetch(path, { ...options, headers });
  if (response.status === 401) {
    showLogin();
    throw new Error("登录已过期");
  }
  if (!response.ok) {
    let detail = `请求失败 (${response.status})`;
    try {
      const payload = await response.json();
      detail = payload.detail || detail;
    } catch (_) {}
    throw new Error(detail);
  }
  return response.json();
}

function showToast(message, error = false) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.className = `toast show${error ? " error" : ""}`;
  clearTimeout(showToast.timer);
  showToast.timer = setTimeout(() => { toast.className = "toast"; }, 3500);
}

async function boot() {
  const bootstrap = await api("/api/bootstrap");
  if (bootstrap.setup_required) {
    $("#setup-view").hidden = false;
    return;
  }
  try {
    const result = await api("/api/auth/me");
    enterApp(result.user);
  } catch (_) {
    showLogin();
  }
}

function showLogin() {
  $("#setup-view").hidden = true;
  $("#app-view").hidden = true;
  $("#login-view").hidden = false;
}

function enterApp(user) {
  state.user = user;
  $("#setup-view").hidden = true;
  $("#login-view").hidden = true;
  $("#app-view").hidden = false;
  $("#org-name").textContent = user.organization_name || "";
  $("#user-label").textContent = `${user.display_name} · ${roleLabels[user.role] || user.role}`;
  configureNavigation();
  const firstPage = (user.permissions || []).includes("dashboard.view") ? "today" : "ask";
  showPage(firstPage);
  if (user.must_change_password) {
    showToast("这是初始密码，请尽快修改", true);
  }
}

function configureNavigation() {
  $('[data-page="today"]').hidden = !hasPermission("dashboard.view");
  $('[data-page="team"]').hidden = !hasPermission("team.manage");
  $("#role-panel").hidden = !hasPermission("role.manage");
}

function hasPermission(code) {
  return (state.user?.permissions || []).includes(code);
}

function scopeLabel(scope, teamName) {
  if (scope === "private") return "仅自己可见";
  if (scope === "team") return `团队可见 · ${teamName || "未指定团队"}`;
  return "全公司可见";
}

async function showPage(name) {
  const [eyebrow, title] = pageMeta[name] || ["", name];
  $("#page-eyebrow").textContent = eyebrow;
  $("#page-title").textContent = title;
  $$(".nav button").forEach(button => button.classList.toggle("active", button.dataset.page === name));
  $$(".page").forEach(page => page.classList.toggle("active", page.id === `page-${name}`));
  $(".sidebar").classList.remove("open");
  if (name === "today") await loadDashboard();
  if (name === "knowledge") await loadKnowledge();
  if (name === "review") await loadGaps();
  if (name === "team") await loadTeam();
}

async function loadDashboard() {
  try {
    const data = await api("/api/dashboard");
    state.dashboard = data;
    renderDashboard(data);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderDashboard(data) {
  const metrics = data.metrics;
  $("#readiness-value").textContent = `${metrics.readiness}%`;
  $("#metric-grid").innerHTML = [
    [metrics.published, "已发布知识"],
    [metrics.weekly_interactions, "本周问答"],
    [metrics.open_gaps, "待处理缺口"],
    [`${metrics.draft_acceptance}%`, "草稿低修改采用"],
    [metrics.expiring, "14 天内到期"],
  ].map(([value, label]) => `
    <div class="metric-card"><strong>${esc(value)}</strong><span>${esc(label)}</span></div>
  `).join("");
  $("#action-list").innerHTML = data.actions.map(action => `
    <div class="action-item">
      <div><strong>${esc(action.title)}</strong><p>${esc(action.detail)}</p></div>
      <button class="text-button page-link" data-target="${esc(action.target)}">去处理</button>
    </div>
  `).join("");
  $("#checklist").innerHTML = data.checklist.map(item => `
    <div class="check-item ${item.done ? "done" : ""}">
      <span class="check-dot">${item.done ? "✓" : "·"}</span>
      <span>${esc(item.label)}</span>
    </div>
  `).join("");
  $("#recent-knowledge").innerHTML = data.recent_knowledge.map(item => `
    <div class="knowledge-mini">
      <strong>${esc(item.title)}</strong>
      <span>${esc(kindLabels[item.kind] || item.kind)} · ${esc(statusLabels[item.status] || item.status)}</span>
    </div>
  `).join("") || '<p class="muted small">还没有知识，先创建或导入第一批内容。</p>';
  $$(".page-link").forEach(button => button.addEventListener("click", () => showPage(button.dataset.target)));
}

async function ask() {
  const question = $("#question-input").value.trim();
  if (!question) {
    showToast("请先输入客户问题", true);
    return;
  }
  const button = $("#ask-button");
  button.disabled = true;
  try {
    const data = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question, channel: "web" }),
    });
    state.lastInteraction = data;
    renderAnswer(data);
  } catch (error) {
    showToast(error.message, true);
  } finally {
    button.disabled = false;
  }
}

function renderAnswer(data) {
  const panel = $("#answer-panel");
  panel.classList.remove("empty-state");
  const statusText = data.status === "escalated" ? "需要人工确认" : "AI 草稿";
  const riskClass = data.risk === "high" ? "risk" : "";
  panel.innerHTML = `
    <div class="answer-head">
      <div class="button-row">
        <span class="answer-badge ${riskClass}">${esc(statusText)}</span>
        <span class="pill">${esc(data.reason)}</span>
      </div>
      <span class="muted small">交互 #${esc(data.interaction_id)} · ${esc(data.model)}</span>
    </div>
    <textarea class="answer-editor" id="final-answer" placeholder="输入最终要发给客户的回复">${esc(data.answer)}</textarea>
    <div class="citation-list">
      ${data.citations.map(item => `<span class="citation">#${item.id} ${esc(item.title)}</span>`).join("") || '<span class="muted small">当前没有可引用知识</span>'}
    </div>
    <div class="answer-actions">
      <button class="button ghost" id="save-copy-button">复制并记录</button>
      <button class="button primary" id="mark-sent-button">标记已发送</button>
    </div>
  `;
  $("#save-copy-button").addEventListener("click", () => saveInteraction("copied", true));
  $("#mark-sent-button").addEventListener("click", () => saveInteraction("sent", false));
}

async function saveInteraction(status, copy) {
  if (!state.lastInteraction) return;
  const answer = $("#final-answer").value.trim();
  if (status === "sent" && !answer) {
    showToast("标记已发送前请填写最终回复", true);
    return;
  }
  try {
    if (copy) {
      await navigator.clipboard.writeText(answer);
    }
    await api(`/api/interactions/${state.lastInteraction.interaction_id}`, {
      method: "PUT",
      body: JSON.stringify({ final_answer: answer, status }),
    });
    showToast(status === "sent" ? "已记录最终回复和发送状态" : "已复制并记录");
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadKnowledge() {
  const params = new URLSearchParams();
  const status = $("#knowledge-status").value;
  const kind = $("#knowledge-kind").value;
  const query = $("#knowledge-search").value.trim();
  if (status) params.set("status", status);
  if (kind) params.set("kind", kind);
  if (query) params.set("q", query);
  try {
    const data = await api(`/api/knowledge?${params}`);
    state.knowledge = data.items;
    renderKnowledge();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderKnowledge() {
  const canPublish = hasPermission("knowledge.publish");
  $("#knowledge-list").innerHTML = state.knowledge.map(item => `
    <article class="knowledge-item">
      <div class="kind-label">${esc(kindLabels[item.kind] || item.kind)}</div>
      <div>
        <h4>${esc(item.title)}</h4>
        <p>${esc(item.answer).slice(0, 180)}</p>
        <p>版本 ${item.version} · ${esc(item.owner_name || "未指定负责人")} · ${esc(scopeLabel(item.scope, item.team_name))} ${item.valid_until ? `· 有效期至 ${esc(item.valid_until)}` : ""}</p>
      </div>
      <div>
        <span class="pill status-${esc(item.status)}">${esc(statusLabels[item.status] || item.status)}</span>
        ${canPublish && item.status !== "published" ? `<button class="text-button publish-knowledge" data-id="${item.id}">发布</button>` : ""}
        ${canPublish && item.status === "published" ? `<button class="text-button archive-knowledge" data-id="${item.id}">归档</button>` : ""}
      </div>
    </article>
  `).join("") || '<p class="muted">还没有知识。先新建一条标准问答，或批量导入。</p>';
  $$(".publish-knowledge").forEach(button => button.addEventListener("click", async () => {
    try {
      await api(`/api/knowledge/${button.dataset.id}/publish`, { method: "POST" });
      showToast("知识已发布");
      await loadKnowledge();
    } catch (error) {
      showToast(error.message, true);
    }
  }));
  $$(".archive-knowledge").forEach(button => button.addEventListener("click", async () => {
    const confirmed = await openConfirm("归档知识", "归档后新回答不再使用这条知识，是否继续？", "确认归档");
    if (!confirmed) return;
    try {
      await api(`/api/knowledge/${button.dataset.id}/archive`, { method: "POST" });
      showToast("知识已归档");
      await loadKnowledge();
    } catch (error) {
      showToast(error.message, true);
    }
  }));
}

async function createKnowledge() {
  await ensureTeams();
  const result = await openDialog({
    title: "新建知识",
    description: "先保存为草稿，确认来源和适用边界后再发布。",
    fields: [
      { name: "kind", label: "类型", type: "select", value: "faq", options: Object.entries(kindLabels).map(([value, label]) => ({ value, label })) },
      { name: "title", label: "标题", required: true, placeholder: "例如：定制 Logo 的 MOQ" },
      { name: "question", label: "客户问题", type: "textarea", placeholder: "客户通常会怎么问？" },
      { name: "answer", label: "标准答案", type: "textarea", required: true, placeholder: "必须来自确认过的业务事实。" },
      { name: "risk_level", label: "风险", type: "select", value: "low", options: [{ value: "low", label: "低" }, { value: "medium", label: "中" }, { value: "high", label: "高风险" }] },
      { name: "scope", label: "可见范围", type: "select", value: "organization", options: [{ value: "organization", label: "全公司" }, { value: "team", label: "指定团队" }, { value: "private", label: "仅自己" }] },
      { name: "team_id", label: "团队（选择团队可见时使用）", type: "select", options: state.teams.map(team => ({ value: String(team.id), label: team.name })) },
      { name: "source_ref", label: "来源依据", placeholder: "文件、邮件或负责人确认记录" },
    ],
    submitText: "保存草稿",
  });
  if (!result) return;
  result.team_id = result.scope === "team" ? Number(result.team_id) : null;
  try {
    await api("/api/knowledge", {
      method: "POST",
      body: JSON.stringify({ ...result, valid_until: null, metadata: {} }),
    });
    showToast("知识草稿已保存");
    await loadKnowledge();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function importKnowledge() {
  const result = await openDialog({
    title: "批量导入标准问答",
    description: "使用 Q:/A: 格式，每段之间用 --- 分隔。导入后均为待审核草稿。",
    fields: [{
      name: "content",
      label: "问答内容",
      type: "textarea",
      required: true,
      value: "Q: What is your MOQ?\nA: It depends on the product and customization. Please share the model and target quantity.\n---\nQ: 可以定制包装吗？\nA: 可以。请提供包装设计、数量和目标市场，我们会确认 MOQ 和打样时间。",
    }],
    submitText: "导入草稿",
  });
  if (!result) return;
  try {
    const data = await api("/api/knowledge/import-text", {
      method: "POST",
      body: JSON.stringify(result),
    });
    showToast(`已导入 ${data.count} 条知识草稿`);
    await loadKnowledge();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadGaps() {
  try {
    const data = await api(`/api/gaps?status=${encodeURIComponent($("#gap-status").value)}`);
    state.gaps = data.items;
    renderGaps();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderGaps() {
  const canResolve = hasPermission("gap.manage");
  $("#gap-list").innerHTML = state.gaps.map(gap => `
    <article class="gap-item">
      <div>
        <h4>${esc(gap.question)}</h4>
        <p>${esc(gap.reason)} · ${esc(gap.assignee_name || "未指派")}</p>
      </div>
      <div class="button-row">
        ${canResolve && gap.status === "open" ? `
          <button class="button primary answer-gap" data-id="${gap.id}">补知识</button>
          <button class="button ghost ignore-gap" data-id="${gap.id}">忽略</button>` : `<span class="pill">${esc(statusLabels[gap.status] || gap.status)}</span>`}
      </div>
    </article>
  `).join("") || '<p class="muted">当前没有待处理知识缺口。</p>';
  $$(".answer-gap").forEach(button => button.addEventListener("click", () => answerGap(Number(button.dataset.id))));
  $$(".ignore-gap").forEach(button => button.addEventListener("click", () => ignoreGap(Number(button.dataset.id))));
}

async function answerGap(id) {
  const gap = state.gaps.find(item => item.id === id);
  if (!gap) return;
  const result = await openDialog({
    title: "从知识缺口创建草稿",
    description: gap.question,
    fields: [
      { name: "title", label: "知识标题", required: true, value: gap.question.slice(0, 100) },
      { name: "answer", label: "标准答案", type: "textarea", required: true, placeholder: "只填写已经确认的事实和表达。" },
      { name: "source_ref", label: "来源依据", value: "知识缺口 #" + gap.id },
    ],
    submitText: "创建知识草稿",
  });
  if (!result) return;
  try {
    await api(`/api/gaps/${id}/answer`, {
      method: "POST",
      body: JSON.stringify(result),
    });
    showToast("已解决缺口并创建知识草稿");
    await loadGaps();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function ignoreGap(id) {
  const result = await openDialog({
    title: "忽略知识缺口",
    description: "请记录为什么不需要纳入知识库。",
    fields: [{ name: "note", label: "原因", type: "textarea", required: true, value: "一次性问题，无复用价值" }],
    submitText: "确认忽略",
    danger: true,
  });
  if (!result) return;
  try {
    await api(`/api/gaps/${id}/ignore`, { method: "POST", body: JSON.stringify(result) });
    showToast("知识缺口已忽略");
    await loadGaps();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadTeam() {
  try {
    const requests = [api("/api/team"), api("/api/teams")];
    if (hasPermission("role.manage")) requests.push(api("/api/roles"));
    const [data, teams, roles] = await Promise.all(requests);
    state.team = data.items;
    state.teams = teams.items;
    state.roles = roles?.items || [];
    renderTeam();
    renderRoles();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderTeam() {
  $("#team-list").innerHTML = state.team.map(account => `
    <article class="team-item">
      <div><strong>${esc(account.display_name)}</strong><p class="muted small">${esc(account.username)} · ${esc(account.role_name || roleLabels[account.role] || account.role)} · ${esc((account.team_names || []).join("、") || "未分配团队")}</p></div>
      <div class="button-row">
        <span class="pill">${account.enabled ? "启用" : "停用"}</span>
        <button class="button ghost edit-account-teams" data-id="${account.id}">调整团队</button>
        ${account.role !== "owner" ? `<button class="button ghost toggle-account" data-id="${account.id}" data-enabled="${account.enabled}">${account.enabled ? "停用" : "启用"}</button>` : ""}
      </div>
    </article>
  `).join("");
  $$(".edit-account-teams").forEach(button => button.addEventListener("click", () => editAccountTeams(Number(button.dataset.id))));
  $$(".toggle-account").forEach(button => button.addEventListener("click", async () => {
    const enabled = button.dataset.enabled !== "true";
    if (!enabled) {
      const confirmed = await openConfirm("停用账户", "停用后该成员会立即退出登录，是否继续？", "停用账户");
      if (!confirmed) return;
    }
    try {
      await api(`/api/team/${button.dataset.id}/enabled`, {
        method: "PUT", body: JSON.stringify({ enabled }),
      });
      showToast(enabled ? "账户已启用" : "账户已停用");
      await loadTeam();
    } catch (error) {
      showToast(error.message, true);
    }
  }));
}

async function createAccount() {
  if (!state.roles.length) await loadTeam();
  const result = await openDialog({
    title: "添加团队成员",
    description: "首次登录后建议修改临时密码。",
    fields: [
      { name: "display_name", label: "姓名", required: true },
      { name: "username", label: "登录名", required: true, pattern: "^[a-zA-Z0-9_.-]{3,}$", error: "至少 3 位，仅支持字母、数字、点、横线和下划线" },
      { name: "password", label: "初始密码", inputType: "password", required: true },
      { name: "role_id", label: "角色", type: "select", required: true, value: String(state.roles.find(role => role.code === "sales")?.id || ""), options: state.roles.filter(role => role.code !== "owner").map(role => ({ value: String(role.id), label: role.name })) },
      { name: "team_ids", label: "所属团队", type: "multiselect", value: state.teams.length ? [state.teams[0].id] : [], options: state.teams.map(team => ({ value: String(team.id), label: team.name })) },
    ],
    submitText: "创建账户",
  });
  if (!result) return;
  result.role_id = Number(result.role_id);
  result.team_ids = result.team_ids.map(Number);
  try {
    await api("/api/team", { method: "POST", body: JSON.stringify(result) });
    showToast("团队成员已创建");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function createTeam() {
  const result = await openDialog({
    title: "新建团队",
    description: "团队用于隔离不同产品线、区域或业务小组的知识。",
    fields: [{ name: "name", label: "团队名称", required: true, placeholder: "例如：宠物用品组" }],
    submitText: "创建团队",
  });
  if (!result) return;
  try {
    await api("/api/teams", { method: "POST", body: JSON.stringify(result) });
    showToast("团队已创建");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editAccountTeams(accountId) {
  const account = state.team.find(item => item.id === accountId);
  if (!account) return;
  const current = state.teams.filter(team => (account.team_names || []).includes(team.name)).map(team => String(team.id));
  const result = await openDialog({
    title: `调整团队 · ${account.display_name}`,
    description: "团队成员将看到所属团队的 team 范围知识。",
    fields: [{
      name: "team_ids", label: "所属团队", type: "multiselect",
      value: current,
      options: state.teams.map(team => ({ value: String(team.id), label: team.name })),
    }],
    submitText: "保存团队",
  });
  if (!result) return;
  result.team_ids = result.team_ids.map(Number);
  try {
    await api(`/api/team/${accountId}/teams`, {
      method: "PUT", body: JSON.stringify(result),
    });
    showToast("成员团队已更新，该成员需要重新登录");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderRoles() {
  if (!hasPermission("role.manage")) return;
  $("#role-list").innerHTML = state.roles.map(role => `
    <article class="role-item">
      <div class="role-item-head">
        <div><strong>${esc(role.name)}</strong> <span class="muted small">${esc(role.code)}${role.builtin ? " · 内置模板" : ""}</span></div>
        ${role.code !== "owner" ? `<button class="text-button edit-role" data-id="${role.id}">编辑权限</button>` : '<span class="pill">不可修改</span>'}
      </div>
      <div class="permission-chips">
        ${(role.permissions || []).map(code => `<span class="permission-chip">${esc(permissionLabels[code] || code)}</span>`).join("") || '<span class="muted small">未分配权限</span>'}
      </div>
    </article>
  `).join("");
  $$(".edit-role").forEach(button => button.addEventListener("click", () => editRolePermissions(Number(button.dataset.id))));
}

async function createRole() {
  const result = await openDialog({
    title: "新建角色",
    description: "角色用于业务差异，不要求老板理解技术权限模型。",
    fields: [
      { name: "name", label: "角色名称", required: true, placeholder: "例如：宠物用品业务员" },
      { name: "code", label: "角色编码", required: true, pattern: "^[a-z][a-z0-9_]*$", error: "需以小写字母开头，仅支持小写字母、数字和下划线" },
      { name: "permissions", label: "权限", type: "multiselect", value: ["ask.use", "knowledge.view", "knowledge.suggest"], options: Object.entries(permissionLabels).map(([value, label]) => ({ value, label })) },
    ],
    submitText: "创建角色",
  });
  if (!result) return;
  try {
    await api("/api/roles", { method: "POST", body: JSON.stringify(result) });
    showToast("角色已创建");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editRolePermissions(roleId) {
  const role = state.roles.find(item => item.id === roleId);
  if (!role) return;
  const result = await openDialog({
    title: `编辑权限 · ${role.name}`,
    description: "权限只影响功能。知识可见范围由每条知识自己的公司/团队/个人范围控制。",
    fields: [{
      name: "permissions", label: "功能权限", type: "multiselect",
      value: role.permissions || [],
      options: Object.entries(permissionLabels).map(([value, label]) => ({ value, label })),
    }],
    submitText: "保存权限",
  });
  if (!result) return;
  try {
    await api(`/api/roles/${roleId}/permissions`, {
      method: "PUT", body: JSON.stringify(result),
    });
    showToast("角色权限已更新");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function ensureTeams() {
  if (state.teams.length) return;
  try {
    const data = await api("/api/teams");
    state.teams = data.items || [];
  } catch (_) {
    state.teams = [];
  }
}

async function changePassword() {
  const result = await openDialog({
    title: "修改密码",
    description: "新密码至少 8 位。修改后其他设备的登录会话会退出。",
    fields: [
      { name: "current_password", label: "当前密码", inputType: "password", required: true },
      { name: "new_password", label: "新密码", inputType: "password", required: true },
    ],
    submitText: "修改密码",
  });
  if (!result) return;
  try {
    await api("/api/auth/password", {
      method: "POST", body: JSON.stringify(result),
    });
    state.user.must_change_password = false;
    showToast("密码已修改");
  } catch (error) {
    showToast(error.message, true);
  }
}

function openConfirm(title, description, confirmText = "确认") {
  return openDialog({
    title,
    description,
    fields: [],
    submitText: confirmText,
    danger: true,
  }).then(value => Boolean(value));
}

function openDialog({ title, description = "", fields = [], submitText = "保存", danger = false }) {
  return new Promise(resolve => {
    const root = $("#modal-root");
    let finished = false;
    const finish = value => {
      if (finished) return;
      finished = true;
      root.hidden = true;
      root.innerHTML = "";
      document.removeEventListener("keydown", onKey);
      resolve(value);
    };
    const onKey = event => {
      if (event.key === "Escape") finish(null);
    };
    document.addEventListener("keydown", onKey);
    root.hidden = false;
    root.innerHTML = `
      <div class="dialog-card" role="dialog" aria-modal="true">
        <h2>${esc(title)}</h2>
        ${description ? `<p class="muted">${esc(description)}</p>` : ""}
        <form class="dialog-form" id="dialog-form">
          ${fields.map((field, index) => dialogField(field, index)).join("")}
          <div class="dialog-error" id="dialog-error"></div>
          <div class="dialog-actions">
            <button class="button ghost" type="button" id="dialog-cancel">取消</button>
            <button class="button ${danger ? "danger" : "primary"}" type="submit">${esc(submitText)}</button>
          </div>
        </form>
      </div>`;
    root.onclick = event => { if (event.target === root) finish(null); };
    $("#dialog-cancel").onclick = () => finish(null);
    $("#dialog-form").onsubmit = event => {
      event.preventDefault();
      const values = {};
      const error = $("#dialog-error");
      error.style.display = "none";
      for (let index = 0; index < fields.length; index += 1) {
        const field = fields[index];
        if (field.type === "multiselect") {
          values[field.name] = $$(`input[data-dialog-field="${index}"]:checked`)
            .map(input => input.value);
          continue;
        }
        const input = $(`#dialog-field-${index}`);
        const value = input.value.trim();
        if (field.required && !value) {
          error.textContent = `${field.label}不能为空`;
          error.style.display = "block";
          input.focus();
          return;
        }
        if (value && field.pattern && !(new RegExp(field.pattern)).test(value)) {
          error.textContent = field.error || `${field.label}格式不正确`;
          error.style.display = "block";
          input.focus();
          return;
        }
        values[field.name] = value;
      }
      finish(values);
    };
    const first = $("#dialog-form input, #dialog-form textarea, #dialog-form select");
    if (first) first.focus();
  });
}

function dialogField(field, index) {
  const id = `dialog-field-${index}`;
  let control;
  if (field.type === "textarea") {
    control = `<textarea id="${id}" placeholder="${esc(field.placeholder || "")}">${esc(field.value || "")}</textarea>`;
  } else if (field.type === "multiselect") {
    const selected = new Set(field.value || []);
    control = `<div class="permission-grid">${(field.options || []).map(option => {
      const value = typeof option === "string" ? option : option.value;
      const label = typeof option === "string" ? option : option.label;
      return `<label class="permission-option"><input type="checkbox" data-dialog-field="${index}" value="${esc(value)}" ${selected.has(value) ? "checked" : ""}>${esc(label)}</label>`;
    }).join("")}</div>`;
  } else if (field.type === "select") {
    control = `<select id="${id}">${(field.options || []).map(option => {
      const value = typeof option === "string" ? option : option.value;
      const label = typeof option === "string" ? option : option.label;
      return `<option value="${esc(value)}" ${String(field.value || "") === String(value) ? "selected" : ""}>${esc(label)}</option>`;
    }).join("")}</select>`;
  } else {
    control = `<input id="${id}" type="${esc(field.inputType || "text")}" value="${esc(field.value || "")}" placeholder="${esc(field.placeholder || "")}">`;
  }
  return `<label>${esc(field.label)}${field.required ? " *" : ""}${control}</label>`;
}

$("#setup-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  const body = Object.fromEntries(form.entries());
  try {
    const result = await api("/api/setup", { method: "POST", body: JSON.stringify(body) });
    enterApp(result.user);
  } catch (error) {
    const box = $("#setup-error");
    box.textContent = error.message;
    box.style.display = "block";
  }
});

$("#login-form").addEventListener("submit", async event => {
  event.preventDefault();
  const form = new FormData(event.currentTarget);
  try {
    const result = await api("/api/auth/login", {
      method: "POST", body: JSON.stringify(Object.fromEntries(form.entries())),
    });
    enterApp(result.user);
  } catch (error) {
    const box = $("#login-error");
    box.textContent = error.message;
    box.style.display = "block";
  }
});

$$(".nav button").forEach(button => button.addEventListener("click", () => showPage(button.dataset.page)));
$("#mobile-menu").addEventListener("click", () => $(".sidebar").classList.toggle("open"));
$("#logout-button").addEventListener("click", async () => {
  await api("/api/auth/logout", { method: "POST" });
  state.user = null;
  showLogin();
});
$("#change-password-button").addEventListener("click", changePassword);
$("#ask-button").addEventListener("click", ask);
$("#knowledge-refresh").addEventListener("click", loadKnowledge);
$("#knowledge-status").addEventListener("change", loadKnowledge);
$("#knowledge-kind").addEventListener("change", loadKnowledge);
$("#knowledge-search").addEventListener("keydown", event => { if (event.key === "Enter") loadKnowledge(); });
$("#new-knowledge-button").addEventListener("click", createKnowledge);
$("#import-knowledge-button").addEventListener("click", importKnowledge);
$("#gap-status").addEventListener("change", loadGaps);
$("#new-account-button").addEventListener("click", createAccount);
$("#new-team-button").addEventListener("click", createTeam);
$("#new-role-button").addEventListener("click", createRole);

boot();

