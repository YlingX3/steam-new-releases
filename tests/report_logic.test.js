const assert = require("node:assert/strict");
const { filterItems, pageOf, selectBulkTargets, aiBadge } = require("../report.js");

const items = [
  { appid: "1", title: "AI Game", kind: "game", is_free: false, price_value: 100, release_date: "2026-10-02", source_order: 1, ai_involved: true },
  { appid: "2", title: "Plain", kind: "game", is_free: false, price_value: 200, release_date: "2026-10-03", source_order: 0, ai_involved: false },
  { appid: "3", title: "Unknown", kind: "demo", is_free: true, price_value: 0, release_date: "2026-10-01", source_order: 2 },
];

const base = { kind: "all", paidOnly: false, hideAi: false, unseenOnly: false, loggedIn: true, sort: "release-desc" };

const hidden = filterItems(items, [], { ...base, hideAi: true }, new Set());
assert.deepEqual(hidden.map((item) => item.appid), ["2", "3"]);

const unseen = filterItems(items, [], { ...base, unseenOnly: true }, new Set(["2"]));
assert.deepEqual(unseen.map((item) => item.appid), ["1", "3"]);

const loggedOut = filterItems(items, [], { ...base, unseenOnly: true, loggedIn: false }, new Set(["2"]));
assert.equal(loggedOut.length, 3);

const paged = pageOf(hidden, 1, 1);
assert.equal(paged.totalPages, 2);
assert.equal(paged.items[0].appid, "2");

const skipped = selectBulkTargets(items, new Set(["1", "3"]), true);
assert.deepEqual(skipped.targets.map((item) => item.appid), ["2"]);
assert.deepEqual(skipped.skipped.map((item) => item.appid), ["1", "3"]);

const guest = selectBulkTargets(items, new Set(["1"]), false);
assert.equal(guest.targets.length, 3);
assert.equal(guest.skipped.length, 0);

const marked = aiBadge({ ai_involved: true, ai_disclosure: 'uses "models" & voices' });
assert.equal(marked, '<span class="badge ai" title="uses &quot;models&quot; &amp; voices">AI</span>');
assert.equal(aiBadge({ ai_involved: false, ai_disclosure: "hidden" }), "");
assert.equal(
  aiBadge({ ai_involved: true, ai_disclosure: "  " }),
  '<span class="badge ai" title="Steam 商店页标注了 AI 生成内容披露">AI</span>',
);

console.log("report logic ok");
