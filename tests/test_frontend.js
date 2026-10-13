// Run: node --test tests/test_frontend.js. Exercise requests and asynchronous video changes.
const { test } = require("node:test");
const assert = require("node:assert/strict");
const fs = require("node:fs");
const vm = require("node:vm");
const path = require("node:path");

function deferred() {
  let resolve;
  const promise = new Promise(r => { resolve = r; });
  return { promise, resolve };
}
const flush = () => new Promise(resolve => setImmediate(resolve));

function harness(handler) {
  let state, timerId = 0;
  const timers = new Map(), calls = [];
  const context = vm.createContext({
    Vue: { ref: value => ({ value }), reactive: value => value,
      createApp: options => ({ mount: () => { state = options.setup(); } }) },
    localStorage: { getItem: () => null },
    document: { querySelector: () => null },
    setTimeout: (fn, ms) => { const id = ++timerId; timers.set(id, { fn, ms }); return id; },
    clearTimeout: id => timers.delete(id),
    fetch: async (url, options) => {
      const body = options?.body ? JSON.parse(options.body) : undefined;
      calls.push({ url, body });
      const result = await handler(url, body);
      return { ok: true, json: async () => result };
    },
  });
  vm.runInContext(fs.readFileSync(path.join(__dirname, "../frontend/js/app.js"), "utf8"), context);
  return { state, calls, async poll() {
    const entry = [...timers.entries()].find(([, timer]) => timer.ms === 800);
    assert.ok(entry, "analysis polling must be scheduled");
    timers.delete(entry[0]);
    await entry[1].fn();
  } };
}
const info = title => ({ title, webpage_url: `https://example.com/${title}`, qualities: [{ value: "1080" }] });
const task = (id, status, extra = {}) => ({ task_id: id, status,
  summary_requested: true,
  text_status: status === "queued" ? "pending" : "done",
  summary_status: status === "queued" ? "pending" : status === "done" ? "done" : "generating", ...extra });
const text = title => ({ title, chars: 4, text: `${title}文本` });
const textTask = (id, status, extra = {}) => task(id, status, {
  summary_requested: false, summary_status: "idle", ...extra,
});
const summary = title => ({ title, summary: `${title}摘要`, session_id: title, keywords: [],
  chapters: [{ title: "章节", start: 0, points: ["要点"] }], mindmap: null });

test("parse only extracts text; clicking AI summary generates summary and map from the existing task", async () => {
  let requested = false;
  const h = harness(async (url, body) => {
    if (url === "/api/parse") return info("one");
    if (url === "/api/analyze") {
      assert.equal(body.include_summary, false);
      return textTask("1", "queued");
    }
    if (url.endsWith("/summarize")) { requested = true; return task("1", "summarizing", { text_result: text("one") }); }
    return !requested ? textTask("1", "done", { text_result: text("one") })
      : task("1", "done", { text_result: text("one"), summary_result: summary("one") });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush();
  assert.equal(h.calls[1].url, "/api/analyze");
  assert.equal(h.state.textLoading.value, true);
  await h.poll();
  assert.equal(h.state.textResult.text, "one文本");
  assert.equal(h.state.textLoading.value, false);
  assert.equal(h.state.sumLoading.value, false);
  assert.equal(h.state.analysis.summaryRequested, false);
  assert.equal(h.state.sumResult.summary, "");
  assert.equal(h.state.mm.nodes.length, 0);
  assert.equal(requested, false);
  await h.state.onSummarizeClick();
  assert.equal(h.state.analysis.summaryRequested, true);
  assert.equal(h.state.sumLoading.value, true);
  await h.poll();
  assert.equal(h.state.sumResult.summary, "one摘要");
  assert.equal(h.state.mm.nodes.length, 3);
  assert.equal(h.state.sumLoading.value, false);
  assert.equal(h.state.showSumModal.value, false);
  const count = h.calls.length;
  await h.state.extractText(); h.state.onSummarizeClick(); await flush();
  assert.equal(h.calls.length, count, "view buttons must reuse generated results");
  assert.equal(h.state.showTextModal.value, true);
  assert.equal(h.state.showSumModal.value, true);
});

test("a late result from the previous video cannot overwrite the newly parsed video", async () => {
  const oldPoll = deferred();
  const h = harness(async (url, body) => {
    if (url === "/api/parse") return info(body.url.endsWith("one") ? "one" : "two");
    if (url === "/api/analyze") return textTask(body.url.endsWith("one") ? "1" : "2", "queued");
    if (url.endsWith("/1")) return oldPoll.promise;
    return textTask("2", "done", { text_result: text("two") });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush();
  const pending = h.poll();
  h.state.url.value = "https://example.com/two";
  await h.state.parseVideo(); await flush();
  assert.equal(h.state.textResult.text, "");
  await h.poll();
  oldPoll.resolve(task("1", "done", { text_result: text("one"), summary_result: summary("one") }));
  await pending;
  assert.equal(h.state.textResult.title, "two");
  assert.equal(h.state.sumResult.title, "");
  assert.equal(h.state.sumResult.sessionId, "");
  assert.equal(h.state.analysis.summaryRequested, false);
});

test("AI failure retains text and retry uses the existing analysis task", async () => {
  let retried = false, requested = false;
  const h = harness(async url => {
    if (url === "/api/parse") return info("one");
    if (url === "/api/analyze") return textTask("1", "queued");
    if (url.endsWith("/summarize")) { requested = true; return task("1", "summarizing", { text_result: text("one") }); }
    if (url.endsWith("/retry")) { retried = true; return task("1", "summarizing", { text_result: text("one") }); }
    if (!requested) return textTask("1", "done", { text_result: text("one") });
    return retried ? task("1", "done", { text_result: text("one"), summary_result: summary("one") })
      : task("1", "error", { text_result: text("one"), summary_status: "error", summary_error: "AI unavailable" });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush(); await h.poll();
  await h.state.onSummarizeClick(); await h.poll();
  assert.equal(h.state.textResult.text, "one文本");
  assert.equal(h.state.analysis.summaryError, "AI unavailable");
  await h.state.retryAnalysis(); await h.poll();
  assert.equal(h.calls.filter(c => c.url === "/api/analyze").length, 1);
  assert.ok(h.calls.some(c => c.url === "/api/analysis/1/retry"));
  assert.equal(h.state.sumResult.summary, "one摘要");
});

test("the standalone extract-text button never requests AI", async () => {
  const h = harness(async (url, body) => {
    assert.equal(url === "/api/analyze" || url === "/api/analysis/1", true);
    if (url === "/api/analyze") {
      assert.equal(body.include_summary, false);
      return textTask("1", "queued");
    }
    return textTask("1", "done", { text_result: text("one") });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.extractText(); await h.poll();
  assert.equal(h.state.analysis.summaryRequested, false);
  assert.equal(h.state.sumResult.summary, "");
  assert.equal(h.state.mm.nodes.length, 0);
});
