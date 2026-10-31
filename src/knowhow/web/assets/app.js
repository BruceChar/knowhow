const state = {
  user: null,
  dashboard: null,
  knowledge: [],
  gaps: [],
  team: [],
  roles: [],
  teamRoles: [],
  teams: [],
  owners: [],
  lastInteraction: null,
};

const pageMeta = {
  today: ["TODAY", "今日工作台"],
  ask: ["SALES COPILOT", "销售工作台"],
  knowledge: ["COMPANY BRAIN", "公司知识"],
  review: ["REVIEW INBOX", "待处理"],
  team: ["TEAM", "团队"],
  training: ["READINESS", "新人训练"],
  facts: ["FACTS", "产品报价"],
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
  "training.use": "使用新人训练",
  "facts.view": "查看产品与报价事实",
  "facts.manage": "导入和管理产品报价",
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
  const inviteToken = new URLSearchParams(window.location.search).get("token");
  if (window.location.pathname === "/join" && inviteToken) {
    await loadJoinInvite(inviteToken);
    return;
  }
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

async function loadJoinInvite(token) {
  $("#setup-view").hidden = true;
  $("#login-view").hidden = true;
  $("#app-view").hidden = true;
  $("#join-view").hidden = false;
  try {
    const invite = await api(`/api/invites/${encodeURIComponent(token)}`);
    $("#join-invite-info").textContent = `${invite.organization_name} · ${invite.team_name} · ${invite.role_name}`;
    $("#join-form").dataset.token = token;
  } catch (error) {
    $("#join-invite-info").textContent = error.message;
    $("#join-form").hidden = true;
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
  $('[data-page="team"]').hidden = !(
    hasPermission("team.manage") ||
    (state.user?.team_roles || []).some(role => (role.permissions || []).includes("team.lead"))
  );
  $('[data-page="training"]').hidden = !hasPermission("training.use");
  $('[data-page="facts"]').hidden = !hasPermission("facts.view");
  $("#facts-import-button").hidden = !hasPermission("facts.manage");
  $("#role-panel").hidden = !hasPermission("role.manage");
  const globalTeamManager = hasPermission("team.manage");
  $("#new-team-button").hidden = !globalTeamManager;
  $("#new-account-button").hidden = !globalTeamManager;
  $("#invite-member-button").hidden = !globalTeamManager;
  $("#lan-info-button").hidden = !globalTeamManager;
}

function hasPermission(code) {
  return (state.user?.permissions || []).includes(code);
}

function hasTeamPermission(teamId, code) {
  return (state.user?.team_roles || []).some(
    role => role.team_id === teamId && (role.permissions || []).includes(code),
  );
}

function canManageTeam(teamId) {
  return hasPermission("team.manage") || hasTeamPermission(teamId, "team.lead");
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
  if (name === "training") await loadTraining();
  if (name === "facts") await loadFacts();
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
    [metrics.training_score, "训练平均分"],
    [metrics.won_30d, "30 天成交"],
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
      <span class="muted small">交互 #${esc(data.interaction_id)} · ${esc(data.model)} · 检索 ${esc(data.retrieval?.mode || "lexical")}</span>
    </div>
    <textarea class="answer-editor" id="final-answer" placeholder="输入最终要发给客户的回复">${esc(data.answer)}</textarea>
    <div class="citation-list">
      ${data.citations.map(item => `<span class="citation">#${item.id} ${esc(item.title)}</span>`).join("") || '<span class="muted small">当前没有可引用知识</span>'}
    </div>
    <div class="answer-actions">
      <select id="outcome-select">
        <option value="unknown">客户结果</option>
        <option value="following">持续跟进</option>
        <option value="won">已成交</option>
        <option value="lost">已丢单</option>
      </select>
      <button class="button ghost" id="save-outcome-button">保存结果</button>
      <button class="button ghost" id="save-copy-button">复制并记录</button>
      <button class="button primary" id="mark-sent-button">标记已发送</button>
    </div>
  `;
  $("#save-copy-button").addEventListener("click", () => saveInteraction("copied", true));
  $("#mark-sent-button").addEventListener("click", () => saveInteraction("sent", false));
  $("#save-outcome-button").addEventListener("click", saveOutcome);
}

async function saveOutcome() {
  if (!state.lastInteraction) return;
  try {
    await api(`/api/interactions/${state.lastInteraction.interaction_id}/outcome`, {
      method: "PUT",
      body: JSON.stringify({
        outcome: $("#outcome-select").value,
        value: null,
        note: "",
      }),
    });
    showToast("客户结果已记录");
  } catch (error) {
    showToast(error.message, true);
  }
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
    const [data, imports] = await Promise.all([
      api(`/api/knowledge?${params}`),
      api("/api/imports"),
    ]);
    state.knowledge = data.items;
    renderKnowledge();
    renderImports(imports.items);
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderImports(items) {
  $("#import-history").innerHTML = items.map(item => `
    <article class="knowledge-item">
      <div class="kind-label">${esc(item.source_type.toUpperCase())}</div>
      <div><h4>${esc(item.source_name)}</h4><p>${esc(item.status)} · 可导入 ${item.ready_rows} · 重复 ${item.duplicate_rows} · 错误 ${item.error_rows} · 已创建 ${item.created_count}</p></div>
      <div>${item.status === "preview" && item.ready_rows ? `<button class="text-button execute-import" data-id="${item.id}">继续导入</button>` : ""}</div>
    </article>
  `).join("") || '<p class="muted small">还没有文件导入记录。</p>';
  $$(".execute-import").forEach(button => button.addEventListener("click", () => executeImport(Number(button.dataset.id))));
}

async function uploadImport(file) {
  const form = new FormData();
  form.append("file", file);
  form.append("source_type", file.name.toLowerCase().includes("wechat") ? "wechat" : "auto");
  try {
    const response = await fetch("/api/imports/preview", {
      method: "POST",
      headers: { "X-Knowhow-CSRF": "1" },
      body: form,
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || `导入分析失败 (${response.status})`);
    const issueSummary = data.issues.slice(0, 5)
      .map(issue => `第 ${issue.row} 行：${issue.error}`).join("\n");
    const confirmed = await openDialog({
      title: "导入预览",
      description: [
        `文件：${data.source_name}`,
        `总行数：${data.total_rows}`,
        `可导入：${data.ready_rows}`,
        `重复：${data.duplicate_rows}`,
        `错误：${data.error_rows}`,
        issueSummary,
      ].filter(Boolean).join("\n"),
      fields: [],
      submitText: data.ready_rows ? "写入知识草稿" : "关闭",
    });
    if (confirmed && data.ready_rows) await executeImport(data.id);
    await loadKnowledge();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    $("#import-file-input").value = "";
  }
}

async function executeImport(batchId) {
  try {
    const result = await api(`/api/imports/${batchId}/execute`, { method: "POST" });
    showToast(`已导入 ${result.created} 条知识草稿`);
    await loadKnowledge();
  } catch (error) {
    showToast(error.message, true);
  }
}

function renderKnowledge() {
  $("#knowledge-list").innerHTML = state.knowledge.map(item => `
    <article class="knowledge-item">
      <div class="kind-label">${esc(kindLabels[item.kind] || item.kind)}</div>
      <div>
        <h4>${esc(item.title)}</h4>
        <p>${esc(item.answer).slice(0, 180)}</p>
        <p>版本 ${item.version} · ${esc(item.owner_name || "未指定负责人")} · ${esc(scopeLabel(item.scope, item.team_name))} ${item.valid_until ? `· 有效期至 ${esc(item.valid_until)}` : ""} · ${item.embedding_model ? "语义索引已就绪" : "仅关键词检索"}</p>
      </div>
      <div>
        <span class="pill status-${esc(item.status)}">${esc(statusLabels[item.status] || item.status)}</span>
        ${(hasPermission("knowledge.publish") || (item.scope === "team" && hasTeamPermission(item.team_id, "knowledge.publish"))) && item.status !== "published" ? `<button class="text-button publish-knowledge" data-id="${item.id}">发布</button>` : ""}
        ${(hasPermission("knowledge.publish") || (item.scope === "team" && hasTeamPermission(item.team_id, "knowledge.publish"))) && item.status === "published" ? `<button class="text-button archive-knowledge" data-id="${item.id}">归档</button>` : ""}
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
  if (hasPermission("knowledge.edit")) await ensureOwners();
  const ownerOptions = state.owners.map(owner => ({ value: String(owner.id), label: `${owner.display_name} · ${owner.role_name}` }));
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
      ...(ownerOptions.length ? [{ name: "owner_id", label: "知识负责人", type: "select", value: String(state.user.id), options: ownerOptions }] : []),
      { name: "valid_until", label: "有效期（可选）", inputType: "date" },
      { name: "source_ref", label: "来源依据", placeholder: "文件、邮件或负责人确认记录" },
    ],
    submitText: "保存草稿",
  });
  if (!result) return;
  result.team_id = result.scope === "team" ? Number(result.team_id) : null;
  result.owner_id = result.owner_id ? Number(result.owner_id) : null;
  result.valid_until = result.valid_until || null;
  try {
    const created = await api("/api/knowledge", {
      method: "POST",
      body: JSON.stringify({ ...result, metadata: {} }),
    });
    showToast("知识草稿已保存");
    if (created.potential_conflicts?.length) {
      await openDialog({
        title: "发现相似知识",
        description: created.potential_conflicts.map(item => `#${item.id} ${item.title}`).join("\n"),
        fields: [],
        submitText: "我知道了",
      });
    }
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
    const globalTeamManager = hasPermission("team.manage");
    const [data, teams] = await Promise.all([
      globalTeamManager ? api("/api/team") : Promise.resolve({ items: [] }),
      globalTeamManager ? api("/api/teams/all") : api("/api/teams"),
    ]);
    const roles = hasPermission("role.manage")
      ? await api("/api/roles") : null;
    const teamRoles = (globalTeamManager || hasTeamLeadRole())
      ? await api("/api/team-roles") : null;
    state.team = data.items;
    state.teams = teams.items;
    state.roles = roles?.items || [];
    state.teamRoles = teamRoles?.items || [];
    renderTeams();
    renderTeam();
    renderRoles();
  } catch (error) {
    showToast(error.message, true);
  }
}

