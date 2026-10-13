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
      { icon: "🔒", title: "隐私安全", desc: "文件 2 小时自动清理，不留存任何个人数据" },
      { icon: "✨", title: "AI 视频总结", desc: "一键生成视频内容摘要（会员专享，即将上线）" },
    ];
    const faqs = [
      { q: "支持哪些平台？", a: "基于 yt-dlp 开源引擎，支持哔哩哔哩、抖音、快手、微博、YouTube、Instagram、TikTok 等 1800+ 网站；少数平台（如小红书）暂不支持。" },
      { q: "真的是完全免费吗？", a: "是。视频下载与文本提取不限次数、不限清晰度、无需登录；仅后续上线的 AI 视频总结为会员功能。" },
      { q: "下载的文件会保存多久？", a: "文件在服务器临时保留 2 小时后自动删除，请下载完成后及时保存到本地设备。" },
      { q: "为什么有些视频解析失败？", a: "常见原因：链接无效、平台限制需要登录、内容为私有或地域受限。需要登录的内容（如 B 站 1080P+ 部分）计划二期支持 Cookie。" },
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
    const showTextModal = ref(false);

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
      if (!r.ok) throw new Error(d.detail || `请求失败（${r.status}）`);
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
      const u = url.value.trim();
      if (!u) { toast("请先粘贴视频链接", "error"); return; }
      if (!/^https?:\/\//.test(u)) { toast("链接需以 http(s):// 开头", "error"); return; }
      parsing.value = true;
      stopPoll();
      resetDl();
      try {
        const info = await api("/api/parse", { url: u });
        videoInfo.value = info;
        const q = info.qualities.find(x => x.value === "1080") || info.qualities[0];
        selectedQuality.value = q ? q.value : "";
        scrollTo("#result");
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
      textLoading.value = true;
      try {
        const r = await api("/api/extract-text", {
          url: videoInfo.value?.webpage_url || url.value.trim(),
        });
        Object.assign(textResult, r);
        showTextModal.value = true;
      } catch (e) {
        toast(e.message, "error");
      } finally {
        textLoading.value = false;
      }
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
    function onSummarizeClick() {
      if (!isVip.value) { openPay(); return; }
      toast("AI 视频总结将在阶段 4 上线，敬请期待 ✨");
    }

    return {
      platforms, features, faqs,
      url, parsing, videoInfo, selectedQuality, resultSection, openFaq,
      dl, textLoading, textResult, showTextModal,
      showPayModal, payTab, vipCode, isVip, toasts,
      parseVideo, startDownload, resetDownload, extractText, copyText, downloadTxt,
      openPay, mockPaySuccess, activateVip, onSummarizeClick,
      formatDuration, statusText, scrollTo,
    };
  },
});
app.mount("#app");
