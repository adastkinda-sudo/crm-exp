const state = { products: [], recommendations: [], currentUser: null, token: localStorage.getItem("financial-session-token"), category: "全部", search: "", risk: "全部", sort: "default", switchUsername: "" };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (options.body) headers["Content-Type"] = "application/json";
  const response = await fetch(path, { ...options, headers });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(payload.error || `请求失败：${response.status}`);
  return payload;
}

function showToast(message) { const toast = $("#toast"); toast.textContent = message; toast.classList.add("show"); window.clearTimeout(showToast.timer); showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2200); }
function riskLabel(level) { return ["低风险", "稳健型", "平衡型", "进取型", "高风险"][level - 1] || ""; }
function escapeHTML(value) { return String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character])); }
function termLabel(days) { if (days === null) return "期限未披露"; if (days <= 1) return "申赎安排见资料"; return `${days}天`; }
function amountLabel(product) { if (product.min_amount === null || /起[投购]金额[^；。]*未(?:披露|列明)/.test(product.notes || "")) return "起投金额待确认"; return `¥${Number(product.min_amount).toLocaleString()} 起投`; }
function roleLabel(role) { return role === "admin" ? "管理员账号" : "客户账号"; }
function productHighlight(product) {
  if (product.category === "银行理财") return { value: "查看基准", label: "业绩比较基准" };
  const value = product.expected_return_mid;
  return { value: value === null || value === undefined ? "未披露" : `${Number(value).toFixed(2)}%`, label: "近1年历史收益" };
}

function productVisual(product) {
  return `<div class="product-visual ${product.category === "银行理财" ? "visual-bank" : "visual-fund"}"><span class="visual-category">${escapeHTML(product.category)}</span><span class="visual-symbol" aria-hidden="true">${product.category === "银行理财" ? "理" : "基"}</span><strong>${escapeHTML(product.issuer)}</strong><small>${escapeHTML(product.code)}</small></div>`;
}

function renderAccount(user) {
  $("#account-avatar").textContent = user.name.slice(0, 1);
  $("#account-name").textContent = user.name;
  $("#account-role").textContent = roleLabel(user.role);
  $("#admin-nav").classList.toggle("is-hidden", user.role !== "admin");
}

function enterApp(user, token) {
  state.currentUser = user;
  state.token = token;
  localStorage.setItem("financial-session-token", token);
  localStorage.setItem("financial-session-user", JSON.stringify(user));
  $("#login-screen").classList.add("is-hidden");
  $("#app-shell").classList.remove("is-hidden");
  renderAccount(user);
  navigate(user.role === "admin" ? "admin" : "home");
}

function showLogin() {
  state.currentUser = null; state.token = null;
  localStorage.removeItem("financial-session-token"); localStorage.removeItem("financial-session-user");
  $("#app-shell").classList.add("is-hidden"); $("#login-screen").classList.remove("is-hidden");
  $("#login-username").focus();
}

