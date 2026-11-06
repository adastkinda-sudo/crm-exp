const state = { products: [], recommendations: [], category: "全部", search: "", risk: "全部", sort: "default" };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`请求失败：${response.status}`);
  return response.json();
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2200);
}

function riskLabel(level) { return ["低风险", "稳健型", "平衡型", "进取型", "高风险"][level - 1] || ""; }
function termLabel(days) { return days >= 365 && days % 365 === 0 ? `${days / 365}年期限` : `${days}天期限`; }
function productHighlight(product) {
  if (product.category === "保险") return { value: "全年保障", label: "保障计划" };
  if (product.category === "信用卡") return { value: "消费权益", label: "服务权益" };
  if (product.category === "贷款") return { value: "灵活额度", label: "资金服务" };
  return { value: product.expected_return ? `${product.expected_return}%` : "—", label: "参考年化" };
}

function navigate(view) {
  $$(".nav-link").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  if (view === "products") loadCatalog();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function productCard(product) {
  const highlight = productHighlight(product);
  return `<article class="product-card"><img class="product-image" src="${product.image}" alt="${product.name}" /><div class="product-card-body"><div class="product-card-top"><h3 title="${product.name}">${product.name}</h3></div><div class="product-category">${product.issuer} · ${product.product_type}</div><div class="product-tags"><span class="product-tag risk">${riskLabel(product.risk_level)}</span><span class="product-tag">${termLabel(product.term_days)}</span></div><div class="product-card-bottom"><div class="product-rate"><strong>${highlight.value}</strong><small>${highlight.label} · ${product.min_amount ? `¥${product.min_amount.toLocaleString()} 起投` : "门槛灵活"}</small></div><button type="button" data-product="${product.id}">查看详情</button></div></div></article>`;
}

function openProductModal(product) {
  const highlight = productHighlight(product);
  $("#modal-image").src = product.image;
  $("#modal-image").alt = product.name;
  $("#modal-category").textContent = `${product.category} · ${product.issuer}`;
  $("#modal-title").textContent = product.name;
  $("#modal-description").textContent = product.description;
  $("#modal-meta").innerHTML = [["风险等级", riskLabel(product.risk_level)], ["投资期限", termLabel(product.term_days)], [highlight.label, highlight.value], ["起投金额", product.min_amount ? `¥${product.min_amount.toLocaleString()}` : "门槛灵活"]].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  $("#product-modal").classList.add("open");
  $("#product-modal").setAttribute("aria-hidden", "false");
}

function closeProductModal() { $("#product-modal").classList.remove("open"); $("#product-modal").setAttribute("aria-hidden", "true"); }

function navigateCategory(category) {
  state.category = category;
  state.search = "";
  $("#catalog-search").value = "";
  $$("[data-category-filter]").forEach((button) => button.classList.toggle("active", button.dataset.categoryFilter === category));
  navigate("products");
}

function renderCatalog(products) {
  const sorted = [...products];
  if (state.sort === "return") sorted.sort((a, b) => b.expected_return - a.expected_return);
  if (state.sort === "risk") sorted.sort((a, b) => a.risk_level - b.risk_level);
  $("#catalog-count").textContent = `${sorted.length} 个产品`;
  $("#catalog-label").textContent = state.category === "全部" ? "全部产品" : state.category;
  $("#catalog-grid").innerHTML = sorted.map(productCard).join("") || `<div class="empty-state">暂未找到匹配的产品。</div>`;
}

async function loadCatalog() {
  const query = new URLSearchParams({ search: state.search, category: state.category, risk: state.risk, status: "全部" });
  try { const payload = await api(`/api/products?${query}`); state.products = payload.items; renderCatalog(state.products); } catch (error) { showToast(error.message); }
}

async function loadHome() {
  try {
    const recommendationPayload = await api("/api/recommendations?customer_id=U001");
    state.recommendations = recommendationPayload.items;
    $("#recommendation-grid").innerHTML = state.recommendations.map(productCard).join("");
    const productPayload = await api("/api/products?category=全部&risk=全部&status=全部");
    state.products = productPayload.items;
    const popular = [...productPayload.items].sort((a, b) => (b.expected_return || 0) - (a.expected_return || 0)).slice(0, 4);
    $("#popular-grid").innerHTML = popular.map(productCard).join("");
  } catch (error) { showToast(error.message); }
}

function resetFilters() {
  state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default";
  $("#catalog-search").value = ""; $("#risk-filter").value = "全部"; $("#sort-filter").value = "default";
  $$("[data-category-filter]").forEach((button) => button.classList.toggle("active", button.dataset.categoryFilter === "全部"));
  loadCatalog();
}

function bindEvents() {
  $$('[data-view="home"], [data-view="products"]').forEach((item) => item.addEventListener("click", (event) => { event.preventDefault(); navigate(item.dataset.view); }));
  $$('[data-category], [data-category-filter]').forEach((item) => item.addEventListener("click", () => navigateCategory(item.dataset.category || item.dataset.categoryFilter)));
  $("#explore-products").addEventListener("click", () => navigate("products"));
  $("#catalog-search").addEventListener("input", (event) => { state.search = event.target.value; loadCatalog(); });
  $("#risk-filter").addEventListener("change", (event) => { state.risk = event.target.value; loadCatalog(); });
  $("#sort-filter").addEventListener("change", (event) => { state.sort = event.target.value; loadCatalog(); });
  $("#reset-filters").addEventListener("click", resetFilters);
  $("#global-search").addEventListener("keydown", (event) => { if (event.key === "Enter") { state.search = event.target.value.trim(); navigate("products"); } });
  $("#service-button").addEventListener("click", () => showToast("在线客服将在工作时间为您服务"));
  document.addEventListener("click", (event) => { const button = event.target.closest("[data-product]"); if (!button) return; const product = [...state.products, ...state.recommendations].find((item) => String(item.id) === button.dataset.product); if (product) openProductModal(product); });
  $("#modal-close").addEventListener("click", closeProductModal);
  $("#product-modal").addEventListener("click", (event) => { if (event.target.id === "product-modal") closeProductModal(); });
  $("#modal-primary").addEventListener("click", () => showToast("产品详情已为您保留"));
  $("#modal-favorite").addEventListener("click", (event) => { event.currentTarget.textContent = "♥ 已收藏"; showToast("已加入收藏"); });
  document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeProductModal(); });
}

async function init() { bindEvents(); await loadHome(); }
init();
