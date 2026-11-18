// 用钱时间轴纯函数与标记的检查。运行：node tests/timeline_model.test.js
// 只加载 app.js 中「网络与反馈」之前的部分（格式化函数和时间轴），不触碰 DOM。
const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const vm = require("node:vm");

const source = fs.readFileSync(path.join(__dirname, "..", "frontend", "app.js"), "utf8");
const cut = source.indexOf("/* ---------- 网络与反馈 ---------- */");
assert.ok(cut > 0, "找不到「网络与反馈」分隔注释");
const context = vm.createContext({ localStorage: { getItem: () => null } });
const api = vm.runInContext(`${source.slice(0, cut)}\n;({ state, timelineModel, timelineHTML, timelineTerm, sampleTimelineHTML, productTimelineHTML, homeTimelineHTML })`, context);
const { state, timelineModel, timelineHTML, timelineTerm, sampleTimelineHTML, productTimelineHTML, homeTimelineHTML } = api;

const TODAY = "2026-11-18";
const plan = { plan_completed: true, target_date: "2027-06-01", emergency_reserve: 50000 };
const targetDays = (Date.UTC(2027, 5, 1) - Date.UTC(2026, 10, 18)) / 86400000; // 195
const item = (id, termDays, extra = {}) => ({ id, name: `产品${id}`, term_days: termDays, ...extra });

// 1. 有计划：轴范围、色条百分比、结论
let model = timelineModel(plan, [item(1, 90), item(2, 195), item(3, 365), item(4, 30)], TODAY, { reserve: true });
assert.equal(model.mode, "plan");
assert.equal(model.target.days, targetDays);
assert.equal(model.target.dateText, "2027 年 6 月 1 日");
assert.equal(model.target.label, "2027 年 6 月 1 日 要用钱");
assert.equal(model.axisDays, Math.ceil(365 * 1.1));
assert.equal(model.rows.length, 4, "应急备用金加最多 3 个产品，第 4 个产品不显示");
assert.equal(model.rows[0].kind, "reserve");
assert.equal(model.rows[0].endPct, 100);
assert.equal(model.rows[1].verdict, "before");
assert.equal(model.rows[1].verdictText, "✓ 用钱前到期");
assert.equal(model.rows[1].endPct, Math.round(90 / model.axisDays * 1000) / 10);
assert.equal(model.rows[2].verdict, "before", "到期日等于用钱日期算用钱前到期");
assert.equal(model.rows[3].verdict, "after");
assert.equal(model.rows[3].verdictText, "⚠ 晚于用钱日期");
assert.equal(model.rows[3].whenText, "约 1 年后到期");
assert.equal(model.rows[3].dateText, "2027 年 11 月 18 日");
assert.ok(model.target.pct > 0 && model.target.pct < 100);

// 2. 随时可取、没有期限、所选期限档
model = timelineModel(plan, [item(1, 1), item(2, null), item(3, 365, { selected_variant: { id: "v", label: "半年", term_days: 182 } })], TODAY);
assert.deepEqual([...model.rows.map((row) => row.verdict)], ["anytime", "unknown", "before"]);
assert.equal(model.rows[0].verdictText, "✓ 随时可取");
assert.equal(model.rows[1].hasBar, false);
assert.match(model.rows[1].whenText, /资料未写明/);
assert.equal(model.rows[2].termDays, 182);
assert.equal(model.rows[2].name, "产品3 · 半年档");
assert.equal(timelineTerm({ term_days: 365, selected_variant: { label: "x", term_days: null } }).kind, "unknown");

// 3. 到账提醒
model = timelineModel(plan, [item(1, 100, { assessment: { soft_penalties: ["settlement_buffer"] } })], TODAY);
assert.match(model.rows[0].note, /到账可能晚于用款日/);

// 4. 固定 5 年轴：游客、没有计划、用钱日期已过
for (const profile of [null, { plan_completed: false, target_date: "2027-06-01" }, { plan_completed: true, target_date: "2026-11-18" }, { plan_completed: true, target_date: "2020-01-01" }]) {
  model = timelineModel(profile, [item(1, 365), item(2, 3650), item(3, 1)], TODAY, { reserve: true });
  assert.equal(model.mode, "fixed");
  assert.equal(model.target, null);
  assert.equal(model.axisDays, 1825);
  assert.equal(model.rows.length, 3, "固定轴不画应急备用金");
  assert.equal(model.rows[0].endPct, 20);
  assert.equal(model.rows[0].whenText, "约 1 年后可取出");
  assert.equal(model.rows[0].verdictText, "");
  assert.equal(model.rows[1].endPct, 100);
  assert.equal(model.rows[1].overflow, true);
  assert.equal(model.rows[2].verdictText, "随时可取");
}
assert.equal(timelineModel(plan, [item(1, 365)], TODAY, { mode: "fixed" }).mode, "fixed");
assert.equal(timelineModel(plan, [], TODAY).rows.length, 0);
assert.equal(timelineHTML(timelineModel(plan, [], TODAY)), "");

// 5. 标记：图形 aria-hidden、文字可见、插值转义
const hostile = { id: 9, name: '<img src=x onerror="1">', term_days: 365 };
let html = timelineHTML(timelineModel(plan, [hostile], TODAY, { reserve: true }), { variant: "hero" });
assert.ok(!html.includes("<img"), "产品名必须转义");
assert.ok(html.includes("&lt;img"));
assert.match(html, /class="timeline-axis" aria-hidden="true"/);
assert.equal((html.match(/class="timeline-track" aria-hidden="true"/g) || []).length, 2);
assert.match(html, /timeline-verdict">⚠ 晚于用钱日期|timeline-verdict">✓ 用钱前到期/);
assert.match(html, /2027 年 6 月 1 日 要用钱/);
assert.match(html, /aria-label="用钱时间轴"/);
assert.match(html, /class="timeline-target-flag is-start" style="--at:48\.5%"/);
assert.match(timelineHTML(timelineModel(plan, [item(1, 90)], TODAY), { variant: "detail" }), /timeline-target-flag is-end/, "用钱日期靠右时标签向左展开");

// 6. 卡片与详情：游客固定轴，登录且计划完整时用用钱日期
state.currentUser = null; state.profile = plan;
html = productTimelineHTML(item(1, 365), "card");
assert.match(html, /timeline--card/); assert.match(html, /timeline--fixed/); assert.ok(!html.includes("timeline-target-tick"));
state.currentUser = { role: "customer" };
html = productTimelineHTML(item(1, 365), "card");
assert.match(html, /timeline--plan/); assert.match(html, /timeline-target-tick/); assert.match(html, /晚于用钱日期/);
assert.ok(!html.includes("timeline-name"), "卡片里不重复产品名");
state.profile = { plan_completed: false };
assert.match(productTimelineHTML(item(1, 365), "detail"), /timeline--fixed/);
assert.match(homeTimelineHTML(plan, [item(1, 90)]), /应急备用金/);

// 7. 游客示例
html = sampleTimelineHTML(new Date(2026, 10, 18));
assert.match(html, /timeline-badge">示例</); assert.match(html, /示例产品 A/); assert.match(html, /不是真实产品/);
assert.match(html, /用钱前到期/); assert.match(html, /晚于用钱日期/);

console.log("timeline ok");