function navigate(view) {
  if (view === "admin" && state.currentUser?.role !== "admin") return navigate("home");
  $$(".nav-link").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  if (view === "products") loadCatalog();
  if (view === "admin") loadAdmin();
  if (view === "home") loadHome();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function productCard(product) {
  const highlight = productHighlight(product);
  return `<article class="product-card">${productVisual(product)}<div class="product-card-body"><div class="product-card-top"><h3 title="${escapeHTML(product.name)}">${escapeHTML(product.name)}</h3></div><div class="product-category">${escapeHTML(product.issuer)} · ${escapeHTML(product.product_type)}</div><div class="product-tags"><span class="product-tag risk">R${product.risk_level} ${riskLabel(product.risk_level)}</span><span class="product-tag">${termLabel(product.term_days)}</span></div><p class="product-return-text">${escapeHTML(product.expected_return_text)}</p><div class="product-card-bottom"><div class="product-rate"><strong>${highlight.value}</strong><small>${highlight.label}</small><small>${amountLabel(product)}</small></div><button type="button" data-product="${product.id}">查看详情</button></div><div class="product-as-of">资料日期 ${escapeHTML(product.as_of)} · ${escapeHTML(product.status)}</div></div></article>`;
}

function openProductModal(product) {
  $("#modal-visual").innerHTML = productVisual(product); $("#modal-category").textContent = `${product.category} · ${product.issuer}`; $("#modal-title").textContent = product.name; $("#modal-description").textContent = product.description;
  $("#modal-meta").innerHTML = [["风险等级", `R${product.risk_level} ${riskLabel(product.risk_level)}`], ["期限 / 持有安排", termLabel(product.term_days)], ["产品状态", product.status], ["起投金额", amountLabel(product)], ["产品代码", product.code]].map(([label, value]) => `<div><span>${label}</span><strong>${escapeHTML(value)}</strong></div>`).join("");
  $("#modal-return").textContent = `${product.return_basis}：${product.expected_return_text}`;
  $("#modal-notes").textContent = product.notes || "";
  $("#modal-as-of").textContent = `资料日期：${product.as_of} · 来源：${product.data_source}`;
  $("#modal-primary").href = product.source_url;
  $("#product-modal").classList.add("open"); $("#product-modal").setAttribute("aria-hidden", "false");
}
function closeProductModal() { $("#product-modal").classList.remove("open"); $("#product-modal").setAttribute("aria-hidden", "true"); }

function navigateCategory(category) { state.category = category; state.search = ""; $("#catalog-search").value = ""; $$("[data-category-filter]").forEach((button) => button.classList.toggle("active", button.dataset.categoryFilter === category)); navigate("products"); }

function renderCatalog(products) {
  const sorted = [...products]; if (state.sort === "amount") sorted.sort((a, b) => (amountLabel(a).includes("待确认") ? Infinity : a.min_amount) - (amountLabel(b).includes("待确认") ? Infinity : b.min_amount)); if (state.sort === "risk") sorted.sort((a, b) => a.risk_level - b.risk_level);
  $("#catalog-count").textContent = `${sorted.length} 个产品`; $("#catalog-label").textContent = state.category === "全部" ? "全部产品" : state.category; $("#catalog-grid").innerHTML = sorted.map(productCard).join("") || `<div class="empty-state">暂未找到匹配的产品。</div>`;
}

async function loadCatalog() {
  const query = new URLSearchParams({ search: state.search, category: state.category, risk: state.risk, status: "全部" });
  try { const payload = await api(`/api/products?${query}`); state.products = payload.items; renderCatalog(state.products); } catch (error) { showToast(error.message); }
}

async function loadHome() {
  try {
    const customerId = state.currentUser?.customer_id || "U001";
    const recommendationPayload = await api(`/api/recommendations?customer_id=${customerId}`); state.recommendations = recommendationPayload.items; $("#recommendation-grid").innerHTML = state.recommendations.map(productCard).join("");
    const productPayload = await api("/api/products?category=全部&risk=全部&status=全部"); state.products = productPayload.items;
    const selectedIds = new Set(state.recommendations.map((item) => item.id));
    const popular = state.products.filter((item) => !selectedIds.has(item.id)).slice(0, 4); $("#popular-grid").innerHTML = popular.map(productCard).join("");
    const hero = state.recommendations[0] || state.products[0];
    if (hero) {
      const highlight = productHighlight(hero);
      $("#hero-product").innerHTML = `<div class="hero-feature-top"><span class="feature-label">为你精选</span><span class="feature-index">${escapeHTML(hero.category)}</span></div>${productVisual(hero)}<div class="hero-feature-info"><div><span class="feature-category">${escapeHTML(hero.issuer)}</span><h2>${escapeHTML(hero.name)}</h2></div><div class="feature-rate"><strong>${highlight.value}</strong><small>${highlight.label}</small></div></div><div class="hero-feature-footer"><span>R${hero.risk_level} ${riskLabel(hero.risk_level)}</span><span>${termLabel(hero.term_days)}</span><button data-product="${hero.id}">查看详情 <b>↗</b></button></div>`;
      $("#hero-product").classList.remove("is-hidden");
    }
  } catch (error) { showToast(error.message); }
}

async function loadAdmin() {
  try {
    const payload = await api("/api/admin/overview");
    $("#admin-stats").innerHTML = [["商品总数", payload.stats.products], ["明确开放或在售", payload.stats.on_sale], ["客户账号", payload.stats.customers], ["行为记录", payload.stats.behaviors]].map(([label, value]) => `<div class="admin-stat"><span>${label}</span><strong>${value}</strong></div>`).join("");
    $("#admin-products").innerHTML = payload.products.map((item) => `<tr><td>${item.name}</td><td>${item.issuer}</td><td>${item.category}</td><td>R${item.risk_level}</td><td>${item.status}</td></tr>`).join("");
  } catch (error) { showToast(error.message); }
}

function resetFilters() { state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default"; $("#catalog-search").value = ""; $("#risk-filter").value = "全部"; $("#sort-filter").value = "default"; $$("[data-category-filter]").forEach((button) => button.classList.toggle("active", button.dataset.categoryFilter === "全部")); loadCatalog(); }

function openSwitchModal() {
  $("#account-menu").classList.remove("open"); $("#switch-error").textContent = ""; $("#switch-password").value = ""; $("#switch-modal").classList.add("open"); $("#switch-modal").setAttribute("aria-hidden", "false");
  api("/api/auth/users").then((payload) => { state.switchUsers = payload.items; state.switchUsername = payload.items[0]?.username || ""; $("#switch-user-list").innerHTML = payload.items.map((user, index) => `<button type="button" class="switch-user ${index === 0 ? "selected" : ""}" data-switch-username="${user.username}"><span class="switch-user-avatar">${user.name.slice(0, 1)}</span><span><strong>${user.name}</strong><small>${user.tag}</small></span><span class="switch-role">${roleLabel(user.role)}</span></button>`).join(""); }).catch((error) => { $("#switch-error").textContent = error.message; });
}
function closeSwitchModal() { $("#switch-modal").classList.remove("open"); $("#switch-modal").setAttribute("aria-hidden", "true"); }

function openRegisterModal() { $("#register-error").textContent = ""; $("#register-form").reset(); $("#register-risk").value = "2"; $("#register-budget").value = "10000"; $("#register-modal").classList.add("open"); $("#register-modal").setAttribute("aria-hidden", "false"); $("#register-name").focus(); }
function closeRegisterModal() { $("#register-modal").classList.remove("open"); $("#register-modal").setAttribute("aria-hidden", "true"); }

async function submitRegister(event) {
  event.preventDefault();
  $("#register-error").textContent = "";
  const password = $("#register-password").value;
  if (password !== $("#register-confirm").value) { $("#register-error").textContent = "两次输入的密码不一致"; return; }
  try {
    const payload = await api("/api/auth/register", { method: "POST", body: JSON.stringify({ name: $("#register-name").value.trim(), username: $("#register-username").value.trim(), password, risk_tolerance: Number($("#register-risk").value), investment_budget: Number($("#register-budget").value), preferred_term: 365 }) });
    closeRegisterModal(); enterApp(payload.user, payload.token); showToast("账号注册成功");
  } catch (error) { $("#register-error").textContent = error.message; }
}

async function submitSwitch() {
  const password = $("#switch-password").value; if (!state.switchUsername || !password) { $("#switch-error").textContent = "请选择账号并输入密码"; return; }
  try { const payload = await api("/api/auth/switch", { method: "POST", body: JSON.stringify({ username: state.switchUsername, password }) }); closeSwitchModal(); enterApp(payload.user, payload.token); showToast(`已切换至${payload.user.name}`); } catch (error) { $("#switch-error").textContent = error.message; }
}

function bindEvents() {
  $$('[data-view="home"], [data-view="products"], [data-view="admin"]').forEach((item) => item.addEventListener("click", (event) => { event.preventDefault(); navigate(item.dataset.view); }));
  $$('[data-category], [data-category-filter]').forEach((item) => item.addEventListener("click", () => navigateCategory(item.dataset.category || item.dataset.categoryFilter)));
  $("#explore-products").addEventListener("click", () => navigate("products")); $("#catalog-search").addEventListener("input", (event) => { state.search = event.target.value; loadCatalog(); }); $("#risk-filter").addEventListener("change", (event) => { state.risk = event.target.value; loadCatalog(); }); $("#sort-filter").addEventListener("change", (event) => { state.sort = event.target.value; loadCatalog(); }); $("#reset-filters").addEventListener("click", resetFilters); $("#admin-refresh").addEventListener("click", loadAdmin);
  $("#global-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { state.search = event.target.value.trim(); navigate("products"); } }); $("#service-button").addEventListener("click", () => showToast("在线客服将在工作时间为您服务"));
  $("#account-button").addEventListener("click", () => $("#account-menu").classList.toggle("open")); $("#switch-account").addEventListener("click", openSwitchModal); $("#logout-button").addEventListener("click", async () => { try { await api("/api/auth/logout", { method: "POST" }); } catch (_) {} showLogin(); }); $("#switch-close").addEventListener("click", closeSwitchModal); $("#switch-submit").addEventListener("click", submitSwitch);
  $("#switch-user-list").addEventListener("click", (event) => { const target = event.target.closest("[data-switch-username]"); if (!target) return; state.switchUsername = target.dataset.switchUsername; $$(".switch-user").forEach((item) => item.classList.toggle("selected", item === target)); });
  document.addEventListener("click", (event) => { const button = event.target.closest("[data-product]"); if (button) { const product = [...state.products, ...state.recommendations].find((item) => String(item.id) === button.dataset.product); if (product) openProductModal(product); } if (!event.target.closest(".account-wrap")) $("#account-menu").classList.remove("open"); });
  $("#modal-close").addEventListener("click", closeProductModal); $("#product-modal").addEventListener("click", (event) => { if (event.target.id === "product-modal") closeProductModal(); }); $("#modal-favorite").addEventListener("click", (event) => { event.currentTarget.textContent = "♥ 已收藏"; showToast("已加入收藏"); }); $("#switch-modal").addEventListener("click", (event) => { if (event.target.id === "switch-modal") closeSwitchModal(); }); document.addEventListener("keydown", (event) => { if (event.key === "Escape") { closeProductModal(); closeSwitchModal(); closeRegisterModal(); } });
  $("#login-form").addEventListener("submit", async (event) => { event.preventDefault(); $("#login-error").textContent = ""; try { const payload = await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username: $("#login-username").value.trim(), password: $("#login-password").value }) }); enterApp(payload.user, payload.token); } catch (error) { $("#login-error").textContent = error.message; } }); $("#login-help").addEventListener("click", () => showToast("请联系平台客服获取账号支持")); $("#register-link").addEventListener("click", openRegisterModal); $("#register-close").addEventListener("click", closeRegisterModal); $("#register-form").addEventListener("submit", submitRegister); $("#register-modal").addEventListener("click", (event) => { if (event.target.id === "register-modal") closeRegisterModal(); });
}

async function init() {
  bindEvents();
  if (!state.token) { showLogin(); return; }
  try { const payload = await api("/api/auth/me"); enterApp(payload.user, state.token); } catch (_) { showLogin(); }
}

init();
