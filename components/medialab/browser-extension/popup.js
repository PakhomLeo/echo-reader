// medialab Session Bridge — popup（Q1/Q6/Q7/Q8/Q10 重构版）
//
// 信息架构：
//   主视图：自动连接状态 + 未授权时引导卡 + 设置齿轮
//   设置视图：连接 / 平台白名单（默认折叠）/ 关于
// 刷新策略：打开即查 + 3s 轻量轮询连接状态；操作后刷新。

const $ = (id) => document.getElementById(id);

const POLL_MS = 3000;
const FEEDBACK_MS = 2000;

const state = {
  view: "main",
  connected: false,
  pollTimer: null,
  guideClosed: false,
  domains: [],
  authorizedCount: 0,
};

// ---------- 视图切换（Q10：单页双视图） ----------

function showView(name) {
  state.view = name;
  $("view-main").classList.toggle("hidden", name !== "main");
  $("view-settings").classList.toggle("hidden", name !== "settings");
  if (name === "settings") {
    refreshSettings();
  }
}

$("openSettings").addEventListener("click", () => showView("settings"));
$("backMain").addEventListener("click", () => showView("main"));

// ---------- 反馈（Q1/Q3：明显且短暂） ----------

let feedbackTimer = null;

function showFeedback(el, text, kind) {
  clearTimeout(feedbackTimer);
  el.textContent = text;
  el.className = "feedback " + kind;
  el.classList.remove("out");
  feedbackTimer = setTimeout(() => {
    el.classList.add("out");
  }, FEEDBACK_MS);
}

// ---------- 状态轮询（Q6） ----------

function startPolling() {
  stopPolling();
  refreshStatus();
  state.pollTimer = setInterval(() => {
    refreshStatus();
  }, POLL_MS);
}

function stopPolling() {
  if (state.pollTimer) {
    clearInterval(state.pollTimer);
    state.pollTimer = null;
  }
}

async function getBridgeStatus() {
  try {
    return await chrome.runtime.sendMessage({ type: "bridge_status" });
  } catch (e) {
    return null;
  }
}

async function refreshStatus() {
  const st = await getBridgeStatus();
  if (!st) {
    setStatusUI("bad", "扩展后台不可用", "请重新加载扩展");
    return;
  }
  state.connected = !!st.connected;

  if (st.connected) {
    setStatusUI("ok", "已连接服务", "已与本地服务自动握手");
    maybeShowGuide();
  } else {
    setStatusUI("bad", "未连接服务", st.lastError || "正在连接本地服务");
    maybeShowGuide();
    // 唤醒后台重连（幂等，无害）
    chrome.runtime.sendMessage({ type: "bridge_reconnect" }).catch(() => {});
  }

  // 设置页连接详情（Q7：原因只在设置页）
  const port = st.port || 19876;
  $("portDisplay").textContent = String(port);
  $("connDetail").textContent = st.connected
    ? `已连接（${st.connectedAt || ""}）`
    : (st.lastError ? `未连接：${st.lastError}` : "未连接");
}

function setStatusUI(kind, text, sub) {
  const dot = $("statusDot");
  dot.className = "status-dot " + kind;
  $("statusText").textContent = text;
  $("statusSub").textContent = sub || "";
}

// ---------- 首次引导（Q3：按需一次、可关闭） ----------

async function maybeShowGuide() {
  const g = $("setupGuide");
  if (state.guideClosed) {
    g.classList.add("hidden");
    return;
  }
  const ws = await getWhitelistStatus();
  const needAuth = ws && ws.total > 0 && ws.authorized_count < ws.total;
  g.classList.toggle("hidden", !needAuth);
}

async function getWhitelistStatus() {
  try {
    return await chrome.runtime.sendMessage({ type: "bridge_whitelist_status" });
  } catch (e) {
    return null;
  }
}

$("closeGuide").addEventListener("click", () => {
  state.guideClosed = true;
  $("setupGuide").classList.add("hidden");
});

$("authorizeAll").addEventListener("click", async () => {
  await doAuthorizeAll(null);
});

$("reconnect").addEventListener("click", async () => {
  await chrome.runtime.sendMessage({ type: "bridge_reconnect" });
  showFeedback($("settingsFeedback"), "已请求重新连接", "ok");
  setTimeout(refreshStatus, 500);
});


// ---------- 白名单（Q8：默认折叠） ----------

$("toggleDomains").addEventListener("click", () => {
  const wrap = $("toggleDomains").closest(".collapse");
  wrap.classList.toggle("open");
  $("domainList").classList.toggle("hidden");
  if (wrap.classList.contains("open") && !$("domainList").dataset.loaded) {
    renderDomains();
  }
});

