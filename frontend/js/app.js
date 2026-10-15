/* 闪电下载 · 前端应用（Vue3，本地 vendor 引入，零构建） */
const { createApp, reactive, ref } = Vue;

const app = createApp({
  setup() {
    /* ---------- 静态展示数据 ---------- */
    const platforms = [
      { name: "哔哩哔哩", icon: "📺", color: "#FB7299" },
      { name: "抖音", icon: "🎵", color: "#111827" },
      { name: "YouTube", icon: "▶️", color: "#FF0000" },
      { name: "快手", icon: "🎬", color: "#FF7300" },
      { name: "微博", icon: "🦋", color: "#E6162D" },
      { name: "Instagram", icon: "📷", color: "#E1306C" },
      { name: "TikTok", icon: "🎹", color: "#25F4EE" },
      { name: "优酷", icon: "🔵", color: "#1EBEFF" },
      { name: "腾讯视频", icon: "🐧", color: "#FFA726" },
      { name: "西瓜视频", icon: "🍉", color: "#16A34A" },
    ];
    const features = [
      { icon: "⚡", title: "秒级解析", desc: "粘贴链接即刻获取视频信息与全部可用清晰度" },
      { icon: "🎬", title: "超清画质", desc: "最高支持 4K，自动合并音视频，支持 MP3 提取" },
      { icon: "📝", title: "文本提取", desc: "一键提取视频字幕文本，学习整理更高效" },
      { icon: "📱", title: "全端适配", desc: "手机、平板、电脑浏览器直接用，无需装 App" },
      { icon: "🆓", title: "完全免费", desc: "不限次数、不限清晰度、无需注册登录" },
      { icon: "🌐", title: "海量平台", desc: "基于 yt-dlp 引擎，支持 1800+ 视频网站" },
      { icon: "🔒", title: "隐私安全", desc: "字幕链路不读取 Cookie；问答转写仅在内存临时保留" },
      { icon: "✨", title: "AI 视频总结", desc: "摘要+时间戳章节+思维导图+追问问答，快速吃透长视频" },
    ];
    const faqs = [
      { q: "支持哪些平台？", a: "基于 yt-dlp 开源引擎，支持哔哩哔哩、抖音、快手、微博、YouTube、Instagram、TikTok 等 1800+ 网站；少数平台（如小红书）暂不支持。" },
      { q: "真的是完全免费吗？", a: "是。视频下载与文本提取不限次数、不限清晰度、无需登录；AI 总结与 AI 问答当前限时免费开放（每分钟 3 次）。" },
      { q: "下载的文件会保存多久？", a: "文件在服务器临时保留 2 小时后自动删除，请下载完成后及时保存到本地设备。" },
      { q: "为什么提取不到 B 站字幕？", a: "本工具为了保护隐私，不读取浏览器或本机 Cookie。只有平台公开可访问的字幕能直接提取；若该视频字幕要求登录，目前无法提取。使用 AI 总结时，字幕文本和视频标题会发送给本项目配置的 AI 服务商。" },
      { q: "手机上怎么保存视频？", a: "点击\"保存文件\"后浏览器会直接下载；iOS 可在\"文件\"App 中查看，安卓在下载目录查看，也可保存到相册。" },
      { q: "如何开通会员？", a: "当前为模拟支付阶段：点击定价区\"立即开通会员\"，模拟支付成功或输入激活码（如 VIP2026）即可体验会员流程，正式支付将在后续版本接入。" },
    ];

    /* ---------- 状态 ---------- */
    const url = ref("");
    const parsing = ref(false);
    const videoInfo = ref(null);
    const selectedQuality = ref("");
    const resultSection = ref(null);
    const openFaq = ref(-1);

    const dl = reactive({
      taskId: null, status: "", progress: 0, speed: null,
      eta: null, error: null, download_url: null, filename: null,
    });
    let pollTimer = null;

    const textLoading = ref(false);
    const textResult = reactive({ title: "", text: "", chars: 0, lang: "" });
    // 同屏总结面板：当前激活 Tab（summary/chapters/mindmap/text/qa）
    const activeTab = ref("summary");
    const analysis = reactive({
      taskId: "", url: "", status: "", textStatus: "", summaryStatus: "",
      textError: "", summaryError: "", summaryRequested: false,
    });
    let analysisTimer = null;
    let analysisVersion = 0;
    let parsedInput = "";

    const showPayModal = ref(false);
    const payTab = ref("wechat");
    const vipCode = ref("");
    // 阶段 4 将迁移到后端校验（/api/vip/activate），当前与 config.VIP_CODES 保持一致
    const VIP_CODES = ["VIP2026", "DOWN8888"];
    const isVip = ref(localStorage.getItem("sd_vip") === "1");

    const toasts = reactive([]);
    let toastId = 0;
    function toast(msg, type = "info") {
      const id = ++toastId;
      toasts.push({ id, msg, type });
      setTimeout(() => {
        const i = toasts.findIndex(t => t.id === id);
        if (i > -1) toasts.splice(i, 1);
      }, 3200);
    }

    /* ---------- 工具 ---------- */
    async function api(path, body) {
      const opts = body
        ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
        : {};
      const r = await fetch(path, opts);
      const d = await r.json().catch(() => ({}));
      if (!r.ok) {
        const error = new Error(d.detail || `请求失败（${r.status}）`);
        error.status = r.status;
        throw error;
      }
      return d;
    }
    function formatDuration(sec) {
      if (!sec && sec !== 0) return "";
      sec = Math.round(sec);
      const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
      const mm = String(m).padStart(2, "0"), ss = String(s).padStart(2, "0");
      return h ? `${h}:${mm}:${ss}` : `${m}:${ss}`;
    }
    function statusText(s) {
      return { queued: "排队中", downloading: "下载中", merging: "合并音视频中", converting: "转码中", done: "已完成" }[s] || s;
    }
    function scrollTo(sel) {
      setTimeout(() => document.querySelector(sel)?.scrollIntoView({ behavior: "smooth", block: "start" }), 60);
    }

    /* ---------- 核心业务：解析 ---------- */
    async function parseVideo() {
      if (parsing.value) return;
      const u = url.value.trim();
      if (!u) { toast("请先粘贴视频链接", "error"); return; }
      if (!/^https?:\/\//.test(u)) { toast("链接需以 http(s):// 开头", "error"); return; }
      parsing.value = true;
      stopPoll();
      resetDl();
      resetAnalysis();
      const version = analysisVersion;
      videoInfo.value = null;
      parsedInput = "";
      try {
        const info = await api("/api/parse", { url: u });
        if (version !== analysisVersion) return;
        videoInfo.value = info;
        parsedInput = u;
        const q = info.qualities.find(x => x.value === "1080") || info.qualities[0];
        selectedQuality.value = q ? q.value : "";
        scrollTo("#result");
        // 解析即总结：同屏右栏自动走 提取文本 → AI 总结 → 流式文章 全流程
        startAnalysis(info.webpage_url || u, version, true);
      } catch (e) {
        toast(e.message, "error");
      } finally {
        parsing.value = false;
      }
    }

    /* ---------- 核心业务：下载 ---------- */
    function resetDl() {
      stopPoll();
      Object.assign(dl, {
        taskId: null, status: "", progress: 0, speed: null,
        eta: null, error: null, download_url: null, filename: null,
      });
    }
    function resetDownload() { resetDl(); }

    async function startDownload() {
      if (!selectedQuality.value) { toast("请选择清晰度", "error"); return; }
      resetDl();
      try {
        const r = await api("/api/download", {
          url: videoInfo.value.webpage_url || url.value.trim(),
          quality: selectedQuality.value,
          title: videoInfo.value.title,
        });
        dl.taskId = r.task_id;
        dl.status = r.status;
        startPoll();
      } catch (e) {
        toast(e.message, "error");
      }
    }
    function startPoll() {
      stopPoll();
      pollTimer = setInterval(async () => {
        try {
          const t = await api(`/api/tasks/${dl.taskId}`);
          Object.assign(dl, t);
          if (t.status === "done") {
            stopPoll();
            toast("下载完成，快保存文件吧 🎉", "success");
          } else if (t.status === "error" || t.status === "expired") {
            stopPoll();
          }
        } catch (e) {
          stopPoll();
          dl.status = "error";
          dl.error = e.message;
        }
      }, 800);
    }
    function stopPoll() { if (pollTimer) { clearInterval(pollTimer); pollTimer = null; } }

    /* ---------- 核心业务：文本提取 ---------- */
    async function extractText() {
      if (parsing.value) return;
      const u = currentSource();
      if (analysis.url === u && textResult.text) {
        activeTab.value = "text";
        scrollTo("#result");
        return;
      }
      if (!u) { toast("请先粘贴视频链接", "error"); return; }
      if (analysis.url === u) {
        if (analysis.status === "error") await retryAnalysis();
      } else {
        resetAnalysis();
        videoInfo.value = null;
        await startAnalysis(u);
      }
      activeTab.value = "text";
      scrollTo("#result");
    }
    async function copyText() {
      try {
        await navigator.clipboard.writeText(textResult.text);
        toast("已复制到剪贴板", "success");
      } catch {
        const ta = document.createElement("textarea");
        ta.value = textResult.text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        toast("已复制到剪贴板", "success");
      }
    }
    function downloadTxt() {
      const blob = new Blob([textResult.text], { type: "text/plain;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${(textResult.title || "视频文本").slice(0, 50)}.txt`;
      a.click();
      URL.revokeObjectURL(a.href);
    }

    /* ---------- 会员（模拟） ---------- */
    function openPay() { showPayModal.value = true; }
    function mockPaySuccess() {
      isVip.value = true;
      localStorage.setItem("sd_vip", "1");
      showPayModal.value = false;
      toast("模拟支付成功，已开通会员 👑", "success");
    }
    function activateVip() {
      const code = vipCode.value.trim();
      if (!code) { toast("请输入激活码", "error"); return; }
      if (!VIP_CODES.includes(code.toUpperCase())) { toast("激活码无效", "error"); return; }
      isVip.value = true;
      localStorage.setItem("sd_vip", "1");
      showPayModal.value = false;
      vipCode.value = "";
      toast("激活成功，欢迎加入会员 👑", "success");
    }
    function onSummarizeClick() { return doSummarize(); }

    /* ---------- 阶段4：AI 总结 + AI 问答 + 思维导图 ---------- */
    const sumLoading = ref(false);
    const sumResult = reactive({
      sessionId: "", title: "", chars: 0, model: "", elapsed: 0,
      summary: "", transcript: "", chapters: [], keywords: [], mindmap: null,
      article: "", articleStatus: "idle", articleDegraded: false,
    });
    let articleSource = null;   // 文章 SSE 订阅（EventSource）

    // 右栏面板状态徽章
    function spStatusText() {
      if (analysis.status === "error") return "失败";
      if (analysis.status === "extracting") return "提取文本中…";
      if (analysis.status === "summarizing") return sumResult.article ? "文章流式生成中…" : "AI 总结生成中…";
      if (analysis.status === "done") return analysis.summaryRequested ? "已完成" : "文本就绪";
      return "排队中…";
    }
    function spStatusClass() {
      if (analysis.status === "error") return "bad";
      if (["extracting", "summarizing", "queued"].includes(analysis.status)) return "busy";
      return "ok";
    }
    const mm = reactive({ viewBox: "0 0 640 160", width: 640, height: 160, nodes: [], links: [] });
    const qaMessages = reactive([]);
    const qaInput = ref("");
    const qaLoading = ref(false);

    function currentSource() {
      return videoInfo.value && url.value.trim() === parsedInput
        ? videoInfo.value.webpage_url || parsedInput : url.value.trim();
    }

    function resetAnalysis() {
      analysisVersion++;
      clearTimeout(analysisTimer);
      analysisTimer = null;
      closeArticleStream();
      Object.assign(analysis, {
        taskId: "", url: "", status: "", textStatus: "", summaryStatus: "",
        textError: "", summaryError: "", summaryRequested: false,
      });
      Object.assign(textResult, { title: "", text: "", chars: 0, lang: "" });
      Object.assign(sumResult, {
        sessionId: "", title: "", chars: 0, model: "", elapsed: 0,
        summary: "", transcript: "", chapters: [], keywords: [], mindmap: null,
        article: "", articleStatus: "idle", articleDegraded: false,
      });
      Object.assign(mm, { viewBox: "0 0 640 160", width: 640, height: 160, nodes: [], links: [] });
      activeTab.value = "summary";
      textLoading.value = false;
      sumLoading.value = false;
      qaMessages.splice(0, qaMessages.length);
      qaInput.value = "";
      qaLoading.value = false;
    }

    function applyAnalysis(task) {
      Object.assign(analysis, {
        taskId: task.task_id, status: task.status,
        textStatus: task.text_status, summaryStatus: task.summary_status,
        textError: task.text_error || "", summaryError: task.summary_error || "",
        summaryRequested: !!task.summary_requested,
      });
      textLoading.value = ["pending", "extracting"].includes(task.text_status);
      sumLoading.value = ["pending", "generating"].includes(task.summary_status);
      if (task.text_result) Object.assign(textResult, task.text_result);
      // 文章：轮询通道与服务端任务字段同源（SSE 只是更高频的同源推送），直接覆盖
      if (task.article_status !== undefined && task.article_status !== "idle") {
        sumResult.article = task.article || "";
        sumResult.articleStatus = task.article_status;
        sumResult.articleDegraded = !!task.article_degraded;
      }
      if (task.summary_requested && !["done", "error"].includes(task.status)) openArticleStream(task.task_id);
      if (task.summary_result && !sumResult.summary) {
        Object.assign(sumResult, task.summary_result);
        sumResult.sessionId = task.summary_result.session_id || "";
        if (task.summary_result.article) {
          sumResult.article = task.summary_result.article;
          sumResult.articleStatus = "done";
          sumResult.articleDegraded = !!task.summary_result.article_degraded;
        }
        ensureMindmap();
        layoutMindmap();
        toast("文本、AI 总结和思维导图已生成 ✨", "success");
      }
    }

    /* ---------- 总结文章：SSE 流式订阅 + markdown 轻量渲染 ---------- */

    function openArticleStream(taskId) {
      if (articleSource && articleSource._taskId === taskId) return;
      closeArticleStream();
      if (typeof EventSource === "undefined") return; // 极老浏览器退化为轮询展示
      const version = analysisVersion;
      const es = new EventSource(`/api/analysis/${taskId}/article-stream`);
      es._taskId = taskId;
      es.onmessage = (ev) => {
        if (version !== analysisVersion) { es.close(); return; }
        let d;
        try { d = JSON.parse(ev.data); } catch { return; }
        if (typeof d.text === "string" && d.text) sumResult.article = d.text;
        sumResult.articleStatus = d.status;
        sumResult.articleDegraded = !!d.degraded;
        if (d.status === "done" || d.status === "error") es.close();
      };
      es.onerror = () => { /* 断线重连交给 EventSource；任务终态后由 onmessage 关闭 */ };
      articleSource = es;
    }

    function closeArticleStream() {
      if (articleSource) { articleSource.close(); articleSource = null; }
    }

    // 零依赖 markdown 渲染：仅支持文章 prompt 约定的子集（## 标题 / **加粗** / - 列表）。
    // 先整体 HTML 转义再变换；流式半截内容（未闭合 ** 等）按原样展示，不报错。
    function renderArticle(md) {
      const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const inline = (s) => esc(s).replace(/\*\*([^*\n]+)\*\*/g, "<strong>$1</strong>");
      const out = [];
      let inList = false, para = [];
      const flushPara = () => { if (para.length) { out.push(`<p>${para.join("<br>")}</p>`); para = []; } };
      const flushList = () => { if (inList) { out.push("</ul>"); inList = false; } };
      for (const raw of String(md || "").split(/\r?\n/)) {
        const line = raw.trimEnd();
        const h = line.match(/^(#{2,5})\s+(.+)$/);
        const li = line.match(/^\s*[-*]\s+(.+)$/);
        if (h) {
          flushPara(); flushList();
          const level = Math.min(h[1].length + 2, 6); // ## 映射 h4，视觉贴合卡片
          out.push(`<h${level}>${inline(h[2])}</h${level}>`);
        } else if (li) {
          flushPara();
          if (!inList) { out.push("<ul>"); inList = true; }
          out.push(`<li>${inline(li[1])}</li>`);
        } else if (!line.trim()) {
          flushPara(); flushList();
        } else {
          flushList();
          para.push(inline(line));
        }
      }
      flushPara(); flushList();
      return out.join("");
    }

    function articleFilename() {
      return (sumResult.title || "AI总结文章").slice(0, 50).replace(/[\\/:*?"<>|]/g, "_") + "-AI文章.md";
    }

    async function copyArticle() {
      const text = sumResult.article || "";
      if (!text) { toast("文章还没有生成好", "error"); return; }
      try {
        await navigator.clipboard.writeText(text);
        toast("文章已复制到剪贴板", "success");
      } catch {
        const ta = document.createElement("textarea");
        ta.value = text;
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        toast("文章已复制到剪贴板", "success");
      }
    }

    function downloadArticle() {
      const text = sumResult.article || "";
      if (!text) { toast("文章还没有生成好", "error"); return; }
      const blob = new Blob([text], { type: "text/markdown;charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = articleFilename();
      a.click();
      URL.revokeObjectURL(a.href);
    }

    function scheduleAnalysisPoll(version, taskId) {
      clearTimeout(analysisTimer);
      analysisTimer = setTimeout(async () => {
        if (version !== analysisVersion || taskId !== analysis.taskId) return;
        try {
          const task = await api(`/api/analysis/${taskId}`);
          if (version !== analysisVersion || taskId !== analysis.taskId) return;
          applyAnalysis(task);
          if (!["done", "error"].includes(task.status)) scheduleAnalysisPoll(version, taskId);
        } catch (e) {
          if (version !== analysisVersion) return;
          failAnalysis(e.message);
        }
      }, 800);
    }

    function failAnalysis(message) {
      analysis.status = "error";
      if (!textResult.text) {
        analysis.textStatus = "error";
        analysis.textError = message;
      }
      analysis.summaryStatus = analysis.summaryRequested ? "error" : "idle";
      analysis.summaryError = analysis.summaryRequested ? message : "";
      textLoading.value = false;
      sumLoading.value = false;
    }

    async function startAnalysis(u, version = analysisVersion, includeSummary = false) {
      Object.assign(analysis, {
        url: u, status: "queued", textStatus: "pending",
        summaryStatus: includeSummary ? "pending" : "idle", summaryRequested: includeSummary,
      });
      textLoading.value = true;
      sumLoading.value = includeSummary;
      try {
        const task = await api("/api/analyze", { url: u, include_summary: includeSummary });
        if (version !== analysisVersion) return;
        applyAnalysis(task);
        if (!["done", "error"].includes(task.status)) scheduleAnalysisPoll(version, task.task_id);
      } catch (e) {
        if (version === analysisVersion) failAnalysis(e.message);
      }
    }

    async function retryAnalysis() {
      if (textLoading.value || sumLoading.value || !analysis.url) return;
      if (!analysis.taskId) return startAnalysis(analysis.url, analysisVersion, analysis.summaryRequested);
      const version = analysisVersion;
      textLoading.value = !textResult.text;
      sumLoading.value = analysis.summaryRequested;
      analysis.textError = "";
      analysis.summaryError = "";
      try {
        const task = await api(`/api/analysis/${analysis.taskId}/retry`, {});
        if (version !== analysisVersion) return;
        applyAnalysis(task);
        if (!["done", "error"].includes(task.status)) scheduleAnalysisPoll(version, task.task_id);
      } catch (e) {
        if (version !== analysisVersion) return;
        if (e.status === 404) await startAnalysis(analysis.url, version, analysis.summaryRequested);
        else failAnalysis(e.message);
      }
    }

    async function doSummarize() {
      if (parsing.value) return;
      const u = currentSource();
      if (!u) { toast("请先粘贴视频链接", "error"); return; }
      if (analysis.url === u) {
        if (sumResult.summary) { activeTab.value = "summary"; scrollTo("#result"); return; }
        if (sumLoading.value || textLoading.value) return;
        if (analysis.summaryRequested && analysis.status === "error") {
          await retryAnalysis();
        } else if (analysis.taskId) {
          const version = analysisVersion;
          analysis.summaryRequested = true;
          analysis.summaryStatus = "pending";
          sumLoading.value = true;
          try {
            const task = await api(`/api/analysis/${analysis.taskId}/summarize`, {});
            if (version !== analysisVersion) return;
            applyAnalysis(task);
            if (!["done", "error"].includes(task.status)) scheduleAnalysisPoll(version, task.task_id);
          } catch (e) {
            if (version !== analysisVersion) return;
            if (e.status === 404) await startAnalysis(u, version, true);
            else failAnalysis(e.message);
          }
        } else {
          await startAnalysis(u, analysisVersion, true);
        }
      } else {
        resetAnalysis();
        videoInfo.value = null;
        await startAnalysis(u, analysisVersion, true);
      }
      activeTab.value = "summary";
      scrollTo("#result");
    }

    function ensureMindmap() {
      if (sumResult.mindmap && sumResult.mindmap.label) return;
      sumResult.mindmap = {
        label: (sumResult.title || "视频").slice(0, 14),
        children: (sumResult.chapters || []).map(c => ({
          label: String(c.title || "").slice(0, 14),
          children: (c.points || []).slice(0, 3).map(p => ({ label: String(p).slice(0, 14) })),
        })),
      };
    }

    function mmTrim(s) { s = String(s || ""); return s.length > 14 ? s.slice(0, 13) + "…" : s; }

    function layoutMindmap() {
      const root = sumResult.mindmap;
      if (!root || !root.label) { mm.nodes = []; mm.links = []; return; }
      const W = 176, STEP = 46, PADX = 14, PADY = 16;
      const nodes = [], links = [];
      let cursor = 0, maxDepth = 0;
      function place(node, depth, parent) {
        maxDepth = Math.max(maxDepth, depth);
        const kids = node.children || [];
        const rec = {
          id: nodes.length, label: mmTrim(node.label), depth,
          childCount: kids.length, collapsed: !!node._collapsed,
          x: PADX + Math.min(depth, 4) * (W + 28), w: W, y: 0, _ref: node,
        };
        nodes.push(rec);
        if (parent) links.push({ from: parent.id, to: rec.id });
        const shown = rec.collapsed ? [] : kids;
        if (!shown.length) { rec.y = PADY + cursor * STEP + 17; cursor++; }
        else {
          const ys = shown.map(k => place(k, depth + 1, rec));
          rec.y = (ys[0] + ys[ys.length - 1]) / 2;
        }
        return rec.y;
      }
      place(root, 0, null);
      const height = Math.max(cursor * STEP + PADY * 2, 140);
      const width = PADX * 2 + Math.min(maxDepth, 4) * (W + 28) + W;
      mm.viewBox = `0 0 ${width} ${height}`;
      mm.width = width;
      mm.height = height;
      mm.nodes = nodes;
      mm.links = links.map(l => {
        const a = nodes[l.from], b = nodes[l.to];
        const x1 = a.x + a.w, y1 = a.y, x2 = b.x, y2 = b.y;
        const dx = Math.max(24, (x2 - x1) / 2);
        return { d: `M ${x1} ${y1} C ${x1 + dx} ${y1}, ${x2 - dx} ${y2}, ${x2} ${y2}` };
      });
    }

    function toggleMmNode(n) {
      if (mmDrag.moved) return; // 拖拽平移后松手误触的 click 不折叠
      if (!n.childCount) return;
      n._ref._collapsed = !n._ref._collapsed;
      layoutMindmap();
    }

    /* ---------- 思维导图：全屏覆盖层 + 拖拽平移/缩放 + PNG 下载 ---------- */

    const mmView = reactive({ fullscreen: false, x: 0, y: 0, scale: 1 });
    const mmDrag = { on: false, moved: false, sx: 0, sy: 0, bx: 0, by: 0 };

    function mmFitView() {
      const vw = window.innerWidth || 1200, vh = window.innerHeight || 800;
      mmView.scale = Math.max(0.2, Math.min(1, (vw - 80) / (mm.width || 1), (vh - 180) / (mm.height || 1)));
      mmView.x = (vw - mm.width * mmView.scale) / 2;
      mmView.y = (vh - mm.height * mmView.scale) / 2;
    }

    function mmOnKey(e) { if (e.key === "Escape") closeMmFullscreen(); }

    function openMmFullscreen() {
      if (!mm.nodes.length) return;
      mmView.fullscreen = true;
      mmFitView();
      window.addEventListener("keydown", mmOnKey);
    }

    function closeMmFullscreen() {
      mmView.fullscreen = false;
      window.removeEventListener("keydown", mmOnKey);
    }

    function onMmPointerDown(e) {
      mmDrag.on = true; mmDrag.moved = false;
      mmDrag.sx = e.clientX; mmDrag.sy = e.clientY;
      mmDrag.bx = mmView.x; mmDrag.by = mmView.y;
      if (e.currentTarget.setPointerCapture) {
        try { e.currentTarget.setPointerCapture(e.pointerId); } catch { /* 旧浏览器忽略 */ }
      }
    }

    function onMmPointerMove(e) {
      if (!mmDrag.on) return;
      const dx = e.clientX - mmDrag.sx, dy = e.clientY - mmDrag.sy;
      if (Math.abs(dx) + Math.abs(dy) > 6) mmDrag.moved = true;
      mmView.x = mmDrag.bx + dx;
      mmView.y = mmDrag.by + dy;
    }

    function onMmPointerUp() { mmDrag.on = false; }

    function mmZoomAt(factor, cx, cy) {
      const s0 = mmView.scale;
      const s1 = Math.min(3, Math.max(0.15, s0 * factor));
      mmView.x = cx - (cx - mmView.x) * (s1 / s0); // 以指针为锚点缩放
      mmView.y = cy - (cy - mmView.y) * (s1 / s0);
      mmView.scale = s1;
    }

    function onMmWheel(e) {
      e.preventDefault();
      mmZoomAt(e.deltaY < 0 ? 1.12 : 0.89, e.clientX, e.clientY);
    }

    function mmZoom(factor) {
      mmZoomAt(factor, (window.innerWidth || 1200) / 2, (window.innerHeight || 800) / 2);
    }

    // 生成独立 SVG 字符串（内嵌样式，不依赖页面 CSS），供 PNG 导出使用
    function mmSvgString() {
      const esc = (s) => String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
      const parts = [
        `<svg xmlns="http://www.w3.org/2000/svg" width="${mm.width}" height="${mm.height}" viewBox="${mm.viewBox}" font-family="-apple-system,'Segoe UI','Microsoft YaHei',sans-serif">`,
        "<style>.mm-link{fill:none;stroke:#C7D2FE;stroke-width:1.6}.mm-node rect{fill:#fff;stroke:#93C5FD;stroke-width:1.2}.mm-node text{font-size:12.5px;fill:#1F2937}.mm-node.depth0 rect{fill:#2563EB;stroke:#1D4ED8}.mm-node.depth0 text{fill:#fff;font-weight:700}.mm-node.depth1 rect{fill:#EFF6FF}.mm-fold{fill:#2563EB;font-weight:700}.mm-node.depth0 .mm-fold{fill:#fff}</style>",
        `<rect width="${mm.width}" height="${mm.height}" fill="#ffffff"/>`,
      ];
      for (const l of mm.links) parts.push(`<path class="mm-link" d="${l.d}"/>`);
      for (const n of mm.nodes) {
        parts.push(`<g class="mm-node depth${n.depth}" transform="translate(${n.x},${n.y})"><rect x="0" y="-17" width="${n.w}" height="34" rx="8"/><text x="12" y="5">${esc(n.label)}</text>`);
        if (n.childCount) parts.push(`<text class="mm-fold" x="${n.w - 16}" y="5">${n.collapsed ? "+" : "−"}</text>`);
        parts.push("</g>");
      }
      parts.push("</svg>");
      return parts.join("");
    }

    function downloadMmPng() {
      if (!mm.nodes.length) { toast("思维导图还没有生成", "error"); return; }
      const scale = 2, pad = 24; // 2 倍采样保证文字清晰
      const blob = new Blob([mmSvgString()], { type: "image/svg+xml;charset=utf-8" });
      const url = URL.createObjectURL(blob);
      const img = new Image();
      img.onload = () => {
        const canvas = document.createElement("canvas");
        canvas.width = (mm.width + pad * 2) * scale;
        canvas.height = (mm.height + pad * 2) * scale;
        const ctx = canvas.getContext("2d");
        ctx.fillStyle = "#fff";
        ctx.fillRect(0, 0, canvas.width, canvas.height);
        ctx.drawImage(img, pad * scale, pad * scale, mm.width * scale, mm.height * scale);
        URL.revokeObjectURL(url);
        canvas.toBlob((b) => {
          if (!b) { toast("导图图片生成失败", "error"); return; }
          const a = document.createElement("a");
          a.href = URL.createObjectURL(b);
          a.download = (sumResult.title || "思维导图").slice(0, 50).replace(/[\\/:*?"<>|]/g, "_") + "-思维导图.png";
          a.click();
          URL.revokeObjectURL(a.href);
          toast("思维导图图片已下载 🖼", "success");
        }, "image/png");
      };
      img.onerror = () => { URL.revokeObjectURL(url); toast("导图图片生成失败", "error"); };
      img.src = url;
    }

    async function askQuestion() {
      const q = qaInput.value.trim();
      if (!q || qaLoading.value) return;
      if (!sumResult.sessionId) { toast("请先生成 AI 总结", "error"); return; }
      qaInput.value = "";
      qaMessages.push({ role: "user", content: q });
      qaLoading.value = true;
      const version = analysisVersion;
      try {
        const history = qaMessages.slice(0, -1).slice(-6).map(m => ({ role: m.role, content: m.content }));
        const r = await api("/api/qa", { session_id: sumResult.sessionId, question: q, history });
        if (version !== analysisVersion) return;
        qaMessages.push({ role: "assistant", content: r.answer });
      } catch (e) {
        if (version !== analysisVersion) return;
        qaMessages.push({ role: "assistant", content: "⚠ " + e.message });
      } finally {
        if (version === analysisVersion) qaLoading.value = false;
        setTimeout(() => {
          const el = document.querySelector(".sp-body .qa-list");
          if (el) el.scrollTop = el.scrollHeight;
        }, 30);
      }
    }

    async function copyTs(sec) {
      const ts = "[" + formatDuration(sec) + "]";
      try { await navigator.clipboard.writeText(ts); toast(`已复制 ${ts}`, "success"); }
      catch { toast(`时间点 ${ts}`, "info"); }
    }

    function summaryToText() {
      const r = sumResult;
      let out = `【${r.title}】AI 视频总结\n\n摘要：${r.summary}\n\n关键词：${(r.keywords || []).join("、")}\n\n`;
      if (r.article) out += `【总结文章】\n${r.article}\n\n`;
      (r.chapters || []).forEach(c => {
        out += `[${formatDuration(c.start)}] ${c.title}\n`;
        (c.points || []).forEach(p => { out += `  - ${p}\n`; });
        out += "\n";
      });
      out += "—— 由闪电下载 AI 生成（基于视频字幕）";
      return out;
    }

    function summaryToMd() {
      const r = sumResult;
      let out = `# ${r.title} · AI 视频总结\n\n> 摘要：${r.summary}\n\n`;
      if (r.keywords && r.keywords.length) out += `**关键词**：${r.keywords.join("、")}\n\n`;
      if (r.article) out += `${r.article}\n\n---\n\n`;
      (r.chapters || []).forEach((c, i) => {
        out += `## ${i + 1}. [${formatDuration(c.start)}] ${c.title}\n\n`;
        (c.points || []).forEach(p => { out += `- ${p}\n`; });
        out += "\n";
      });
      out += `---\n*由闪电下载 AI 生成 · 模型 ${r.model} · 字幕 ${r.chars} 字*\n`;
      return out;
    }

    async function copySummary() {
      try {
        await navigator.clipboard.writeText(summaryToText());
        toast("总结已复制到剪贴板", "success");
      } catch {
        const ta = document.createElement("textarea");
        ta.value = summaryToText();
        document.body.appendChild(ta);
        ta.select();
        document.execCommand("copy");
        ta.remove();
        toast("总结已复制到剪贴板", "success");
      }
    }

    function downloadSummary(fmt) {
      const isMd = fmt === "md";
      const content = isMd ? summaryToMd() : summaryToText();
      const blob = new Blob([content], { type: (isMd ? "text/markdown" : "text/plain") + ";charset=utf-8" });
      const a = document.createElement("a");
      a.href = URL.createObjectURL(blob);
      a.download = `${(sumResult.title || "AI总结").slice(0, 50).replace(/[\\/:*?"<>|]/g, "_")}.${isMd ? "md" : "txt"}`;
      a.click();
      URL.revokeObjectURL(a.href);
    }

    return {
      platforms, features, faqs,
      url, parsing, videoInfo, selectedQuality, resultSection, openFaq,
      dl, textLoading, textResult, analysis, retryAnalysis,
      showPayModal, payTab, vipCode, isVip, toasts,
      parseVideo, startDownload, resetDownload, extractText, copyText, downloadTxt,
      openPay, mockPaySuccess, activateVip, onSummarizeClick,
      sumLoading, sumResult, mm, qaMessages, qaInput, qaLoading,
      activeTab, spStatusText, spStatusClass,
      renderArticle, copyArticle, downloadArticle,
      mmView, openMmFullscreen, closeMmFullscreen, mmFitView,
      onMmPointerDown, onMmPointerMove, onMmPointerUp, onMmWheel, mmZoom, downloadMmPng,
      askQuestion, copyTs, copySummary, downloadSummary, toggleMmNode,
      summaryToMd, summaryToText,
      formatDuration, statusText, scrollTo,
    };
  },
});
app.mount("#app");
