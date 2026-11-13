const state = { products: [], preferences: [], currentUser: null, profile: null, token: localStorage.getItem("financial-session-token"), category: "全部", search: "", risk: "全部", sort: "default", view: "home", activeProduct: null, version: 0, request: 0 };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
const money = (value) => `¥${Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 })}`;
const isCustomer = () => state.currentUser?.role === "customer";
let modalOpener;

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401 && state.currentUser && !path.startsWith("/api/auth/")) showLogin();
    throw new Error(payload.error || "请求未完成，请稍后重试");
  }
  return payload;
}
const post = (path, body) => api(path, { method: "POST", body: JSON.stringify(body) });
function showToast(message) { const toast = $("#toast"); toast.textContent = message; toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(() => toast.classList.remove("show"), 3000); }
function riskLabel(level) { return ["低风险", "中低风险", "中风险", "中高风险", "高风险"][level - 1] || "待确认"; }
function termLabel(days) { if (days === null) return "持有安排待确认"; if (days <= 1) return "申赎安排见资料"; return `持有安排 ${days} 天`; }
function amountLabel(product) { if (product.min_amount === null || /起[投购]金额[^；。]*未(?:披露|列明)/.test(product.notes || "")) return "起购金额待确认"; return `${money(product.min_amount)} 起购`; }
function riskText(product) { return product.risk_level == null ? "风险分级待核实" : `R${product.risk_level} ${riskLabel(product.risk_level)}`; }
function productHighlight(product) {
  if (product.is_simulated) return { value: "模拟资料", label: "无真实购买入口" };
  if (product.category === "保险") return { value: "查看保障", label: "领取与退保以合同为准" };
  if (product.category === "养老理财") return { value: "查看基准", label: "业绩比较基准见正式资料" };
  return { value: product.expected_return_mid == null ? "待确认" : `${Number(product.expected_return_mid).toFixed(product.category === "现金管理" ? 3 : 2)}%`, label: product.category === "现金管理" ? "七日年化（资料快照）" : product.product_type === "储蓄国债" ? "票面年利率" : `存款年利率${product.selected_variant ? ` · ${product.selected_variant.label}` : "（参考档）"}` };
}
function productVisual(product, eager = false) { return `<div class="product-image-wrap"><img class="product-image" src="${escapeHTML(product.image)}" alt="${escapeHTML(product.image_alt || `${product.name}产品封面`)}" width="640" height="360" loading="${eager ? "eager" : "lazy"}" decoding="async" /><span class="image-fallback">${escapeHTML(product.issuer)} · ${escapeHTML(product.name)}</span></div>`; }
function emptyState(title, text, planButton = false) { return `<div class="empty-state"><strong>${escapeHTML(title)}</strong><p>${escapeHTML(text)}</p>${planButton ? '<button class="link-button" data-view="plan">完善资金计划 →</button>' : ""}</div>`; }
function returnTextRepeatsHighlight(product) {
  const match = /^\s*(\d+(?:\.\d+)?)\s*%\s*$/.exec(product.expected_return_text || "");
  return !product.expected_return_text || Boolean(match && product.expected_return_mid != null && Number(match[1]) === Number(product.expected_return_mid));
}
function productCard(product) {
  const highlight = productHighlight(product);
  return `<article class="product-card">${productVisual(product)}<div class="product-card-body"><h3>${escapeHTML(product.name)}</h3><div class="product-category">${escapeHTML(product.issuer)} · ${escapeHTML(product.product_type)}</div><div class="product-tags"><span class="product-tag risk ${product.risk_level >= 3 ? "high-risk" : ""}">${riskText(product)}</span><span class="product-tag">${termLabel(product.term_days)}</span></div>${product.assessment?.eligible ? '<p class="suitability-line">✓ 符合当前计划筛选条件</p>' : ""}${returnTextRepeatsHighlight(product) ? "" : `<p class="product-return-text">${escapeHTML(product.expected_return_text)}</p>`}<div class="product-card-bottom"><div class="product-rate"><strong>${highlight.value}</strong><small>${highlight.label}</small><small>${amountLabel(product)}</small></div><button data-product="${product.id}" data-variant="${escapeHTML(product.selected_variant?.id || "")}">查看详情</button></div><div class="product-as-of">资料日期 ${escapeHTML(product.as_of)} · ${escapeHTML(product.status)}</div></div></article>`;
}
function substituteCard(item) {
  const original = item.original;
  const replacement = item.replacement;
  return `<article class="substitute-card"><div class="substitute-original"><span class="substitute-label">您关注的商品</span><h3>${escapeHTML(original.name)}</h3><p>${escapeHTML(item.original_assessment.reasons[0])}</p><button class="link-button" data-product="${original.id}">查看原商品</button></div><span class="substitute-arrow" aria-hidden="true">→</span><div><span class="substitute-label">${replacement ? "可比较的同类选择" : "暂时没有合适替代"}</span>${replacement ? `<h3>${escapeHTML(replacement.name)}</h3><p>${riskText(replacement)} · ${amountLabel(replacement)}</p><ul>${item.differences.map((difference) => `<li>${escapeHTML(difference)}</li>`).join("")}</ul><button class="outline-button" data-product="${replacement.id}" data-variant="${escapeHTML(replacement.selected_variant?.id || "")}">比较这款商品</button>` : `<p>${escapeHTML(item.message)}</p>`}</div></article>`;
}
function renderSummary(profile) {
  if (!profile?.plan_completed) {
    $("#plan-summary").innerHTML = `<span class="section-kicker">您的资金安排</span><h2>先了解您，再挑选产品</h2><p>留好应急所需，告诉我们最早的用款时间和能够承受的风险。</p><button class="link-button" data-view="plan">填写我的资金计划 →</button>`;
    return;
  }
  const available = Math.max(0, profile.investment_budget - profile.emergency_reserve);
  $("#plan-summary").innerHTML = `<span class="section-kicker">我的资金计划</span><h2>扣除应急预留，可安排</h2><strong class="summary-amount">${money(available)}</strong>${profile.personal_pension_budget > 0 ? `<p>其中个人养老金账户 ${money(profile.personal_pension_budget)}，普通资金 ${money(Math.max(0, available - profile.personal_pension_budget))}，分别匹配。</p>` : ""}<div class="summary-meta"><div><span>应急预留</span><strong>${money(profile.emergency_reserve)}</strong></div><div><span>最早用款日期</span><strong>${escapeHTML(profile.target_date)}</strong></div><div><span>愿意考虑的最高风险</span><strong>R${profile.risk_tolerance} ${riskLabel(profile.risk_tolerance)}</strong></div><div><span>本金损失</span><strong>${profile.can_accept_loss ? "可接受一定损失" : "不能接受"}</strong></div></div><button class="link-button" data-view="plan">调整资金计划 →</button>`;
}
async function enterApp(user, token) {
  state.version += 1; state.currentUser = user; state.token = token; state.profile = null; state.products = []; state.preferences = []; state.activeProduct = null;
  state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default";
  $("#catalog-search").value = ""; $("#risk-filter").value = "全部"; $("#sort-filter").value = "default";
  localStorage.setItem("financial-session-token", token);
  $("#account-avatar").textContent = user.name.slice(0, 1); $("#account-name").textContent = user.name; $("#account-role").textContent = user.role === "admin" ? "管理员账号" : "客户账号";
  $$(".admin-only").forEach((item) => item.classList.toggle("is-hidden", user.role !== "admin"));
  $$(".customer-only").forEach((item) => item.classList.toggle("is-hidden", user.role !== "customer"));
  $("#login-screen").classList.add("is-hidden"); $("#app-shell").classList.remove("is-hidden");
  const version = state.version;
  try {
    const data = await api("/api/products");
    if (version !== state.version) return;
    state.products = data.items;
    if (isCustomer()) { const prefs = await api("/api/preferences"); if (version !== state.version) return; state.preferences = prefs.items; }
    navigate(user.role === "admin" ? "admin" : "home");
  } catch (error) { showToast(error.message); }
}
function showLogin() {
  state.version += 1; state.currentUser = null; state.token = null; state.products = []; state.preferences = []; state.profile = null;
  localStorage.removeItem("financial-session-token"); localStorage.removeItem("financial-session-user");
  $$(".modal-backdrop.open").forEach((modal) => closeModal(modal.id));
  $("#account-menu").classList.remove("open"); $("#app-shell").classList.add("is-hidden"); $("#login-screen").classList.remove("is-hidden"); $("#login-password").value = ""; $("#login-username").focus();
}
function navigate(view) {
  if (!state.currentUser) return;
  if ((view === "admin" && isCustomer()) || (["plan", "favorites"].includes(view) && !isCustomer())) return;
  state.view = view;
  $$(".nav-link").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  if (view === "home") loadHome();
  if (view === "products") renderCatalog();
  if (view === "plan") loadPlan();
  if (view === "favorites") loadFavorites();
  if (view === "admin") loadAdmin();
  window.scrollTo({ top: 0, behavior: "instant" });
}
async function loadHome() {
  if (!isCustomer()) {
    $("#plan-summary").innerHTML = '<h2>查看平台商品</h2><p>个人资金计划与推荐请使用客户账号。</p><button class="outline-button" data-view="products">浏览产品</button>';
    $("#recommendation-grid").innerHTML = state.products.slice(0, 3).map(productCard).join("");
    $("#recommendation-caption").textContent = "目录预览"; $("#substitute-grid").innerHTML = emptyState("暂无个人替代方案", "请使用客户账号查看。"); $("#caution-grid").innerHTML = emptyState("暂无个人关注记录", "请使用客户账号查看。"); return;
  }
  const version = state.version; const request = ++state.request;
  try {
    const result = await api("/api/recommendations");
    if (version !== state.version || request !== state.request) return;
    state.profile = result.customer; renderSummary(result.customer);
    $("#recommendation-caption").textContent = result.plan.profile_complete ? "按您的风险范围、可安排资金与用款时间筛选，购买前仍需确认正式条款。" : "完善资金计划后，为您筛选符合条件的商品。";
    $("#recommendation-grid").innerHTML = result.items.map(productCard).join("") || (result.plan.profile_complete ? emptyState("当前没有符合计划的商品", "可以暂不选择。请保留应急资金，并按照实际需要安排用款。") : emptyState("先安排好资金，再看推荐", "用几项简单的信息，了解您的用款需要。", true));
    $("#substitute-grid").innerHTML = result.substitute_items.map(substituteCard).join("") || emptyState("暂时无需替代", "您关注的商品出现不匹配时，这里会提供同类比较；也可以在商品详情中主动寻找。");
    $("#caution-grid").innerHTML = result.caution_items.map((item) => `<article class="caution-item"><div><h3>${escapeHTML(item.name)}</h3><p>${escapeHTML(item.assessment.reasons.join("；"))}</p></div><button class="outline-button" data-product="${item.id}">查看与比较</button></article>`).join("") || emptyState("暂时没有需要提醒的关注商品", "浏览和收藏时，我们会结合您的资金计划提示需要留意的地方。");
  } catch (error) { showToast(error.message); $("#recommendation-grid").innerHTML = emptyState("推荐暂时未能加载", "请稍后重新打开首页。"); }
}
function renderCatalog() {
  const search = state.search.trim().toLocaleLowerCase();
  let products = state.products.filter((product) => (state.category === "全部" || product.category === state.category) && (state.risk === "全部" || String(product.risk_level) === state.risk) && (!search || [product.name, product.full_name, product.code, product.issuer, product.product_type].some((value) => String(value || "").toLocaleLowerCase().includes(search))));
  if (state.sort === "risk") products.sort((a, b) => (a.risk_level ?? Infinity) - (b.risk_level ?? Infinity));
  if (state.sort === "amount") products.sort((a, b) => (amountLabel(a).includes("待确认") ? Infinity : a.min_amount) - (amountLabel(b).includes("待确认") ? Infinity : b.min_amount));
  $$("[data-category-filter]").forEach((button) => button.classList.toggle("active", button.dataset.categoryFilter === state.category));
  $("#catalog-count").textContent = `${products.length} 个产品`; $("#catalog-grid").innerHTML = products.map(productCard).join("") || emptyState("没有找到符合筛选条件的商品", "试试其他关键词或重置筛选。");
}
async function loadPlan() {
  const version = state.version;
  try {
    const data = await api("/api/profile"); if (version !== state.version) return;
    const p = data.customer; state.profile = p;
    $("#plan-retirement").value = p.retirement_status; $("#plan-pension").value = p.pension_type;
    $("#plan-budget").value = p.investment_budget; $("#plan-reserve").value = p.emergency_reserve;
    $("#plan-pension-status").value = p.personal_pension_status || "未确认"; $("#plan-pension-budget").value = p.personal_pension_budget || 0; $("#plan-date").value = p.target_date || ""; $("#plan-category").value = p.preferred_category;
    $("#plan-loss").value = p.can_accept_loss === null ? "" : String(p.can_accept_loss); $("#plan-risk").value = p.risk_tolerance; $("#plan-error").textContent = ""; previewBudget();
  } catch (error) { showToast(error.message); }
}
function previewBudget() { const available = Math.max(0, Number($("#plan-budget").value) - Number($("#plan-reserve").value)); $("#plan-available").textContent = money(available); }
async function savePlan(event) {
  event.preventDefault(); $("#plan-error").textContent = "";
  const button = event.submitter; button.disabled = true;
  try {
    const result = await post("/api/profile", { retirement_status: $("#plan-retirement").value, pension_type: $("#plan-pension").value, personal_pension_status: $("#plan-pension-status").value, personal_pension_budget: Number($("#plan-pension-budget").value), investment_budget: Number($("#plan-budget").value), emergency_reserve: Number($("#plan-reserve").value), target_date: $("#plan-date").value, preferred_category: $("#plan-category").value, can_accept_loss: $("#plan-loss").value === "1", risk_tolerance: Number($("#plan-risk").value) });
    state.profile = result.customer; showToast("资金计划已保存"); navigate("home");
  } catch (error) { $("#plan-error").textContent = error.message; } finally { button.disabled = false; }
}
async function loadFavorites() {
  const version = state.version;
  try { const data = await api("/api/preferences"); if (version !== state.version) return; state.preferences = data.items;
    const favorites = new Set(data.items.filter((item) => item.favorite).map((item) => item.product_id));
    const avoided = new Set(data.items.filter((item) => item.avoided).map((item) => item.product_id));
    $("#favorite-grid").innerHTML = state.products.filter((product) => favorites.has(product.id)).map(productCard).join("") || emptyState("还没有收藏", "在商品详情中点选收藏，留待日后仔细比较。");
    $("#avoided-list").innerHTML = state.products.filter((product) => avoided.has(product.id)).map((product) => `<div class="avoided-item"><span>${escapeHTML(product.name)}</span><button class="outline-button" data-restore="${product.id}">恢复关注</button></div>`).join("") || emptyState("暂无不感兴趣的商品", "您可以在商品详情中管理关注偏好。");
  } catch (error) { showToast(error.message); }
}
async function loadAdmin() {
  const version = state.version;
  try {
    const [overview, diagnostics] = await Promise.all([api("/api/admin/overview"), api("/api/admin/diagnostics")]); if (version !== state.version) return;
    $("#admin-stats").innerHTML = [["商品总数", overview.stats.products], ["客户账号", overview.stats.customers], ["真实行为记录", overview.stats.behaviors], ["推荐分组记录", diagnostics.counts.recommendations]].map(([label, value]) => `<div class="admin-stat"><span>${label}</span><strong>${value}</strong></div>`).join("");
    $("#admin-products").innerHTML = overview.products.map((item) => `<tr><td>${escapeHTML(item.name)}</td><td>${escapeHTML(item.issuer)}</td><td>${escapeHTML(item.category)}</td><td>${item.risk_level == null ? "待核实" : `R${item.risk_level}`}</td><td>${escapeHTML(item.status)}</td></tr>`).join("");
    $("#admin-diagnostic-summary").textContent = `${diagnostics.counts.rules} 条有效规则 · ${diagnostics.counts.anomalies} 条距离异常。正向以收藏为证据，负向以关注后明确“不感兴趣”为证据；至少 5 位相关用户、3 位共同反馈才生成规则。`;
    $("#admin-rules").innerHTML = diagnostics.rules.map((rule) => `<div class="diagnostic-entry">${rule.rule_type === "positive" ? "正向" : "负向偏好"} · 商品 ${rule.product_a} → ${rule.product_b} · 支持度 ${rule.support} · 置信度 ${rule.confidence} · 兴趣度 ${rule.interest} · 证据 ${rule.evidence_users}/${rule.exposed_users} 人</div>`).join("") || '<p class="small-note">暂无满足证据门槛的规则，推荐使用个人计划与商品特征。</p>';
    $("#admin-anomalies").innerHTML = diagnostics.anomalies.map((row) => `<div class="diagnostic-entry">${escapeHTML(row.customer_id)} · ${escapeHTML(row.reason)} · 最小距离 ${row.min_distance} · ${escapeHTML(row.created_at)}</div>`).join("") || '<p class="small-note">暂无距离异常。</p>';
    $("#admin-logs").innerHTML = diagnostics.logs.map((row) => `<div class="diagnostic-entry">${escapeHTML(row.customer_id)} · ${{ recommend: "推荐", substitute: "替代", caution: "谨慎" }[row.block_type]} · 商品 ${escapeHTML(row.product_ids)} · ${escapeHTML(row.created_at)}</div>`).join("") || '<p class="small-note">暂无推荐记录。</p>';
  } catch (error) { showToast(error.message); }
}
function openModal(id) { const modal = $(`#${id}`); if (!$(".modal-backdrop.open")) modalOpener = document.activeElement; modal.classList.add("open"); modal.setAttribute("aria-hidden", "false"); document.body.classList.add("modal-open"); const dialog = modal.querySelector("[role=dialog]"); dialog.scrollTop = 0; dialog.focus(); }
function closeModal(id) { const modal = $(`#${id}`); if (!modal.classList.contains("open")) return; modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); if (!$(".modal-backdrop.open")) { document.body.classList.remove("modal-open"); modalOpener?.focus(); } }
function updateActionButtons() {
  const preference = state.preferences.find((item) => item.product_id === state.activeProduct?.id);
  $("#modal-favorite").textContent = preference?.favorite ? "♥ 已收藏（取消）" : "♡ 收藏";
  $("#modal-avoid").textContent = preference?.avoided ? "恢复关注" : "不感兴趣";
}
async function behavior(productId, action) { const result = await post("/api/behaviors", { product_id: productId, action }); state.preferences = result.items; }
async function openProduct(productId, variantId = "") {
  let product = state.products.find((item) => item.id === Number(productId)); if (!product) return;
  const variant = product.variants?.find(item => item.id === variantId);
  if (variant) product = { ...product, selected_variant: variant, term_days: variant.term_days, min_amount: variant.min_amount, expected_return_mid: variant.rate, expected_return_text: variant.rate == null ? "利率待确认" : `${variant.rate.toFixed(2)}%`, status: variant.status };
  state.activeProduct = product; const version = state.version;
  $("#modal-visual").innerHTML = productVisual(product, true); $("#modal-category").textContent = `${product.category} · ${product.issuer}`; $("#modal-title").textContent = `${product.name}${product.selected_variant ? ` · ${product.selected_variant.label}` : ""}`; $("#modal-description").textContent = product.description;
  $("#modal-meta").innerHTML = [["目录风险等级", `${riskText(product)}`], ["持有 / 申赎安排", termLabel(product.term_days)], ["资料中的状态", product.status], ["起购金额", amountLabel(product)], ["产品代码", product.code || "待披露"]].map(([label, value]) => `<div><span>${label}</span><strong>${escapeHTML(value)}</strong></div>`).join("");
  $("#modal-return").textContent = `${product.return_basis}：${product.expected_return_text}`; $("#modal-notes").textContent = product.notes || ""; $("#modal-as-of").textContent = `资料日期：${product.as_of} · 来源：${product.data_source}`; $("#modal-primary").classList.toggle("is-hidden", !product.source_url || Boolean(product.is_simulated));
  if (product.source_url && !product.is_simulated) $("#modal-primary").href = product.source_url;
  $("#modal-variants").innerHTML = product.variants?.length ? `<details class="variant-details"><summary>查看全部期限档（${product.variants.length} 档）</summary><div class="table-scroll"><table><thead><tr><th>期限档</th><th>起存金额</th><th>年利率</th><th>资料状态</th></tr></thead><tbody>${product.variants.map(v => `<tr><td>${escapeHTML(v.label)}</td><td>${v.min_amount == null ? "待确认" : money(v.min_amount)}</td><td>${v.rate == null ? "待确认" : `${v.rate.toFixed(2)}%`}</td><td>${escapeHTML(v.status)}</td></tr>`).join("")}</tbody></table></div><p class="small-note">各期限档归属同一商品；额度、提前支取和利息规则请向银行确认。</p></details>` : "";
  $("#modal-substitute-result").innerHTML = ""; $("#modal-assessment").className = "assessment"; $("#modal-assessment").textContent = isCustomer() ? "正在核对您的资金计划…" : "请使用客户账号核对个人资金计划。"; updateActionButtons(); openModal("product-modal");
  if (!isCustomer()) return;
  try {
    await behavior(product.id, "view"); const result = await api(`/api/product-assessment?product_id=${product.id}${variantId ? `&variant_id=${encodeURIComponent(variantId)}` : ""}`);
    if (version !== state.version || state.activeProduct?.id !== product.id) return;
    const assessment = result.assessment; $("#modal-assessment").classList.toggle("warning", !assessment.eligible);
    $("#modal-assessment").innerHTML = `<strong>${escapeHTML(assessment.status)}</strong>${assessment.selected_variant ? `<p>匹配的期限档：${escapeHTML(assessment.selected_variant.label)} · ${money(assessment.selected_variant.min_amount)} 起存</p>` : ""}<ul>${assessment.reasons.map((reason) => `<li>${escapeHTML(reason)}</li>`).join("")}</ul>${assessment.codes.includes("profile") ? '<button class="link-button" data-view="plan">完善资金计划 →</button>' : ""}`;
  } catch (error) { $("#modal-assessment").textContent = error.message; }
}
async function submitAuth(event, kind) {
  event.preventDefault(); const button = event.submitter; const errorNode = $(`#${kind}-error`); errorNode.textContent = ""; button.disabled = true;
  try {
    const password = $(`#${kind}-password`).value;
    if (kind === "register" && password !== $("#register-confirm").value) throw new Error("两次输入的密码不一致");
    const payload = { username: $(`#${kind}-username`).value.trim(), password };
    if (kind === "register") payload.name = $("#register-name").value.trim();
    const result = await post(`/api/auth/${kind}`, payload);
    $(`#${kind}-form`).reset(); if (kind !== "login") closeModal(`${kind}-modal`);
    await enterApp(result.user, result.token); if (kind === "register") { navigate("plan"); showToast("账号已创建，请填写您的资金计划"); }
  } catch (error) { errorNode.textContent = error.message; } finally { button.disabled = false; }
}
function setTextSize(value) { document.documentElement.style.fontSize = `${16 * Number(value) / 100}px`; document.documentElement.classList.toggle("large-text", Number(value) > 100); localStorage.setItem("financial-text-size", value); $("#text-size").value = value; }
function bindEvents() {
  document.addEventListener("click", async (event) => {
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) { event.preventDefault(); closeModal("product-modal"); navigate(viewButton.dataset.view); }
    const categoryButton = event.target.closest("[data-category], [data-category-filter]");
    if (categoryButton) { state.category = categoryButton.dataset.category || categoryButton.dataset.categoryFilter; navigate("products"); }
    const productButton = event.target.closest("[data-product]"); if (productButton) openProduct(productButton.dataset.product, productButton.dataset.variant);
    const closeButton = event.target.closest("[data-close]"); if (closeButton) { const id = closeButton.dataset.close; closeModal(id); if (id === "product-modal") refreshCurrentView(); }
    if (!event.target.closest(".account-wrap")) { $("#account-menu").classList.remove("open"); $("#account-button").setAttribute("aria-expanded", "false"); }
    const restoreButton = event.target.closest("[data-restore]"); if (restoreButton) { try { await behavior(Number(restoreButton.dataset.restore), "restore"); showToast("已恢复关注"); loadFavorites(); } catch (error) { showToast(error.message); } }
  });
  document.addEventListener("error", (event) => { if (event.target.matches?.(".product-image")) event.target.parentElement.classList.add("image-failed"); }, true);
  $$(".modal-backdrop").forEach((modal) => modal.addEventListener("click", (event) => { if (event.target === modal) { closeModal(modal.id); if (modal.id === "product-modal") refreshCurrentView(); } }));
  document.addEventListener("keydown", (event) => {
    const modal = $(".modal-backdrop.open"); if (!modal) return;
    if (event.key === "Escape") { closeModal(modal.id); if (modal.id === "product-modal") refreshCurrentView(); }
    if (event.key === "Tab") { const nodes = [...modal.querySelectorAll('button:not([disabled]), a[href], input, select, [tabindex="0"]')].filter((node) => node.getClientRects().length); const first = nodes[0]; const last = nodes.at(-1); if (event.shiftKey && (document.activeElement === first || document.activeElement === modal.querySelector("[role=dialog]"))) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } }
  });
  $("#account-button").addEventListener("click", () => { const open = $("#account-menu").classList.toggle("open"); $("#account-button").setAttribute("aria-expanded", String(open)); });
  $("#switch-account").addEventListener("click", () => { $("#switch-form").reset(); $("#switch-error").textContent = ""; openModal("switch-modal"); $("#switch-username").focus(); });
  $("#logout-button").addEventListener("click", async () => { try { await post("/api/auth/logout", {}); } finally { showLogin(); } });
  $("#register-link").addEventListener("click", () => { $("#register-form").reset(); $("#register-error").textContent = ""; openModal("register-modal"); $("#register-name").focus(); });
  ["login", "register", "switch"].forEach((kind) => $(`#${kind}-form`).addEventListener("submit", (event) => submitAuth(event, kind)));
  $("#plan-form").addEventListener("submit", savePlan); ["budget", "reserve"].forEach((field) => $(`#plan-${field}`).addEventListener("input", previewBudget));
  $("#catalog-search").addEventListener("input", (event) => { state.search = event.target.value; renderCatalog(); });
  $("#risk-filter").addEventListener("change", (event) => { state.risk = event.target.value; renderCatalog(); }); $("#sort-filter").addEventListener("change", (event) => { state.sort = event.target.value; renderCatalog(); });
  $("#reset-filters").addEventListener("click", () => { state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default"; $("#catalog-search").value = ""; $("#risk-filter").value = "全部"; $("#sort-filter").value = "default"; renderCatalog(); });
  $("#admin-refresh").addEventListener("click", loadAdmin); $("#text-size").addEventListener("change", (event) => setTextSize(event.target.value));
  $("#modal-favorite").addEventListener("click", async () => { if (!state.activeProduct) return; const preference = state.preferences.find((row) => row.product_id === state.activeProduct.id); try { await behavior(state.activeProduct.id, preference?.favorite ? "unfavorite" : "favorite"); updateActionButtons(); showToast(preference?.favorite ? "已取消收藏" : "已保存收藏"); } catch (error) { showToast(error.message); } });
  $("#modal-avoid").addEventListener("click", async () => { if (!state.activeProduct) return; const preference = state.preferences.find((row) => row.product_id === state.activeProduct.id); try { await behavior(state.activeProduct.id, preference?.avoided ? "restore" : "avoid"); updateActionButtons(); showToast(preference?.avoided ? "已恢复关注" : "已设为不感兴趣，不再进入推荐"); } catch (error) { showToast(error.message); } });
  $("#modal-substitute").addEventListener("click", async (event) => { if (!state.activeProduct) return; const productId = state.activeProduct.id; event.target.disabled = true; try { const result = await api(`/api/substitutes?product_id=${productId}${state.activeProduct.selected_variant ? `&variant_id=${encodeURIComponent(state.activeProduct.selected_variant.id)}` : ""}`); if (state.activeProduct?.id === productId) $("#modal-substitute-result").innerHTML = substituteCard(result); } catch (error) { showToast(error.message); } finally { event.target.disabled = false; } });
}
function refreshCurrentView() { if (state.view === "home") loadHome(); if (state.view === "favorites") loadFavorites(); }
async function init() { bindEvents(); const size = localStorage.getItem("financial-text-size"); setTextSize(["100", "125", "150", "200"].includes(size) ? size : "100"); if (!state.token) return showLogin(); try { const result = await api("/api/auth/me"); enterApp(result.user, state.token); } catch (_) { showLogin(); } }
init();
