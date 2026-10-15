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

test("parsing auto-runs the summary; the tab panel reuses results without duplicate requests", async () => {
  const h = harness(async (url, body) => {
    if (url === "/api/parse") return info("one");
    if (url === "/api/analyze") {
      assert.equal(body.include_summary, true, "解析即总结");
      return task("1", "queued");
    }
    return task("1", "done", { text_result: text("one"), summary_result: summary("one") });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush();
  assert.equal(h.calls[1].url, "/api/analyze");
  assert.equal(h.state.textLoading.value, true);
  assert.equal(h.state.analysis.summaryRequested, true);
  await h.poll();
  assert.equal(h.state.textResult.text, "one文本");
  assert.equal(h.state.sumResult.summary, "one摘要");
  assert.equal(h.state.mm.nodes.length, 3);
  assert.equal(h.state.sumLoading.value, false);
  assert.equal(h.state.spStatusText(), "已完成");
  // 复看/入口复用：不产生新请求，切到对应 Tab
  const count = h.calls.length;
  await h.state.onSummarizeClick(); await flush();
  assert.equal(h.calls.length, count, "generated results must be reused");
  assert.equal(h.state.activeTab.value, "summary");
  await h.state.extractText(); await flush();
  assert.equal(h.calls.length, count);
  assert.equal(h.state.activeTab.value, "text");
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
  let retried = false;
  const h = harness(async url => {
    if (url === "/api/parse") return info("one");
    if (url === "/api/analyze") return task("1", "queued");
    if (url.endsWith("/retry")) { retried = true; return task("1", "summarizing", { text_result: text("one") }); }
    return retried ? task("1", "done", { text_result: text("one"), summary_result: summary("one") })
      : task("1", "error", { text_result: text("one"), summary_status: "error", summary_error: "AI unavailable" });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush(); await h.poll();
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

test("article markdown renderer supports the constrained subset and escapes html", async () => {
  const h = harness(async () => ({}));
  const html = h.state.renderArticle(
    "## 小节标题\n段落有**加粗**和 <script>标签\n\n- 要点一\n- 要点二\n### 深层标题");
  assert.ok(html.includes("<h4>小节标题</h4>"));
  assert.ok(html.includes("<strong>加粗</strong>"));
  assert.ok(!html.includes("<script>"));
  assert.ok(html.includes("&lt;script&gt;"));
  assert.ok(html.includes("<ul><li>要点一</li><li>要点二</li></ul>"));
  assert.ok(html.includes("<h5>深层标题</h5>"));
  assert.equal(typeof h.state.renderArticle("半截**加粗"), "string", "流式半截内容不报错");
  assert.equal(h.state.renderArticle(""), "");
});

test("summary exports embed the generated article", async () => {
  const article = "## 文章小节\n- 文章要点";
  let requested = false;
  const h = harness(async url => {
    if (url === "/api/parse") return info("one");
    if (url === "/api/analyze") return textTask("1", "queued");
    if (url.endsWith("/summarize")) { requested = true; return task("1", "summarizing", { text_result: text("one") }); }
    if (!requested) return textTask("1", "done", { text_result: text("one") });
    return task("1", "done", {
      text_result: text("one"),
      summary_result: { ...summary("one"), article, article_degraded: false },
      article, article_status: "done",
    });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.parseVideo(); await flush(); await h.poll();
  await h.state.onSummarizeClick(); await h.poll();
  assert.ok(h.state.sumResult.article.includes("文章小节"));
  assert.equal(h.state.sumResult.articleStatus, "done");
  assert.ok(h.state.summaryToMd().includes("文章小节"));
  const txt = h.state.summaryToText();
  assert.ok(txt.includes("【总结文章】"));
  assert.ok(txt.includes("文章要点"));
});

test("polling fallback surfaces streaming article text", async () => {
  let polls = 0;
  const h = harness(async url => {
    if (url === "/api/analyze") return task("1", "queued");
    polls += 1;
    if (polls === 1) {
      return task("1", "summarizing", {
        text_result: text("one"), article_status: "streaming", article: "## 半截",
      });
    }
    return task("1", "done", {
      text_result: text("one"),
      summary_result: { ...summary("one"), article: "## 完整文章", article_degraded: false },
      article: "## 完整文章", article_status: "done",
    });
  });
  h.state.url.value = "https://example.com/one";
  await h.state.extractText(); await h.poll();
  assert.equal(h.state.sumResult.article, "## 半截");
  assert.equal(h.state.sumResult.articleStatus, "streaming");
  await h.poll();
  assert.equal(h.state.sumResult.article, "## 完整文章");
  assert.equal(h.state.sumResult.articleStatus, "done");
});
