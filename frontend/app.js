const state = { products: [], preferences: [], currentUser: null, profile: null, recommendations: null, token: localStorage.getItem("financial-session-token"), category: "全部", search: "", risk: "全部", sort: "default", view: "home", tab: "mine", activeProduct: null, version: 0, request: 0, routeKey: "", inAppNavigation: false, returnTo: null };
const $ = (selector) => document.querySelector(selector);
const $$ = (selector) => [...document.querySelectorAll(selector)];
const escapeHTML = (value) => String(value ?? "").replace(/[&<>"']/g, (character) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[character]));
const isCustomer = () => state.currentUser?.role === "customer";
const VIEW_NAMES = { home: "首页", products: "产品中心", plan: "我的资金计划", favorites: "我的收藏", admin: "管理后台", detail: "商品详情" };
let modalOpener;
let helpCounter = 0;

/* ---------- 格式化：只用于展示 ---------- */
function formatNumber(value) { return Number(value).toLocaleString("zh-CN", { maximumFractionDigits: 2 }); }
function formatMoney(value) {
  if (value === null || value === undefined || value === "" || !Number.isFinite(Number(value))) return "资料未写明";
  const amount = Number(value);
  if (Math.abs(amount) >= 10000) return `${formatNumber(Math.round(amount / 100) / 100)} 万元`;
  return `${formatNumber(amount)} 元`;
}
function formatDate(value) {
  const match = /^(\d{4})-(\d{1,2})-(\d{1,2})/.exec(String(value || ""));
  return match ? `${Number(match[1])} 年 ${Number(match[2])} 月 ${Number(match[3])} 日` : String(value || "日期未写明");
}
function formatRate(value) { return `${Number(value).toFixed(2)}%`; }
function durationText(days) {
  if (days < 30) return `${days} 天`;
  const months = Math.round(days / 30.4);
  if (months < 12) return months === 6 ? "半年" : `${months} 个月`;
  const years = Math.floor(months / 12); const rest = months % 12;
  if (rest === 0) return `${years} 年`;
  return rest === 6 ? `${years} 年半` : `${years} 年 ${rest} 个月`;
}
function termText(days) {
  if (days === null || days === undefined) return "资料未写明，请向销售机构确认";
  if (days <= 1) return "一般可以随时申请取出（到账时间以资料为准）";
  const duration = durationText(days);
  return `约${/^\d/.test(duration) ? " " : ""}${duration}后可取出（${days} 天）`;
}
const RISK_WORDS = ["很低", "较低", "中等", "较高", "很高"];
function riskLabel(level) { return RISK_WORDS[level - 1] ? `风险${RISK_WORDS[level - 1]}` : "风险待确认"; }
function riskText(product) { return product.risk_level == null ? "风险等级待核实" : `${riskLabel(product.risk_level)}（R${product.risk_level}）`; }
function riskScale(level) {
  if (level == null) return "";
  return `<span class="risk-scale" aria-hidden="true">${[1, 2, 3, 4, 5].map((step) => `<i class="${step <= level ? "on" : ""}"></i>`).join("")}</span>`;
}
function amountUnknown(product) { return product.min_amount === null || product.min_amount === undefined || /起[投购]金额[^；。]*未(?:披露|列明)/.test(product.notes || ""); }
function amountLabel(product) { return amountUnknown(product) ? "资料未写明，请向销售机构确认" : formatMoney(product.min_amount); }
function returnInfo(product) {
  const variantNote = product.selected_variant ? `（${product.selected_variant.label}档）` : "（参考档）";
  if (product.is_simulated) return { label: "收益", value: null, note: "这是模拟资料，不能购买" };
  if (product.category === "保险") return { label: "收益", value: null, note: "保障和领取金额以保险合同为准" };
  const rate = product.expected_return_mid;
  if (rate == null) return product.category === "养老理财" ? { label: "参考收益（不是承诺）", value: null, note: "资料未写明具体数字，请看产品资料", help: "benchmark" } : { label: "收益", value: null, note: "资料未写明可以比较的收益" };
  if (product.category === "现金管理") return { label: "近 7 天折算的年收益", value: formatRate(rate), note: "资料快照，每天都会变化，不是承诺", help: "sevenDay" };
  if (product.product_type === "储蓄国债") return { label: "年利率", value: formatRate(rate), note: "国债票面利率" };
  if (/存款/.test(product.return_basis || "") || /存单/.test(product.product_type || "")) return { label: "年利率", value: formatRate(rate), note: `银行公告的存款利率${variantNote}，以银行为准` };
  return { label: "参考收益（不是承诺）", value: formatRate(rate), note: product.return_basis || "", help: "benchmark" };
}

/* ---------- 白话词表与「这是什么？」 ---------- */
const GLOSSARY = {
  risk: "销售机构给产品定的风险档次，从 R1（风险很低）到 R5（风险很高）。档次越高，本金亏损的可能越大。",
  term: "买入后大约要放多久才能把钱取回来，也叫持有期。提前取出可能少拿利息，有的产品不能提前取出。",
  minAmount: "买这款产品至少要投入的钱，也叫起购金额。",
  benchmark: "产品资料里写的参考收益，也叫业绩比较基准。它不是承诺，实际收益可能更低。",
  sevenDay: "把最近 7 天的收益折算成一年的比例，也叫七日年化。每天都会变化。",
  available: "您填写的资金减去应急备用金后，可以用来买产品的钱。",
  reserve: "留着应付看病、急用的钱，不拿去买产品，也叫应急预留。",
};
function helpParts(key, tipTag = "span") {
  const id = `help-tip-${++helpCounter}`;
  return [`<button type="button" class="help-button" data-help-toggle aria-expanded="false" aria-controls="${id}">这是什么？</button>`, `<${tipTag} class="help-tip" id="${id}" hidden>${escapeHTML(GLOSSARY[key])}</${tipTag}>`];
}
function helpButton(key) { return helpParts(key).join(""); }
function toggleHelp(button) {
  const tip = document.getElementById(button.getAttribute("aria-controls")); if (!tip) return;
  const open = button.getAttribute("aria-expanded") !== "true";
  button.setAttribute("aria-expanded", String(open)); tip.hidden = !open; button.textContent = open ? "收起解释" : "这是什么？";
}

/* ---------- 网络与反馈 ---------- */
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
function hideToast() { $("#toast").classList.remove("show"); clearTimeout(showToast.timer); }
function showToast(message) { const toast = $("#toast"); $("#toast-message").textContent = message; toast.classList.add("show"); clearTimeout(showToast.timer); showToast.timer = setTimeout(hideToast, 6000); }
function setDetailStatus(html) { $("#detail-status").innerHTML = html; }

/* ---------- 商品说明块 ---------- */
function productVisual(product, eager = false, decorative = false) { return `<div class="product-image-wrap"><img class="product-image" src="${escapeHTML(product.image)}" alt="${decorative ? "" : escapeHTML(product.image_alt || `${product.name}产品封面`)}" width="640" height="360" loading="${eager ? "eager" : "lazy"}" decoding="async" /><span class="image-fallback" aria-hidden="true">${escapeHTML(product.issuer)}</span></div>`; }
function emptyState(title, text, action = null) { return `<div class="empty-state"><p class="empty-title">${escapeHTML(title)}</p><p>${escapeHTML(text)}</p>${action ? `<button type="button" class="${action.primary ? "primary-button" : "secondary-button"}" ${action.attr}>${escapeHTML(action.label)}</button>` : ""}</div>`; }
function returnLine(product) {
  const info = returnInfo(product);
  return `<div class="pb-return"><p><span class="pb-return-label">${escapeHTML(info.label)}</span>${info.value ? ` <strong class="pb-return-num">${escapeHTML(info.value)}</strong>` : ""}</p>${info.note ? `<p class="pb-return-note">${escapeHTML(info.note)}</p>` : ""}</div>`;
}
function productCard(product, options = {}) {
  const assessment = product.assessment;
  const reasons = (assessment?.reasons || []).slice(0, 2).map(escapeHTML).join("；");
  let fit = "";
  if (assessment?.eligible) fit = `<div class="fit-note ok"><p class="fit-title">✓ 为什么适合您</p>${reasons ? `<p>${reasons}</p>` : ""}</div>`;
  else if (assessment && options.showCaution) fit = `<div class="fit-note warn"><p class="fit-title">⚠ 需要注意</p>${reasons ? `<p>${reasons}</p>` : ""}</div>`;
  const variantCount = product.variants?.length || 0;
  return `<article class="product-block"><div class="pb-thumb">${productVisual(product, false, true)}</div><div class="pb-body"><h3 class="pb-name">${escapeHTML(product.name)}${product.selected_variant ? `<span class="pb-variant"> · ${escapeHTML(product.selected_variant.label)}档</span>` : ""}</h3><p class="pb-issuer">${escapeHTML(product.issuer)} · ${escapeHTML(product.product_type)}</p><dl class="pb-facts"><div><dt>风险</dt><dd>${riskScale(product.risk_level)}${escapeHTML(riskText(product))}</dd></div><div><dt>多久能取出</dt><dd>${escapeHTML(termText(product.term_days))}</dd></div><div><dt>最少要投多少</dt><dd>${escapeHTML(amountLabel(product))}</dd></div></dl>${!product.selected_variant && variantCount > 1 ? `<p class="pb-more">有 ${variantCount} 个期限档可选，详细说明里可以逐一查看。</p>` : ""}${returnLine(product)}${fit}<button type="button" class="secondary-button pb-action" data-product="${product.id}" data-variant="${escapeHTML(product.selected_variant?.id || "")}">看详细说明<span class="visually-hidden">：${escapeHTML(product.name)}</span></button></div></article>`;
}
function substituteCard(item, inDetail = false) {
  const original = item.original;
  const replacement = item.replacement;
  const fits = Boolean(item.original_assessment.eligible);
  return `<article class="substitute-block"><div class="sub-part"><p class="sub-label">${inDetail ? "您正在看的" : "您看过的"}</p><h3 class="pb-name">${escapeHTML(original.name)}</h3><div class="fit-note ${fits ? "ok" : "warn"}"><p class="fit-title">${fits ? "✓ 符合您的计划" : "⚠ 不太合适的原因"}</p><p>${escapeHTML(item.original_assessment.reasons[0])}</p></div>${inDetail ? "" : `<button type="button" class="link-button" data-product="${original.id}">看这款的说明</button>`}</div><p class="sub-arrow" aria-hidden="true">↓</p><div class="sub-part">${replacement ? `<p class="sub-label">可以比较的同类产品</p><h3 class="pb-name">${escapeHTML(replacement.name)}</h3><p class="pb-issuer">${escapeHTML(riskText(replacement))} · 最少要投 ${escapeHTML(amountLabel(replacement))}</p><ul class="plain-list">${item.differences.map((difference) => `<li>${escapeHTML(difference)}</li>`).join("")}</ul><button type="button" class="secondary-button" data-product="${replacement.id}" data-variant="${escapeHTML(replacement.selected_variant?.id || "")}">看详细说明<span class="visually-hidden">：${escapeHTML(replacement.name)}</span></button>` : `<p class="sub-label">暂时没有合适的同类产品</p><p>${escapeHTML(item.message)}</p>`}</div></article>`;
}
function cautionCard(item) {
  return `<article class="product-block caution-block"><div class="pb-body"><div class="fit-note warn"><p class="fit-title">⚠ 请注意</p><p>${escapeHTML(item.assessment.reasons.join("；"))}</p></div><h3 class="pb-name">${escapeHTML(item.name)}</h3><p class="pb-issuer">${escapeHTML(item.issuer)} · ${escapeHTML(riskText(item))}</p><button type="button" class="secondary-button pb-action" data-product="${item.id}">看详细说明<span class="visually-hidden">：${escapeHTML(item.name)}</span></button></div></article>`;
}

/* ---------- 首页 ---------- */
function renderSummary(profile) {
  const greeting = state.currentUser ? `${state.currentUser.name}，您好` : "";
  $("#home-greeting").textContent = greeting;
  if (!profile?.plan_completed) {
    $("#home-title").textContent = "先说说您的资金打算";
    $("#plan-summary").innerHTML = `<p class="hero-lead">回答几个简单问题：这笔钱有多少、什么时候要用、能接受多大风险。我们再按您的情况为您挑选产品。</p><div class="hero-actions"><button type="button" class="primary-button" data-view="plan">先花 2 分钟填写资金计划</button></div>`;
    return;
  }
  const available = Math.max(0, profile.investment_budget - profile.emergency_reserve);
  $("#home-title").innerHTML = `您可以安排的钱<span class="hero-amount">${escapeHTML(formatMoney(available))}</span>`;
  $("#plan-summary").innerHTML = `<p class="hero-lead">已经扣除应急备用金。${helpButton("available")}</p>${profile.personal_pension_budget > 0 ? `<p class="hero-lead">其中个人养老金账户 ${escapeHTML(formatMoney(profile.personal_pension_budget))}，普通资金 ${escapeHTML(formatMoney(Math.max(0, available - profile.personal_pension_budget)))}，分别挑选。</p>` : ""}<div class="hero-actions"><button type="button" class="primary-button" data-view="products">看看适合我的产品</button><button type="button" class="link-button" data-view="plan">修改资金计划</button></div><dl class="ledger hero-ledger"><div><dt>应急备用金</dt><dd>${escapeHTML(formatMoney(profile.emergency_reserve))}</dd></div><div><dt>最早要用钱的日期</dt><dd>${escapeHTML(formatDate(profile.target_date))}</dd></div><div><dt>能接受的最高风险</dt><dd>${riskScale(profile.risk_tolerance)}${escapeHTML(riskLabel(profile.risk_tolerance))}（R${escapeHTML(profile.risk_tolerance)}）</dd></div><div><dt>本金亏损</dt><dd>${profile.can_accept_loss ? "可以接受一定亏损" : "不能接受"}</dd></div></dl>`;
}
async function fetchRecommendations(force = false) {
  if (state.recommendations && !force) return state.recommendations;
  const result = await api("/api/recommendations");
  state.recommendations = result; state.profile = result.customer;
  return result;
}
async function loadHome() {
  $("#substitute-section").classList.remove("is-hidden"); $("#caution-section").classList.remove("is-hidden");
  if (!isCustomer()) {
    $("#home-greeting").textContent = state.currentUser ? `${state.currentUser.name}，您好` : "";
    $("#home-title").textContent = "查看平台商品";
    $("#plan-summary").innerHTML = '<p class="hero-lead">个人资金计划与推荐请使用客户账号。</p><div class="hero-actions"><button type="button" class="primary-button" data-view="products">浏览全部产品</button></div>';
    $("#recommend-title").textContent = "目录预览"; $("#recommendation-caption").textContent = "平台中的前 3 个商品。";
    $("#recommendation-grid").innerHTML = state.products.slice(0, 3).map((product) => productCard(product)).join("");
    $("#substitute-section").classList.add("is-hidden"); $("#caution-section").classList.add("is-hidden"); return;
  }
  $("#recommend-title").textContent = "适合您";
  const version = state.version; const request = ++state.request;
  try {
    const result = await fetchRecommendations(true);
    if (version !== state.version || request !== state.request) return;
    renderSummary(result.customer);
    const complete = result.plan.profile_complete;
    $("#recommendation-caption").textContent = complete ? "按您能接受的风险、可以安排的钱和用钱时间挑选，最多 3 个。购买前仍需确认正式条款。" : "填好资金计划后，这里会列出适合您的产品。";
    $("#recommendation-grid").innerHTML = result.items.map((product) => productCard(product)).join("") || (complete ? emptyState("当前没有符合您计划的产品", "可以暂不选择。请留好应急备用金，按实际需要安排用钱。", { label: "看看全部产品", attr: 'data-view="products" data-tab-target="all"' }) : emptyState("还没有适合您的推荐", "填好资金计划后，这里会列出适合您的产品。", { label: "填写资金计划", attr: 'data-view="plan"' }));
    $("#substitute-grid").innerHTML = result.substitute_items.map(substituteCard).join("") || emptyState("暂时没有需要替代的产品", "您看过的产品不太合适时，这里会列出同类产品；也可以在产品详细说明里点「寻找同类替代」。");
    $("#caution-grid").innerHTML = result.caution_items.map(cautionCard).join("") || emptyState("暂时没有需要提醒的产品", "您看过、收藏的产品和计划不相符时，这里会说明原因。");
    if (!complete) {
      $("#substitute-section").classList.toggle("is-hidden", !result.substitute_items.length);
      $("#caution-section").classList.toggle("is-hidden", !result.caution_items.length);
    }
    restoreReturnPoint("home");
  } catch (error) { showToast(error.message); $("#recommendation-grid").innerHTML = emptyState("推荐暂时没能加载", "请稍后刷新页面再试。"); }
}

/* ---------- 产品中心 ---------- */
function setPressed(selector, matcher) { $$(selector).forEach((button) => button.setAttribute("aria-pressed", String(matcher(button)))); }
function renderProducts() {
  if (!isCustomer()) state.tab = "all";
  setPressed("[data-tab]", (button) => button.dataset.tab === state.tab);
  $("#products-mine").classList.toggle("is-hidden", state.tab !== "mine");
  $("#products-all").classList.toggle("is-hidden", state.tab !== "all");
  if (state.tab === "all") { renderCatalog(); restoreReturnPoint("products"); } else loadMine();
}
async function loadMine() {
  const version = state.version;
  $("#mine-caption").textContent = "正在按您的资金计划挑选…";
  try {
    const result = await fetchRecommendations();
    if (version !== state.version || state.view !== "products" || state.tab !== "mine") return;
    const plan = result.customer;
    if (!result.plan.profile_complete) {
      $("#mine-caption").textContent = "";
      $("#mine-grid").innerHTML = emptyState("还没有填写资金计划", "填好计划后，这里会列出适合您的产品。大约需要 2 分钟。", { label: "先花 2 分钟填写资金计划", attr: 'data-view="plan"', primary: true });
      return;
    }
    $("#mine-caption").textContent = `按您的计划挑选：可以安排 ${formatMoney(result.plan.available_budget)}，最早 ${formatDate(plan.target_date)} 要用钱，能接受的最高风险是${riskLabel(plan.risk_tolerance)}（R${plan.risk_tolerance}）。`;
    $("#mine-grid").innerHTML = result.items.length ? result.items.map((product) => productCard(product)).join("") + `<div class="list-footer"><p>想看更多产品？</p><button type="button" class="secondary-button" data-tab="all">看看全部产品</button></div>` : emptyState("当前没有符合您计划的产品", "可以暂不选择，也可以看看全部产品，了解规则后再决定。", { label: "看看全部产品", attr: 'data-tab="all"' });
    restoreReturnPoint("products");
  } catch (error) { showToast(error.message); $("#mine-grid").innerHTML = emptyState("推荐暂时没能加载", "请稍后刷新页面再试。"); }
}
function renderCatalog() {
  const search = state.search.trim().toLocaleLowerCase();
  let products = state.products.filter((product) => (state.category === "全部" || product.category === state.category) && (state.risk === "全部" || String(product.risk_level) === state.risk) && (!search || [product.name, product.full_name, product.code, product.issuer, product.product_type].some((value) => String(value || "").toLocaleLowerCase().includes(search))));
  if (state.sort === "risk") products.sort((a, b) => (a.risk_level ?? Infinity) - (b.risk_level ?? Infinity));
  if (state.sort === "amount") products.sort((a, b) => (amountUnknown(a) ? Infinity : a.min_amount) - (amountUnknown(b) ? Infinity : b.min_amount));
  setPressed("[data-category-filter]", (button) => button.dataset.categoryFilter === state.category);
  setPressed("[data-risk-filter]", (button) => button.dataset.riskFilter === state.risk);
  setPressed("[data-sort]", (button) => button.dataset.sort === state.sort);
  const filtered = state.category !== "全部" || state.risk !== "全部" || search;
  $("#catalog-count").textContent = filtered ? `找到 ${products.length} 个产品（共 ${state.products.length} 个）` : `共 ${products.length} 个产品`;
  $("#reset-filters").classList.toggle("is-hidden", !filtered && state.sort === "default");
  $("#catalog-grid").innerHTML = products.map((product) => productCard(product)).join("") || emptyState("没有找到符合条件的产品", "换个关键词试试，或者清除筛选条件。", { label: "清除筛选条件", attr: "data-reset-filters" });
}
function resetFilters() { state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default"; $("#catalog-search").value = ""; renderCatalog(); }

/* ---------- 资金计划、收藏、管理后台 ---------- */
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
function previewBudget() { const available = Math.max(0, Number($("#plan-budget").value) - Number($("#plan-reserve").value)); $("#plan-available").textContent = formatMoney(available); }
async function savePlan(event) {
  event.preventDefault(); $("#plan-error").textContent = "";
  const button = event.submitter; button.disabled = true;
  try {
    const result = await post("/api/profile", { retirement_status: $("#plan-retirement").value, pension_type: $("#plan-pension").value, personal_pension_status: $("#plan-pension-status").value, personal_pension_budget: Number($("#plan-pension-budget").value), investment_budget: Number($("#plan-budget").value), emergency_reserve: Number($("#plan-reserve").value), target_date: $("#plan-date").value, preferred_category: $("#plan-category").value, can_accept_loss: $("#plan-loss").value === "1", risk_tolerance: Number($("#plan-risk").value) });
    state.profile = result.customer; state.recommendations = null; showToast("资金计划已保存"); navigate("home");
  } catch (error) { $("#plan-error").textContent = error.message; } finally { button.disabled = false; }
}
async function loadFavorites() {
  const version = state.version;
  try { const data = await api("/api/preferences"); if (version !== state.version) return; state.preferences = data.items;
    const favorites = new Set(data.items.filter((item) => item.favorite).map((item) => item.product_id));
    const avoided = new Set(data.items.filter((item) => item.avoided).map((item) => item.product_id));
    $("#favorite-grid").innerHTML = state.products.filter((product) => favorites.has(product.id)).map((product) => productCard(product)).join("") || emptyState("还没有收藏", "在产品的详细说明里点「收藏」，留着以后慢慢比较。", { label: "去产品中心看看", attr: 'data-view="products"' });
    $("#avoided-list").innerHTML = state.products.filter((product) => avoided.has(product.id)).map((product) => `<div class="avoided-item"><span>${escapeHTML(product.name)}</span><button type="button" class="secondary-button" data-restore="${product.id}">恢复关注</button></div>`).join("") || emptyState("没有不感兴趣的商品", "在产品的详细说明里可以设置「不感兴趣」，也可以随时恢复。");
    restoreReturnPoint("favorites");
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

/* ---------- 弹窗（切换账号、注册） ---------- */
function openModal(id) { const modal = $(`#${id}`); if (!$(".modal-backdrop.open")) modalOpener = document.activeElement; modal.classList.add("open"); modal.setAttribute("aria-hidden", "false"); document.body.classList.add("modal-open"); const dialog = modal.querySelector("[role=dialog]"); dialog.scrollTop = 0; dialog.focus(); }
function closeModal(id) { const modal = $(`#${id}`); if (!modal.classList.contains("open")) return; modal.classList.remove("open"); modal.setAttribute("aria-hidden", "true"); if (!$(".modal-backdrop.open")) { document.body.classList.remove("modal-open"); modalOpener?.focus(); } }

/* ---------- 商品详情 ---------- */
function preferenceFor(productId) { return state.preferences.find((item) => item.product_id === productId); }
function updateActionButtons() {
  const preference = preferenceFor(state.activeProduct?.id);
  $("#detail-favorite").textContent = preference?.favorite ? "♥ 已收藏（点一下取消）" : "♡ 收藏";
  $("#detail-favorite").setAttribute("aria-pressed", String(Boolean(preference?.favorite)));
  $("#detail-avoid").textContent = preference?.avoided ? "恢复关注" : "不感兴趣";
}
async function behavior(productId, action) { const result = await post("/api/behaviors", { product_id: productId, action }); state.preferences = result.items; if (action !== "view") state.recommendations = null; }
function withVariant(product, variantId) {
  const variant = product.variants?.find((item) => item.id === variantId);
  if (!variant) return product;
  return { ...product, selected_variant: variant, term_days: variant.term_days, min_amount: variant.min_amount, expected_return_mid: variant.rate, expected_return_text: variant.rate == null ? "利率待确认" : formatRate(variant.rate), status: variant.status };
}
function renderVariants(product) {
  const section = $("#detail-variant-section");
  if (!product.variants?.length) { section.classList.add("is-hidden"); $("#detail-variants").innerHTML = ""; return; }
  section.classList.remove("is-hidden");
  $("#detail-variants").innerHTML = `<ul class="variant-list">${product.variants.map((v) => { const current = product.selected_variant?.id === v.id; return `<li class="variant-row${current ? " current" : ""}"><div class="variant-info"><p class="variant-name">${escapeHTML(v.label)}${current ? '<span class="variant-current">（正在看这一档）</span>' : ""}</p><p>年利率 ${v.rate == null ? "资料未写明" : escapeHTML(formatRate(v.rate))} · 最少要投 ${escapeHTML(formatMoney(v.min_amount))}</p><p class="small-note">${escapeHTML(v.status)}</p></div>${current ? "" : `<button type="button" class="secondary-button" data-select-variant="${escapeHTML(v.id)}">看这一档<span class="visually-hidden">：${escapeHTML(v.label)}</span></button>`}</li>`; }).join("")}</ul><p class="small-note">各期限档属于同一款产品；额度、提前支取和利息规则请向银行确认。</p>`;
}
function renderDetailNotFound() {
  state.activeProduct = null;
  $("#detail-body").innerHTML = emptyState("没有找到这个产品", "它可能已经下架，或者链接不完整。可以回到产品中心重新查看。", { label: "回到产品中心", attr: 'data-view="products"' });
  $("#detail-more").innerHTML = "";
}
let detailTemplate = "";
async function openProduct(productId, variantId = "") {
  if (!detailTemplate) detailTemplate = $("#detail-body").innerHTML;
  else if (!$("#detail-title")) $("#detail-body").innerHTML = detailTemplate;
  const base = state.products.find((item) => item.id === Number(productId));
  if (!base) return renderDetailNotFound();
  const product = withVariant(base, variantId);
  state.activeProduct = product; const version = state.version;
  stopSpeaking();
  $("#detail-visual").innerHTML = productVisual(product, true);
  $("#detail-title").textContent = `${product.name}${product.selected_variant ? ` · ${product.selected_variant.label}档` : ""}`;
  $("#detail-category").textContent = `${product.issuer} · ${product.category} · ${product.product_type}`;
  const info = returnInfo(product);
  $("#detail-meta").innerHTML = [
    ["风险", `${riskScale(product.risk_level)}${escapeHTML(riskText(product))}`, "risk"],
    ["多久能取出", escapeHTML(termText(product.term_days)), "term"],
    ["最少要投多少", escapeHTML(amountLabel(product)), "minAmount"],
    [info.label, `${info.value ? `<strong class="ledger-num">${escapeHTML(info.value)}</strong> ` : ""}${escapeHTML(info.note)}`, info.help],
    ["资料中的状态", escapeHTML(product.status)],
    ["产品代码", escapeHTML(product.code || "资料未写明")],
  ].map(([label, value, help]) => { const [button, tip] = help ? helpParts(help, "dd") : ["", ""]; return `<div><dt>${escapeHTML(label)}${button ? ` ${button}` : ""}</dt><dd>${value}</dd>${tip}</div>`; }).join("");
  renderVariants(product);
  $("#detail-description").textContent = product.description || "";
  $("#detail-notes").textContent = product.notes && product.notes !== product.description ? product.notes : "";
  $("#detail-notes").classList.toggle("is-hidden", !$("#detail-notes").textContent);
  $("#detail-as-of").textContent = `资料日期：${formatDate(product.as_of)} · 来源：${product.data_source}${product.return_basis ? ` · 收益口径：${product.return_basis}` : ""}`;
  const hasSource = Boolean(product.source_url) && !product.is_simulated;
  $("#detail-primary").classList.toggle("is-hidden", !hasSource);
  if (hasSource) $("#detail-primary").href = product.source_url; else $("#detail-primary").removeAttribute("href");
  $("#detail-speak").classList.toggle("is-hidden", !("speechSynthesis" in window));
  $("#detail-more").innerHTML = `<button type="button" class="link-button" data-category="${escapeHTML(product.category)}">看全部「${escapeHTML(product.category)}」产品</button>`;
  $("#detail-substitute-result").innerHTML = ""; setDetailStatus("");
  const verdict = $("#detail-assessment"); verdict.className = "verdict";
  verdict.innerHTML = isCustomer() ? "<p>正在核对您的资金计划…</p>" : "<p>请使用客户账号核对个人资金计划。</p>";
  updateActionButtons();
  if (!isCustomer()) return;
  try {
    await behavior(product.id, "view"); const result = await api(`/api/product-assessment?product_id=${product.id}${product.selected_variant ? `&variant_id=${encodeURIComponent(product.selected_variant.id)}` : ""}`);
    if (version !== state.version || state.activeProduct?.id !== product.id) return;
    const assessment = result.assessment; state.activeProduct.assessment = assessment;
    verdict.classList.add(assessment.eligible ? "ok" : "warn");
    verdict.innerHTML = `<p class="verdict-title">${assessment.eligible ? "✓ 符合您的计划" : "⚠ 请注意：和您的计划不太相符"}</p><p class="verdict-status">核对结果：${escapeHTML(assessment.status)}</p>${assessment.selected_variant ? `<p>适合您的期限档：${escapeHTML(assessment.selected_variant.label)}，最少要投 ${escapeHTML(formatMoney(assessment.selected_variant.min_amount))}</p>` : ""}<p class="verdict-why">${assessment.eligible ? "理由：" : "原因："}</p><ul class="plain-list">${assessment.reasons.map((reason) => `<li>${escapeHTML(reason)}</li>`).join("")}</ul>${assessment.codes.includes("profile") ? '<button type="button" class="secondary-button" data-view="plan">完善资金计划</button>' : ""}`;
  } catch (error) { verdict.innerHTML = `<p>${escapeHTML(error.message)}</p>`; }
}
function speechText() {
  const product = state.activeProduct; if (!product) return "";
  const info = returnInfo(product);
  const verdict = product.assessment ? `${product.assessment.eligible ? "符合您的计划" : "和您的计划不太相符"}。${product.assessment.reasons.join("。")}。` : "";
  return [`${product.name}，${product.issuer}。`, verdict, `风险：${riskText(product)}。`, `多久能取出：${termText(product.term_days)}。`, `最少要投多少：${amountLabel(product)}。`, `${info.label}：${info.value || ""}，${info.note}。`].join("");
}
function stopSpeaking() { if ("speechSynthesis" in window) window.speechSynthesis.cancel(); const button = $("#detail-speak"); if (button) button.textContent = "🔊 读给我听"; }
function toggleSpeaking() {
  if (!("speechSynthesis" in window)) return;
  if (window.speechSynthesis.speaking) { stopSpeaking(); return; }
  const utterance = new SpeechSynthesisUtterance(speechText()); utterance.lang = "zh-CN"; utterance.rate = 0.9;
  utterance.onend = () => { $("#detail-speak").textContent = "🔊 读给我听"; };
  $("#detail-speak").textContent = "■ 停止朗读"; window.speechSynthesis.speak(utterance);
}

/* ---------- 路由：hash 记录视图与商品详情 ---------- */
function parseRoute() {
  const parts = location.hash.replace(/^#\/?/, "").split("/").filter(Boolean).map((part) => { try { return decodeURIComponent(part); } catch (_) { return part; } });
  if (parts[0] === "product" && parts[1]) return { view: "detail", productId: parts[1], variantId: parts[2] || "" };
  if (parts[0] === "products") return { view: "products", tab: parts[1] === "all" ? "all" : "mine" };
  return { view: VIEW_NAMES[parts[0]] && parts[0] !== "detail" ? parts[0] : "home" };
}
function defaultView() { return state.currentUser?.role === "admin" ? "admin" : "home"; }
function routeHash(view, extra = "") { return `#/${view}${extra}`; }
function go(hash, replace = false) {
  if (location.hash === hash) { renderRoute(); return; }
  if (replace) location.replace(hash); else location.hash = hash;
}
function navigate(view, options = {}) {
  if (view === "products") { const tab = options.tab || (isCustomer() ? "mine" : "all"); go(routeHash("products", tab === "all" ? "/all" : ""), options.replace); return; }
  go(routeHash(view), options.replace);
}
function openProductRoute(productId, variantId = "") {
  state.returnTo = { key: state.routeKey, y: window.scrollY, productId: String(productId) };
  go(`#/product/${encodeURIComponent(productId)}${variantId ? `/${encodeURIComponent(variantId)}` : ""}`);
}
function restoreReturnPoint(view) {
  const point = state.returnTo;
  if (!point || point.key !== state.routeKey || state.view !== view) return;
  state.returnTo = null;
  window.scrollTo(0, point.y);
  const button = $(`#view-${view} [data-product="${CSS.escape(point.productId)}"]`); button?.focus({ preventScroll: true });
}
function renderRoute() {
  if (!state.currentUser) return;
  const route = parseRoute();
  const key = location.hash || "#/home";
  if ((route.view === "admin" && isCustomer()) || (["plan", "favorites"].includes(route.view) && !isCustomer())) { navigate(defaultView(), { replace: true }); return; }
  const previousView = state.view;
  if (state.returnTo && route.view !== "detail" && state.returnTo.key !== key) state.returnTo = null;
  state.routeKey = key; state.view = route.view;
  if (previousView === "detail" && route.view !== "detail") stopSpeaking();
  $$(".nav-link").forEach((item) => { const active = item.dataset.view === (route.view === "detail" ? "products" : route.view); item.classList.toggle("active", active); if (active) item.setAttribute("aria-current", "page"); else item.removeAttribute("aria-current"); });
  $$(".view").forEach((item) => item.classList.toggle("active", item.id === `view-${route.view}`));
  document.title = `${VIEW_NAMES[route.view]} · 金融产品推荐系统`;
  const restoring = state.returnTo && state.returnTo.key === key;
  if (!restoring) window.scrollTo({ top: 0, behavior: "instant" });
  if (route.view === "home") loadHome();
  if (route.view === "products") { state.tab = route.tab; renderProducts(); }
  if (route.view === "plan") loadPlan();
  if (route.view === "favorites") loadFavorites();
  if (route.view === "admin") loadAdmin();
  if (route.view === "detail") { openProduct(route.productId, route.variantId); $("#detail-title")?.focus({ preventScroll: true }); }
  else if (!restoring) focusLostToHeading(route.view);
}
function focusLostToHeading(view) {
  const active = document.activeElement;
  if (active && active !== document.body && active.getClientRects().length) return;
  const heading = $(`#view-${view} h1`); if (!heading) return;
  heading.setAttribute("tabindex", "-1"); heading.focus({ preventScroll: true });
}
function goBack() { if (state.inAppNavigation) history.back(); else navigate("products"); }

/* ---------- 登录与账号 ---------- */
async function enterApp(user, token, keepRoute = false) {
  state.version += 1; state.currentUser = user; state.token = token; state.profile = null; state.recommendations = null; state.products = []; state.preferences = []; state.activeProduct = null; state.returnTo = null;
  state.category = "全部"; state.search = ""; state.risk = "全部"; state.sort = "default";
  $("#catalog-search").value = "";
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
    if (keepRoute && location.hash.length > 2) renderRoute(); else navigate(defaultView(), { replace: true });
  } catch (error) { showToast(error.message); }
}
function showLogin() {
  state.version += 1; state.currentUser = null; state.token = null; state.products = []; state.preferences = []; state.profile = null; state.recommendations = null;
  stopSpeaking(); document.title = "金融产品推荐系统";
  localStorage.removeItem("financial-session-token"); localStorage.removeItem("financial-session-user");
  $$(".modal-backdrop.open").forEach((modal) => closeModal(modal.id));
  $("#account-menu").classList.remove("open"); $("#app-shell").classList.add("is-hidden"); $("#login-screen").classList.remove("is-hidden"); $("#login-password").value = ""; $("#login-username").focus();
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
    await enterApp(result.user, result.token, kind === "login"); if (kind === "register") { navigate("plan"); showToast("账号已创建，请填写您的资金计划"); }
  } catch (error) { errorNode.textContent = error.message; } finally { button.disabled = false; }
}

/* ---------- 字号 ---------- */
const TEXT_SIZES = ["100", "150", "200"];
function nearestTextSize(value) {
  const number = Number(value);
  if (!Number.isFinite(number)) return "100";
  return TEXT_SIZES.reduce((best, size) => (Math.abs(Number(size) - number) <= Math.abs(Number(best) - number) ? size : best), "100");
}
function setTextSize(value) {
  const size = nearestTextSize(value);
  document.documentElement.style.fontSize = `${16 * Number(size) / 100}px`;
  document.documentElement.classList.toggle("large-text", Number(size) > 100);
  document.documentElement.dataset.textSize = size;
  try { localStorage.setItem("financial-text-size", size); } catch (_) { /* 存储不可用时只在本页生效 */ }
  setPressed("button[data-text-size]", (button) => button.dataset.textSize === size);
}

/* ---------- 事件 ---------- */
async function toggleFavorite() {
  if (!state.activeProduct) return; const product = state.activeProduct; const preference = preferenceFor(product.id);
  try { await behavior(product.id, preference?.favorite ? "unfavorite" : "favorite"); updateActionButtons(); const message = preference?.favorite ? "已取消收藏" : "已收藏，可以在「我的收藏」里找到"; setDetailStatus(`✓ ${escapeHTML(message)}`); showToast(message); } catch (error) { setDetailStatus(`✕ ${escapeHTML(error.message)}`); showToast(error.message); }
}
async function toggleAvoid() {
  if (!state.activeProduct) return; const product = state.activeProduct; const preference = preferenceFor(product.id);
  try {
    if (preference?.avoided) { await behavior(product.id, "restore"); updateActionButtons(); setDetailStatus("✓ 已恢复关注，这款产品会重新参与推荐"); showToast("已恢复关注"); return; }
    await behavior(product.id, "avoid"); updateActionButtons();
    setDetailStatus('✓ 已移出推荐 · <button type="button" class="link-button" id="detail-undo">撤销</button>'); showToast("已移出推荐。点错了可以点按钮下方的「撤销」");
  } catch (error) { setDetailStatus(`✕ ${escapeHTML(error.message)}`); showToast(error.message); }
}
async function undoAvoid() {
  if (!state.activeProduct) return;
  try { await behavior(state.activeProduct.id, "restore"); updateActionButtons(); setDetailStatus("✓ 已撤销，这款产品会重新参与推荐"); showToast("已撤销「不感兴趣」"); $("#detail-avoid").focus(); } catch (error) { setDetailStatus(`✕ ${escapeHTML(error.message)}`); showToast(error.message); }
}
async function findSubstitute(button) {
  if (!state.activeProduct) return; const productId = state.activeProduct.id; button.disabled = true; setDetailStatus("正在寻找同类产品…");
  try { const result = await api(`/api/substitutes?product_id=${productId}${state.activeProduct.selected_variant ? `&variant_id=${encodeURIComponent(state.activeProduct.selected_variant.id)}` : ""}`); if (state.activeProduct?.id === productId) { $("#detail-substitute-result").innerHTML = substituteCard(result, true); setDetailStatus(result.replacement ? "✓ 找到了一款同类产品，见下方" : "暂时没有合适的同类产品，见下方说明"); } } catch (error) { setDetailStatus(`✕ ${escapeHTML(error.message)}`); showToast(error.message); } finally { button.disabled = false; }
}
function bindEvents() {
  document.addEventListener("click", async (event) => {
    const helpToggle = event.target.closest("[data-help-toggle]"); if (helpToggle) { toggleHelp(helpToggle); return; }
    const sizeButton = event.target.closest("button[data-text-size]"); if (sizeButton) { setTextSize(sizeButton.dataset.textSize); return; }
    const tabButton = event.target.closest("[data-tab]"); if (tabButton) { navigate("products", { tab: tabButton.dataset.tab }); return; }
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) { event.preventDefault(); $("#account-menu").classList.remove("open"); navigate(viewButton.dataset.view, { tab: viewButton.dataset.tabTarget }); return; }
    const categoryButton = event.target.closest("[data-category]");
    if (categoryButton) { state.category = categoryButton.dataset.category; state.risk = "全部"; state.search = ""; $("#catalog-search").value = ""; navigate("products", { tab: "all" }); return; }
    const filterButton = event.target.closest("[data-category-filter], [data-risk-filter], [data-sort]");
    if (filterButton) { if (filterButton.dataset.categoryFilter) state.category = filterButton.dataset.categoryFilter; if (filterButton.dataset.riskFilter) state.risk = filterButton.dataset.riskFilter; if (filterButton.dataset.sort) state.sort = filterButton.dataset.sort; renderCatalog(); return; }
    if (event.target.closest("[data-reset-filters]")) { resetFilters(); $("#catalog-search").focus(); return; }
    const variantButton = event.target.closest("[data-select-variant]");
    if (variantButton && state.activeProduct) { go(`#/product/${state.activeProduct.id}/${encodeURIComponent(variantButton.dataset.selectVariant)}`, true); return; }
    const productButton = event.target.closest("[data-product]"); if (productButton) { openProductRoute(productButton.dataset.product, productButton.dataset.variant); return; }
    if (event.target.closest("#detail-undo")) { undoAvoid(); return; }
    const closeButton = event.target.closest("[data-close]"); if (closeButton) closeModal(closeButton.dataset.close);
    if (!event.target.closest(".account-wrap")) { $("#account-menu").classList.remove("open"); $("#account-button").setAttribute("aria-expanded", "false"); }
    const restoreButton = event.target.closest("[data-restore]"); if (restoreButton) { try { await behavior(Number(restoreButton.dataset.restore), "restore"); showToast("已恢复关注"); loadFavorites(); } catch (error) { showToast(error.message); } }
  });
  document.addEventListener("error", (event) => { if (event.target.matches?.(".product-image")) event.target.parentElement.classList.add("image-failed"); }, true);
  $$(".modal-backdrop").forEach((modal) => modal.addEventListener("click", (event) => { if (event.target === modal) closeModal(modal.id); }));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#account-menu").classList.contains("open")) { $("#account-menu").classList.remove("open"); $("#account-button").setAttribute("aria-expanded", "false"); $("#account-button").focus(); }
    const modal = $(".modal-backdrop.open"); if (!modal) return;
    if (event.key === "Escape") closeModal(modal.id);
    if (event.key === "Tab") { const nodes = [...modal.querySelectorAll('button:not([disabled]), a[href], input, select, [tabindex="0"]')].filter((node) => node.getClientRects().length); const first = nodes[0]; const last = nodes.at(-1); if (event.shiftKey && (document.activeElement === first || document.activeElement === modal.querySelector("[role=dialog]"))) { event.preventDefault(); last?.focus(); } else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); } }
  });
  window.addEventListener("hashchange", () => { state.inAppNavigation = true; renderRoute(); });
  $("#account-button").addEventListener("click", () => { const open = $("#account-menu").classList.toggle("open"); $("#account-button").setAttribute("aria-expanded", String(open)); });
  $("#switch-account").addEventListener("click", () => { $("#switch-form").reset(); $("#switch-error").textContent = ""; openModal("switch-modal"); $("#switch-username").focus(); });
  $("#logout-button").addEventListener("click", async () => { try { await post("/api/auth/logout", {}); } finally { history.replaceState(null, "", location.pathname); showLogin(); } });
  $("#register-link").addEventListener("click", () => { $("#register-form").reset(); $("#register-error").textContent = ""; openModal("register-modal"); $("#register-name").focus(); });
  ["login", "register", "switch"].forEach((kind) => $(`#${kind}-form`).addEventListener("submit", (event) => submitAuth(event, kind)));
  $("#plan-form").addEventListener("submit", savePlan); ["budget", "reserve"].forEach((field) => $(`#plan-${field}`).addEventListener("input", previewBudget));
  $("#catalog-search").addEventListener("input", (event) => { state.search = event.target.value; renderCatalog(); });
  $("#reset-filters").addEventListener("click", () => { resetFilters(); $("#catalog-search").focus(); });
  $("#admin-refresh").addEventListener("click", loadAdmin);
  $("#toast-close").addEventListener("click", hideToast);
  ["#detail-back-top", "#detail-back-bottom"].forEach((selector) => $(selector).addEventListener("click", goBack));
  $("#view-detail").addEventListener("click", (event) => {
    if (event.target.closest("#detail-favorite")) toggleFavorite();
    if (event.target.closest("#detail-avoid")) toggleAvoid();
    if (event.target.closest("#detail-speak")) toggleSpeaking();
    const substituteButton = event.target.closest("#detail-substitute"); if (substituteButton) findSubstitute(substituteButton);
  });
}
async function init() {
  bindEvents();
  let size = null; try { size = localStorage.getItem("financial-text-size"); } catch (_) { size = null; }
  setTextSize(size || "100");
  if (!state.token) return showLogin();
  try { const result = await api("/api/auth/me"); enterApp(result.user, state.token, true); } catch (_) { showLogin(); }
}
init();
