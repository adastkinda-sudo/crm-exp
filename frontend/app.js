const state = { customerId: "U001", products: [], recommendations: null };

const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];

async function api(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error(`请求失败：${response.status}`);
  return response.json();
}

function riskLabel(level) {
  return `R${level} ${["低风险", "稳健", "平衡", "进取", "高波动"][level - 1] || ""}`;
}

function showToast(message) {
  const toast = $("#toast");
  toast.textContent = message;
  toast.classList.add("show");
  window.clearTimeout(showToast.timer);
  showToast.timer = window.setTimeout(() => toast.classList.remove("show"), 2400);
}

function navigate(view) {
  $$(".nav-item").forEach((item) => item.classList.toggle("active", item.dataset.view === view));
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${view}`));
  if (view === "products") loadProducts();
  if (view === "rules") loadRules();
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function renderCustomerOptions(customers) {
  $("#customer-select").innerHTML = customers.map((customer) => `<option value="${customer.id}">${customer.name} · ${customer.tag}</option>`).join("");
  $("#customer-select").value = state.customerId;
}

function renderProfile(customer, meta) {
  const initials = customer.name.slice(0, 1);
  $("#profile-strip").innerHTML = `
    <div class="profile-main"><div class="profile-avatar">${initials}</div><div><strong>${customer.name}</strong><span>${customer.tag} · ${customer.summary}</span></div></div>
    <div class="profile-stats"><div><span>风险承受</span><strong>R${customer.risk_tolerance}</strong></div><div><span>投资期限</span><strong>${customer.preferred_term >= 365 ? `${Math.round(customer.preferred_term / 365)}年` : `${customer.preferred_term}天`}</strong></div><div><span>预算</span><strong>¥${Math.round(customer.investment_budget / 1000)}k</strong></div></div>`;
  $("#result-meta").textContent = `${meta.association_rule_count} 条有效规则 · 最近距离 ${meta.min_distance}`;
}

function renderRecommendations(payload) {
  state.recommendations = payload;
  renderProfile(payload.customer, payload.meta);
  $("#recommendation-list").innerHTML = payload.items.map((item, index) => `
    <article class="recommend-card">
      <img class="product-image" src="${item.image}" alt="${item.name}" />
      <div class="recommend-body"><div class="section-kicker">0${index + 1} / ${item.algorithm_source}</div><h3>${item.name}</h3><div class="product-meta">${item.issuer} · ${item.product_type} · ${riskLabel(item.risk_level)}</div><div class="tag-row"><span class="tag green">${item.term_days >= 365 ? `${Math.round(item.term_days / 365)}年期限` : `${item.term_days}天期限`}</span><span class="tag blue">${item.expected_return ? `${item.expected_return}% 演示收益` : "保障型产品"}</span></div><p class="recommend-reason">${item.reason}</p></div>
      <div class="score-box"><span>综合适配</span><strong>${Math.round(item.final_score * 100)}<small>%</small></strong><em>距离 ${item.distance}</em></div>
    </article>`).join("") || `<div class="empty-state">当前筛选条件下没有可推荐产品。</div>`;
  const best = payload.items[0];
  const meter = best ? Math.round(best.final_score * 100) : 0;
  $("#insight-card").innerHTML = best ? `<div class="section-kicker">WHY THIS MATCH</div><h3>${best.name}</h3><p>${best.reason}</p><div class="insight-meter"><span style="width:${meter}%"></span></div><div class="insight-foot"><span>关联 ${Math.round(best.association_score * 100)}%</span><span>相似 ${Math.round(best.similarity_score * 100)}%</span></div>` : `<div class="section-kicker">NO MATCH</div><h3>需要更多信息</h3><p>请调整客户画像或产品筛选条件。</p>`;
  if (payload.meta.fallback_used) showToast("已触发热门产品兜底，异常记录已写入后台。");
}

function productCard(item) {
  return `<article class="product-card"><img class="product-image" src="${item.image}" alt="${item.name}" /><h3>${item.name}</h3><div class="product-meta">${item.issuer} · ${item.product_type}</div><div class="tag-row"><span class="tag green">${riskLabel(item.risk_level)}</span><span class="tag">${item.status}</span></div><div class="product-footer"><strong>${item.expected_return ? `${item.expected_return}%` : "保障型"}</strong><button data-product="${item.id}">查看详情 →</button></div></article>`;
}

function openProductModal(product) {
  $("#modal-image").src = product.image;
  $("#modal-image").alt = product.name;
  $("#modal-title").textContent = product.name;
  $("#modal-description").textContent = product.description;
  $("#modal-meta").innerHTML = [["发行机构", product.issuer], ["产品类型", product.product_type], ["风险等级", riskLabel(product.risk_level)], ["投资期限", product.term_days >= 365 ? `${Math.round(product.term_days / 365)}年` : `${product.term_days}天`], ["最低金额", `¥${product.min_amount.toLocaleString()}`], ["演示收益", product.expected_return ? `${product.expected_return}%` : "保障型"]].map(([label, value]) => `<div><span>${label}</span><strong>${value}</strong></div>`).join("");
  $("#product-modal").classList.add("open");
  $("#product-modal").setAttribute("aria-hidden", "false");
}

function closeProductModal() {
  $("#product-modal").classList.remove("open");
  $("#product-modal").setAttribute("aria-hidden", "true");
}

async function loadProducts() {
  const query = new URLSearchParams({ search: $("#search-input").value, category: $("#category-filter").value, risk: $("#risk-filter").value, status: $("#status-filter").value });
  const payload = await api(`/api/products?${query}`);
  state.products = payload.items;
  $("#catalog-count").textContent = `${payload.total} 个产品`;
  $("#product-grid").innerHTML = payload.items.map(productCard).join("") || `<div class="empty-state">没有找到匹配产品。</div>`;
}

async function loadRules() {
  const payload = await api("/api/association-rules");
  $("#rules-body").innerHTML = payload.items.slice(0, 12).map((rule) => `<tr><td>${rule.product_a_name}</td><td>${rule.product_b_name}</td><td class="metric">${rule.support}</td><td class="metric">${rule.confidence}</td><td class="metric">${rule.interest}</td></tr>`).join("") || `<tr><td colspan="5">当前数据不足以生成有效规则。</td></tr>`;
}

async function loadRecommendations() {
  const payload = await api(`/api/recommendations?customer_id=${state.customerId}`);
  renderRecommendations(payload);
}

async function init() {
  try {
    const customers = await api("/api/customers");
    renderCustomerOptions(customers.items);
    await loadRecommendations();
    await loadProducts();
  } catch (error) {
    showToast(error.message);
  }
}

$$("[data-view]").forEach((item) => item.addEventListener("click", () => navigate(item.dataset.view)));
$("#customer-select").addEventListener("change", async (event) => {
  state.customerId = event.target.value;
  await loadRecommendations();
});
$("#jump-recommendations").addEventListener("click", () => document.querySelector("#profile-strip").scrollIntoView({ behavior: "smooth", block: "center" }));
$("#apply-filters").addEventListener("click", loadProducts);
$("#search-input").addEventListener("keydown", (event) => { if (event.key === "Enter") loadProducts(); });
document.addEventListener("click", (event) => {
  const button = event.target.closest("[data-product]");
  if (!button) return;
  const product = state.products.find((item) => String(item.id) === button.dataset.product) || state.recommendations?.items.find((item) => String(item.id) === button.dataset.product);
  if (product) openProductModal(product);
});
$("#modal-close").addEventListener("click", closeProductModal);
$("#product-modal").addEventListener("click", (event) => { if (event.target.id === "product-modal") closeProductModal(); });
document.addEventListener("keydown", (event) => { if (event.key === "Escape") closeProductModal(); });

init();