function hasTeamLeadRole() {
  return (state.user?.team_roles || []).some(
    role => (role.permissions || []).includes("team.lead"),
  );
}

function renderTeams() {
  const globalTeamManager = hasPermission("team.manage");
  $("#team-overview").innerHTML = state.teams.map(team => `
    <article class="team-item">
      <div><strong>${esc(team.name)}</strong><p class="muted small">${team.member_count} 名成员 · 默认角色：${esc(team.default_role_name || "未设置")}</p></div>
      <div class="button-row">
        <button class="button ghost manage-team-members" data-id="${team.id}">成员与角色</button>
        ${globalTeamManager ? `<button class="button ghost set-team-role" data-id="${team.id}">设置默认角色</button>
        <button class="button ghost apply-team-role" data-id="${team.id}">统一应用到组员</button>` : ""}
      </div>
    </article>
  `).join("") || '<p class="muted small">暂无团队。</p>';
  $$(".set-team-role").forEach(button => button.addEventListener("click", () => editTeamRole(Number(button.dataset.id))));
  $$(".apply-team-role").forEach(button => button.addEventListener("click", () => applyTeamRole(Number(button.dataset.id))));
  $$(".manage-team-members").forEach(button => button.addEventListener("click", () => manageTeamMembers(Number(button.dataset.id))));
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
  const availableRoles = state.roles.length ? state.roles : state.teamRoles;
  const result = await openDialog({
    title: "添加团队成员",
    description: "首次登录后建议修改临时密码。",
    fields: [
      { name: "display_name", label: "姓名", required: true },
      { name: "username", label: "登录名", required: true, pattern: "^[a-zA-Z0-9_.-]{3,}$", error: "至少 3 位，仅支持字母、数字、点、横线和下划线" },
      { name: "password", label: "初始密码", inputType: "password", required: true },
      { name: "role_id", label: "公司角色", type: "select", required: true, value: String(availableRoles.find(role => role.code === "sales")?.id || ""), options: availableRoles.filter(role => role.code !== "owner" && !role.team_only).map(role => ({ value: String(role.id), label: role.name })) },
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
  if (!state.roles.length) await loadTeam();
  const result = await openDialog({
    title: "新建团队",
    description: "团队是一个共享边界。示例：把北美客户业务员放在“北美组”，他们能看到北美组的团队知识，但看不到欧洲组。",
    fields: [
      { name: "name", label: "团队名称", required: true, placeholder: "例如：宠物用品组" },
      { name: "default_role_id", label: "团队默认角色", type: "select", value: String(state.roles.find(role => role.code === "sales")?.id || ""), options: state.roles.filter(role => role.code !== "owner").map(role => ({ value: String(role.id), label: role.name })) },
    ],
    submitText: "创建团队",
  });
  if (!result) return;
  result.default_role_id = Number(result.default_role_id);
  try {
    await api("/api/teams", { method: "POST", body: JSON.stringify(result) });
    showToast("团队已创建");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function editTeamRole(teamId) {
  const team = state.teams.find(item => item.id === teamId);
  if (!team) return;
  const result = await openDialog({
    title: `团队默认角色 · ${team.name}`,
    description: "这里只设置默认值。设置后可以点击“统一应用到组员”，不会自动覆盖老板。",
    fields: [{
      name: "role_id", label: "默认角色", type: "select",
      value: String(team.default_role_id || ""),
      options: state.roles.filter(role => role.code !== "owner").map(role => ({ value: String(role.id), label: role.name })),
    }],
    submitText: "保存默认角色",
  });
  if (!result) return;
  result.role_id = Number(result.role_id);
  try {
    await api(`/api/teams/${teamId}/default-role`, {
      method: "PUT", body: JSON.stringify(result),
    });
    showToast("团队默认角色已更新");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function manageTeamMembers(teamId) {
  try {
    const team = state.teams.find(item => item.id === teamId);
    const result = await api(`/api/teams/${teamId}/members`);
    const members = result.items || [];
    const roles = state.teamRoles.length ? state.teamRoles : state.roles;
    if (!members.length) {
      showToast("该团队还没有成员；请先在“成员列表”中调整团队。");
      return;
    }
    const summary = members.map(member => `${member.display_name}：${member.role_name}`).join("\n");
    const form = await openDialog({
      title: `团队成员角色 · ${team?.name || ""}`,
      description: `${summary}\n\n选择一名成员和团队角色。团队角色只在该团队内生效。`,
      fields: [
        {
          name: "account_id", label: "成员", type: "select",
          options: members.map(member => ({
            value: String(member.id),
            label: `${member.display_name}（当前：${member.role_name}）`,
          })),
        },
        {
          name: "role_id", label: "团队角色", type: "select",
          options: roles.filter(role => role.team_only || role.code === "sales").map(role => ({
            value: String(role.id), label: role.name,
          })),
        },
      ],
      submitText: "保存团队角色",
    });
    if (!form) return;
    await api(`/api/teams/${teamId}/members/${form.account_id}`, {
      method: "PUT",
      body: JSON.stringify({ role_id: Number(form.role_id) }),
    });
    showToast("团队角色已更新，该成员需要重新登录");
    await loadTeam();
  } catch (error) {
    showToast(error.message, true);
  }
}

async function applyTeamRole(teamId) {
  const team = state.teams.find(item => item.id === teamId);
  if (!team) return;
  const confirmed = await openConfirm(
    "统一团队成员角色",
    `将“${team.name}”所有非老板成员的权限统一改为“${team.default_role_name || "默认角色"}”。成员需要重新登录，是否继续？`,
    "统一角色",
  );
  if (!confirmed) return;
  try {
    const result = await api(`/api/teams/${teamId}/apply-role`, { method: "POST" });
    showToast(`已更新 ${result.updated} 名成员`);
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

async function showLanInfo() {
  try {
    const info = await api("/api/runtime/info");
    await showShareCard({
      title: "局域网分享",
      description: "把二维码或地址发给同一局域网内的同事。员工仍需账号密码登录。",
      url: info.base_url,
      qrSvg: info.qr_svg,
    });
  } catch (error) {
    showToast(error.message, true);
  }
}

async function inviteMember() {
  if (!state.roles.length || !state.teams.length) await loadTeam();
  const result = await openDialog({
    title: "邀请成员",
    description: "邀请链接一次有效。成员打开后只需填写姓名、登录名和密码，不需要理解角色编码。",
    fields: [
      { name: "team_id", label: "加入团队", type: "select", value: String(state.teams[0]?.id || ""), options: state.teams.map(team => ({ value: String(team.id), label: team.name })) },
      { name: "role_id", label: "团队角色", type: "select", value: String(state.teams[0]?.default_role_id || state.roles.find(role => role.code === "sales")?.id || ""), options: state.roles.filter(role => role.code !== "owner").map(role => ({ value: String(role.id), label: role.name })) },
      { name: "expires_hours", label: "有效期（小时）", inputType: "number", value: "72", required: true },
    ],
    submitText: "生成邀请",
  });
  if (!result) return;
  result.team_id = Number(result.team_id);
  result.role_id = Number(result.role_id);
  result.expires_hours = Number(result.expires_hours);
  try {
    const invite = await api("/api/invites", {
      method: "POST", body: JSON.stringify(result),
    });
    await showShareCard({
      title: "发送团队邀请",
      description: `${invite.organization_name} · ${invite.team_name} · ${invite.role_name}\n链接一次有效，过期或使用后失效。`,
      url: invite.url,
      qrSvg: invite.qr_svg,
    });
  } catch (error) {
    showToast(error.message, true);
  }
}

function copyText(text) {
  if (navigator.clipboard && window.isSecureContext) {
    return navigator.clipboard.writeText(text);
  }
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.appendChild(textarea);
  textarea.select();
  document.execCommand("copy");
  textarea.remove();
  return Promise.resolve();
}

function showShareCard({ title, description, url, qrSvg }) {
  return openDialog({
    title,
    description,
    fields: [],
    submitText: "关闭",
    bodyHtml: `
      <div class="invite-box">
        ${qrSvg}
        <div class="invite-url" id="share-url">${esc(url)}</div>
        <div class="button-row" style="justify-content:center;margin-top:10px;">
          <button class="button ghost" type="button" id="copy-share-url">复制链接</button>
        </div>
      </div>`,
    afterRender: () => {
      $("#copy-share-url").onclick = async () => {
        await copyText(url);
        showToast("链接已复制");
      };
    },
  });
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
    description: "角色决定“能做什么”，不决定“能看哪条知识”。\n示例：业务员可以提问和提交草稿，但不能发布；知识负责人可以编辑和发布。\n系统会自动生成内部编码，无需填写。",
    fields: [
      { name: "name", label: "角色名称", required: true, placeholder: "例如：宠物用品业务员" },
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

async function ensureOwners() {
  if (state.owners.length) return;
  try {
    const data = await api("/api/knowledge/owners");
    state.owners = data.items || [];
  } catch (_) {
    state.owners = [];
  }
}

async function loadTraining() {
  try {
    const [quiz, readiness] = await Promise.all([
      api("/api/training/quiz"),
      api("/api/training/readiness"),
    ]);
    $("#training-score").textContent = readiness.average_score;
    $("#training-list").innerHTML = quiz.items.map((item, index) => `
      <article class="training-card">
        <div class="panel-head"><strong>${esc(item.question)}</strong><span class="pill">${item.risk_level === "high" ? "高风险" : "练习"}</span></div>
        <textarea id="training-response-${index}" placeholder="写下你会怎样回复客户"></textarea>
        <div class="button-row" style="justify-content:flex-end;">
          <button class="button primary training-submit" data-index="${index}" data-id="${item.id}">提交答案</button>
        </div>
        <div id="training-result-${index}"></div>
      </article>
    `).join("") || '<p class="muted">还需要先发布标准问答，系统才能生成训练题。</p>';
    $$(".training-submit").forEach(button => button.addEventListener("click", () => submitTraining(Number(button.dataset.index), Number(button.dataset.id))));
  } catch (error) {
    showToast(error.message, true);
  }
}

async function submitTraining(index, knowledgeId) {
  const response = $(`#training-response-${index}`).value.trim();
  if (!response) {
    showToast("请先写下你的回复", true);
    return;
  }
  try {
    const result = await api("/api/training/attempts", {
      method: "POST",
      body: JSON.stringify({ knowledge_id: knowledgeId, response }),
    });
    const feedback = result.feedback;
    $(`#training-result-${index}`).innerHTML = `
      <div class="training-result">
        <strong>${result.score} 分 · ${feedback.passed ? "达到参考标准" : "需要继续练习"}</strong>
        <p>参考答复：${esc(feedback.reference_answer)}</p>
        <p>缺少要点：${esc((feedback.missing || []).join("、") || "未发现明显遗漏")}</p>
      </div>`;
    const readiness = await api("/api/training/readiness");
    $("#training-score").textContent = readiness.average_score;
  } catch (error) {
    showToast(error.message, true);
  }
}

async function loadFacts() {
  try {
    const data = await api("/api/facts");
    $("#facts-list").innerHTML = data.items.map(item => `
      <article class="knowledge-item">
        <div class="kind-label">${esc(item.category || "产品")}</div>
        <div><h4>${esc(item.name)} · ${esc(item.sku)}</h4><p>${item.variants} 个变体 · ${item.active_offers} 个有效报价 · ${esc(item.status)}</p></div>
        <span class="pill">${item.active_offers ? "可用于客服" : "缺少报价"}</span>
      </article>
    `).join("") || '<p class="muted">还没有产品报价。导入 CSV 后，价格类问题会优先使用这些事实。</p>';
  } catch (error) {
    showToast(error.message, true);
  }
}

async function importFacts(file) {
  const form = new FormData();
  form.append("file", file);
  try {
    const response = await fetch("/api/facts/import", {
      method: "POST",
      headers: { "X-Knowhow-CSRF": "1" },
      body: form,
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "报价导入失败");
    const issueText = result.errors.slice(0, 5)
      .map(issue => `第 ${issue.row} 行：${issue.error}`).join("\n");
    showToast(`已导入 ${result.imported} 条报价`);
    if (issueText) {
      await openDialog({
        title: "部分行未导入",
        description: issueText,
        fields: [],
        submitText: "关闭",
      });
    }
    await loadFacts();
  } catch (error) {
    showToast(error.message, true);
  } finally {
    $("#facts-file-input").value = "";
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

function openDialog({
  title, description = "", fields = [], submitText = "保存", danger = false,
  bodyHtml = "", afterRender = null,
}) {
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
        ${bodyHtml}
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
    if (afterRender) afterRender();
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

$("#join-form").addEventListener("submit", async event => {
  event.preventDefault();
  const token = event.currentTarget.dataset.token;
  if (!token) return;
  const form = Object.fromEntries(new FormData(event.currentTarget).entries());
  try {
    const result = await api(`/api/invites/${encodeURIComponent(token)}/accept`, {
      method: "POST", body: JSON.stringify(form),
    });
    window.history.replaceState({}, "", "/");
    enterApp(result.user);
  } catch (error) {
    const box = $("#join-error");
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
$("#file-import-button").addEventListener("click", () => $("#import-file-input").click());
$("#import-file-input").addEventListener("change", event => {
  const file = event.target.files?.[0];
  if (file) uploadImport(file);
});
$("#gap-status").addEventListener("change", loadGaps);
$("#new-account-button").addEventListener("click", createAccount);
$("#new-team-button").addEventListener("click", createTeam);
$("#lan-info-button").addEventListener("click", showLanInfo);
$("#invite-member-button").addEventListener("click", inviteMember);
$("#reload-quiz-button").addEventListener("click", loadTraining);
$("#facts-import-button").addEventListener("click", () => $("#facts-file-input").click());
$("#facts-file-input").addEventListener("change", event => {
  const file = event.target.files?.[0];
  if (file) importFacts(file);
});
$("#new-role-button").addEventListener("click", createRole);

boot();