async function refreshSettings() {
  const ws = await getWhitelistStatus();
  if (!ws) return;
  state.domains = ws.domains || [];
  state.authorizedCount = ws.authorized_count || 0;
  $("authSummary").textContent = `已授权 ${state.authorizedCount}/${ws.total}`;
  const summary = $("authSummary");
  summary.className = "detail" + (state.authorizedCount < ws.total ? " warn-text" : "");
  if ($("domainList").dataset.loaded) {
    renderDomains();
  }
  const st = await getBridgeStatus();
  $("portDisplay").textContent = String((st && st.port) || 19876);
}

async function renderDomains() {
  const list = state.domains;
  $("domainList").dataset.loaded = "1";
  const authorizedOrigins = (await chrome.permissions.getAll()).origins || [];
  const wrap = $("domainList");
  wrap.innerHTML = "";
  const chips = document.createElement("div");
  chips.className = "chips";
  for (const d of list) {
    const bare = d.replace(/^\./, "");
    const patterns = [`https://${bare}/*`, `https://*.${bare}/*`];
    const granted = patterns.every((p) => authorizedOrigins.includes(p));
    const chip = document.createElement("span");
    chip.className = "chip" + (granted ? " granted" : "");
    chip.textContent = d;
    const rm = document.createElement("button");
    rm.textContent = "×";
    rm.title = "从白名单移除";
    rm.addEventListener("click", async () => {
      const next = list.filter((x) => x !== d);
      await chrome.runtime.sendMessage({ type: "bridge_set_whitelist", domains: next });
      refreshSettings();
    });
    chip.appendChild(rm);
    chips.appendChild(chip);
  }
  wrap.appendChild(chips);
  $("addDomainRow").classList.remove("hidden");
}

async function doAuthorizeAll(feedbackEl) {
  const ws = await getWhitelistStatus();
  const list = (ws && ws.domains && ws.domains.length) ? ws.domains : await getDefaultWhitelist();
  const origins = list.flatMap((d) => {
    const bare = d.replace(/^\./, "");
    return [`https://${bare}/*`, `https://*.${bare}/*`];
  });
  try {
    const granted = await chrome.permissions.request({ origins });
    if (granted) {
      await chrome.storage.local.remove("pending_origins");
      if (feedbackEl) showFeedback(feedbackEl, "✓ 已授权全部平台", "ok");
      $("setupGuide").classList.add("hidden");
      refreshStatus();
      refreshSettings();
    } else {
      if (feedbackEl) showFeedback(feedbackEl, "授权被拒绝（可在权限弹窗中确认）", "err");
    }
  } catch (e) {
    if (feedbackEl) showFeedback(feedbackEl, "授权请求异常：" + String(e), "err");
  }
}

$("authorizeAll2").addEventListener("click", () => doAuthorizeAll($("settingsFeedback")));

async function getDefaultWhitelist() {
  try {
    const resp = await chrome.runtime.sendMessage({ type: "bridge_default_whitelist" });
    if (resp && Array.isArray(resp.domains) && resp.domains.length) return resp.domains;
  } catch (e) { /* 后台不可达时走内置兜底 */ }
  return [".bilibili.com", ".bilivideo.com", ".douyin.com", ".iesdouyin.com",
          ".youtube.com", ".googlevideo.com", ".ytimg.com", ".google.com",
          ".mp.weixin.qq.com", ".zhihu.com", ".toutiao.com", ".x.com",
          ".twitter.com", ".xiaohongshu.com"];
}

$("addDomain").addEventListener("click", async () => {
  const v = $("newDomain").value.trim();
  if (!v) return;
  let d = v.startsWith(".") ? v : "." + v.replace(/^https?:\/\//, "");
  d = d.replace(/\/.*$/, "").toLowerCase();
  const next = state.domains.filter((x) => x !== d);
  next.push(d);
  await chrome.runtime.sendMessage({ type: "bridge_set_whitelist", domains: next });
  $("newDomain").value = "";
  refreshSettings();
});

$("resetWhitelist").addEventListener("click", async () => {
  const defaults = await getDefaultWhitelist();
  await chrome.runtime.sendMessage({ type: "bridge_set_whitelist", domains: defaults });
  showFeedback($("settingsFeedback"), "已恢复默认白名单", "ok");
  refreshSettings();
});

// ---------- 输入聚焦时暂停轮询（Q6） ----------

// ---------- 初始化 ----------

(async function init() {
  $("extId").textContent = chrome.runtime.id || "";
  startPolling();
  // 打开设置视图时同步一次白名单
  refreshSettings();
})();
