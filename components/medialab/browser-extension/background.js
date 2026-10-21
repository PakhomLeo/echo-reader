// medialab Session Bridge — background service worker（P2a：Cookie Broker）
//
// 职责：
//  1. 连接本地 medialab 服务的 WebSocket（127.0.0.1，扩展 Origin 自动握手）
//  2. 处理 get_session 消息：校验 URL → 校验域名授权 → 读取指定域 Cookie → 返回
//  3. Cookie 不出浏览器：只把"当前 Chrome 会话中的指定域 Cookie"按需输出
//
// 安全模型（第一版即完整）：
//  - URL 校验：仅 https，拒绝 localhost/私网/云 metadata/组播地址
//  - 域名授权：chrome.permissions（optional_host_permissions），
//    授权动作必须在 popup（用户手势）完成——MV3 要求
//  - 连接 token：服务端握手后下发，仅保存在当前 Service Worker 内存
//  - 只响应被请求域名的 Cookie，不提供全量导出

const DEFAULT_PORT = 19876;
const RECONNECT_MS = 5000;
const HANDSHAKE_TIMEOUT_MS = 3000;

// 需要 Cookie 的平台白名单（域名集合）：首次运行一键授权全部；popup 可编辑
const DEFAULT_WHITELIST = [
  ".bilibili.com", ".bilivideo.com",
  ".douyin.com", ".iesdouyin.com",
  ".youtube.com", ".googlevideo.com", ".ytimg.com", ".google.com",
  ".tiktok.com", ".tiktokv.com", ".tiktokcdn.com", ".byteoversea.com",
  ".vimeo.com", ".twitch.tv", ".instagram.com", ".facebook.com",
  // P4 文章抓取（Q8：授权白名单扩容）
  ".mp.weixin.qq.com", ".zhihu.com", ".toutiao.com", ".x.com", ".twitter.com", ".xiaohongshu.com", ".xhscdn.com",
  ".medium.com", ".juejin.cn", ".sspai.com", ".36kr.com", ".cnblogs.com", ".infoq.cn", ".jianshu.com",
  ".weibo.com", ".weibo.cn",
];

// ---------- 安全校验 ----------

function isPrivateHostname(hostname) {
  const lower = hostname.toLowerCase();
  if (lower === "localhost" || lower.endsWith(".localhost") ||
      lower.endsWith(".local") || lower.endsWith(".internal")) {
    return true;
  }
  if (lower.includes(":")) {
    const bare = lower.startsWith("[") ? lower.slice(1, -1) : lower;
    if (bare === "::1" || bare === "::" || bare === "0:0:0:0:0:0:0:0" || bare === "0:0:0:0:0:0:0:1") return true;
    if (/^fe[89ab]/.test(bare) || /^fc/.test(bare) || /^fd/.test(bare)) return true;
    return false;
  }
  const parts = lower.split(".");
  if (parts.length === 4 && parts.every((p) => /^\d{1,3}$/.test(p))) {
    const [a, b, c, d] = parts.map(Number);
    if (a === 0 || a === 10 || a === 127) return true;
    if (a === 169 && b === 254) return true;             // 云 metadata（169.254.169.254）
    if (a === 172 && b >= 16 && b <= 31) return true;
    if (a === 192 && b === 168) return true;
    if (a === 100 && b >= 64 && b <= 127) return true;   // CGNAT
    if (a >= 224) return true;                           // 组播/保留
    return false;
  }
  return false;
}

function validateUrl(url) {
  let u;
  try {
    u = new URL(url);
  } catch {
    return false;
  }
  if (u.protocol !== "https:") return false;
  if (isPrivateHostname(u.hostname)) return false;
  return true;
}

// ---------- Cookie Broker ----------

async function collectCookies(domains) {
  const out = [];
  const seen = new Set();
  for (const d of new Set(domains)) {
    const key = d.replace(/^\./, "");
    let cookies = [];
    try {
      cookies = await chrome.cookies.getAll({ domain: key });
    } catch (e) {
      console.warn(`[session-bridge] cookies.getAll failed for ${key}: ${e}`);
      continue;
    }
    for (const c of cookies) {
      const exported = {
        domain: c.domain,
        name: c.name,
        value: c.value,
        path: c.path,
        secure: c.secure,
        httpOnly: c.httpOnly,
        sameSite: c.sameSite || "unspecified",
        expirationDate: c.expirationDate || null,
      };
      const identity = JSON.stringify(exported);
      if (!seen.has(identity)) {
        seen.add(identity);
        out.push(exported);
      }
    }
  }
  return out;
}

function validCookieDomains(domains) {
  if (!Array.isArray(domains)) return false;
  return domains.every((domain) => {
    if (typeof domain !== "string") return false;
    const bare = domain.replace(/^\./, "");
    const labels = bare.split(".");
    return bare.length > 0 && bare.length <= 253 && labels.every((label) =>
      label.length > 0 && label.length <= 63 &&
      /^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$/i.test(label)
    ) && !isPrivateHostname(bare);
  });
}

function hostnameInDomains(hostname, domains) {
  const host = hostname.toLowerCase();
  return domains.some((domain) => {
    const bare = domain.replace(/^\./, "").toLowerCase();
    return host === bare || host.endsWith(`.${bare}`);
  });
}

async function ensureAuthorized(origins) {
  return chrome.permissions.contains({ origins });
}

// ---------- 白名单管理 ----------

// 每个域名生成两个 pattern：裸域 + 子域（chrome 的 *.youtube.com 不匹配裸域）
function domainPatterns(d) {
  const bare = d.replace(/^\./, "");
  return [`https://${bare}/*`, `https://*.${bare}/*`];
}

async function getWhitelist() {
  const { whitelist } = await chrome.storage.local.get("whitelist");
  const configured = Array.isArray(whitelist) ? whitelist : [];
  return [...new Set([...DEFAULT_WHITELIST, ...configured])];
}

async function whitelistStatus() {
  const list = await getWhitelist();
  let authorized = 0;
  for (const d of list) {
    const granted = await chrome.permissions.contains({ origins: domainPatterns(d) });
    if (granted) authorized += 1;
  }
  return { domains: list, authorized_count: authorized, total: list.length };
}

// ---------- 消息处理 ----------

async function handleMessage(msg) {
  if (!msg || msg.type !== "get_session") {
    return { ok: false, error: "unsupported_message" };
  }
  if (!state.connectionToken || msg.token !== state.connectionToken) {
    return { ok: false, error: "bad_token" };
  }
  const { url, job_id, domains } = msg;
  if (!validCookieDomains(domains)) {
    return { ok: false, error: "invalid_domains" };
  }
  if (!validateUrl(url)) {
    return { ok: false, error: "invalid_url", url };
  }
  if (!hostnameInDomains(new URL(url).hostname, domains)) {
    return { ok: false, error: "domain_not_allowed" };
  }
  // chrome.permissions 要求 origin pattern 带路径（https://host/*），且覆盖子域名
  const origins = domains.flatMap(domainPatterns);
  const authorized = await ensureAuthorized(origins);
  if (!authorized) {
    await chrome.storage.local.set({ pending_origins: origins });
    return { ok: false, error: "permission_required", origins, url, job_id };
  }
  const cookies = await collectCookies(domains);
  return {
    ok: true,
    job_id,
    url,
    captured_at: Date.now() / 1000,
    user_agent: navigator.userAgent,
    cookies,
  };
}

// ---------- WebSocket 客户端 ----------

const state = {
  ws: null,
  connectionToken: null,
  reconnectTimer: null,
  lastError: null,
  lastErrorAt: null,
  connectedAt: null,
  authenticated: false,
  attempts: 0,
};

function recordError(text) {
  state.lastError = text;
  state.lastErrorAt = new Date().toLocaleTimeString();
}

function connect() {
  if (state.ws && (state.ws.readyState === WebSocket.OPEN || state.ws.readyState === WebSocket.CONNECTING)) {
    return;
  }
  state.connectionToken = null;
  const url = `ws://127.0.0.1:${DEFAULT_PORT}`;
  state.attempts += 1;
  const ws = new WebSocket(url);
  state.ws = ws;
  let opened = false;
  let authenticated = false;
  let handshakeTimer = null;
  ws.onopen = () => {
    if (state.ws !== ws) return;
    opened = true;
    state.authenticated = false;
    state.connectionToken = null;
    handshakeTimer = setTimeout(() => {
      if (!authenticated) {
        recordError("服务端未确认扩展连接");
        try { ws.close(); } catch { /* noop */ }
      }
    }, HANDSHAKE_TIMEOUT_MS);
  };
  ws.onmessage = async (ev) => {
    let msg;
    try {
      msg = JSON.parse(ev.data);
    } catch {
      return;
    }
    if (!msg || typeof msg.type !== "string") {
      return; // 忽略服务端 ping/其他消息
    }
    if (msg.type === "bridge_ready") {
      if (state.ws !== ws) return;
      if (typeof msg.token !== "string" || msg.token.length < 43) {
        recordError("服务端握手无效");
        try { ws.close(); } catch { /* noop */ }
        return;
      }
      authenticated = true;
      state.authenticated = true;
      state.connectionToken = msg.token;
      state.connectedAt = new Date().toLocaleTimeString();
      state.lastError = null;
      state.lastErrorAt = null;
      if (handshakeTimer) clearTimeout(handshakeTimer);
      handshakeTimer = null;
      console.log("[session-bridge] authenticated");
      return;
    }
    if (!authenticated || state.ws !== ws || !state.connectionToken || msg.token !== state.connectionToken) return;
    if (msg.type === "ping") {
      return; // 心跳只做活性探测，不回复（避免被误认成请求响应）
    }
    let reply;
    try {
      reply = await dispatchMessage(msg);
    } catch (e) {
      console.error("[session-bridge] dispatchMessage error", e);
      reply = { ok: false, error: "handler_error", detail: String(e) };
    }
    reply.req_id = msg.req_id; // 回显请求 id，服务端据此匹配响应
    reply.token = state.connectionToken;
    try {
      ws.send(JSON.stringify(reply));
    } catch (e) {
      console.warn("[session-bridge] send failed", e);
    }
  };
  ws.onclose = () => {
    if (handshakeTimer) clearTimeout(handshakeTimer);
    handshakeTimer = null;
    if (state.ws !== ws) return;
    state.ws = null;
    state.authenticated = false;
    state.connectionToken = null;
    state.connectedAt = null;
    if (!opened) {
      recordError("无法连接本地 medialab 服务");
    } else if (!authenticated) {
      recordError("扩展连接未完成服务端握手");
    } else {
      recordError("连接已断开，自动重连中…");
    }
    scheduleReconnect();
  };
  ws.onerror = () => {
    // onclose 会跟随触发并记录错误；这里只确保关闭
    try { ws.close(); } catch { /* noop */ }
  };
}

function scheduleReconnect() {
  if (state.reconnectTimer) return;
  state.reconnectTimer = setTimeout(() => {
    state.reconnectTimer = null;
    connect();
  }, RECONNECT_MS);
}

// MV3：Service Worker 空闲约 30s 会被挂起（WS 随之断开）。
// 用闹钟周期性唤醒并重连，保证本地服务随时可达。
// 最小周期 0.5 分钟（unpacked），尽量缩短半死窗口。
chrome.alarms.create("bridge-keepalive", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "bridge-keepalive") {
    if (!state.ws || state.ws.readyState !== WebSocket.OPEN) {
      connect();
    }
  }
});

// ---------- YouTube 会话铸造（P3b） ----------

// 在目标页上下文执行：读取 INNERTUBE 上下文 + 尝试 WebPoClient 生成 PO Token。
// 注意：WebPoClient 的模块路径/参数随 YouTube 版本变化，失败时降级只返回 visitor_data。
const YT_MINT_FUNC = async () => {
  const result = {
    visitor_data: null, client_version: null, client_name: null,
    po_token: null, error: null,
    page_url: location.href, ready_state: document.readyState,
    has_ytcfg: !!(window.ytcfg && window.ytcfg.get),
  };
  try {
    const ctx = window.ytcfg && window.ytcfg.get
      ? window.ytcfg.get("INNERTUBE_CONTEXT")
      : (window.INNERTUBE_CONTEXT || null);
    if (ctx && ctx.client) {
      result.visitor_data = ctx.client.visitorData || null;
      result.client_version = ctx.client.clientVersion || null;
      result.client_name = ctx.client.clientName || null;
    }
  } catch (e) {
    result.error = String(e);
  }
  if (result.visitor_data) {
    try {
      const mod = await import("https://www.youtube.com/js/desktop_polymer_po_token.js?name=WebPoClient");
      const WebPoClient = mod.WebPoClient;
      if (WebPoClient && result.client_version) {
        const client = new WebPoClient(
          navigator.userAgent, "youtube.com", 0,
          { clientVersion: result.client_version, clientName: result.client_name || "WEB" },
        );
        const token = await client.generateToken({
          deviceId: result.visitor_data,
          visitorData: result.visitor_data,
          clientVersion: result.client_version,
          clientName: result.client_name || "WEB",
        });
        result.po_token = token || null;
      }
    } catch (e) {
      result.error = (result.error ? result.error + " | " : "") + "WebPoClient: " + String(e);
    }
  }
  return result;
};

async function mintYouTubeSession(videoUrl) {
  // 1) 打开（或复用）YouTube 视频页后台标签页，等播放器初始化
  let tab;
  try {
    tab = await chrome.tabs.create({ url: videoUrl, active: false });
  } catch (e) {
    return { ok: false, error: "tab_create_failed: " + String(e) };
  }
  // 2) 等待页面就绪（最多 30s）；world: MAIN 才能读到页面主世界的 window.ytcfg
  const deadline = Date.now() + 30000;
  let minted = null;
  let lastInjectError = null;
  while (Date.now() < deadline) {
    try {
      const res = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        world: "MAIN",
        func: YT_MINT_FUNC,
      });
      minted = res && res[0] && res[0].result;
      if (minted && minted.visitor_data) break;
      if (minted && minted.error) lastInjectError = minted.error;
    } catch (e) {
      lastInjectError = String(e); // 页面未就绪（注入失败），重试
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  let pageInfo = "";
  if (minted) {
    pageInfo = `url=${minted.page_url} ready=${minted.ready_state} has_ytcfg=${minted.has_ytcfg}`;
  }
  try { await chrome.tabs.remove(tab.id); } catch { /* noop */ }
  if (!minted || !minted.visitor_data) {
    return {
      ok: false,
      error: "mint_failed: 无法读取 Visitor Data",
      detail: (minted ? minted.error : lastInjectError) || null,
      page_info: pageInfo,
    };
  }
  return { ok: true, ...minted };
}

const YOUTUBE_MINT_HOSTS = new Set(["www.youtube.com", "youtube.com", "m.youtube.com", "music.youtube.com", "youtu.be"]);

// Only ever open YouTube watch pages for minting; never an arbitrary URL supplied over the bridge.
function youtubeMintUrl(videoUrl, videoId) {
  if (videoUrl) {
    try {
      const parsed = new URL(videoUrl);
      if (parsed.protocol === "https:" && YOUTUBE_MINT_HOSTS.has(parsed.hostname)
          && !parsed.username && !parsed.password) {
        return parsed.href;
      }
    } catch (e) {
      return null;
    }
    return null;
  }
  return /^[A-Za-z0-9_-]{11}$/.test(videoId || "")
    ? `https://www.youtube.com/watch?v=${videoId}`
    : null;
}

async function handleMint(msg) {
  const { video_url, video_id, client } = msg;
  const mintUrl = youtubeMintUrl(video_url, video_id);
  if (!mintUrl) return { ok: false, error: "invalid_mint_target" };
  const session = await mintYouTubeSession(mintUrl);
  if (!session.ok) return session;
  return {
    ok: true,
    provider: "chrome_extension",
    client: client || "mweb",
    token_scope: msg.token_scope || "player",
    visitor_data: session.visitor_data,
    client_version: session.client_version,
    po_token: session.po_token || null,
    session_binding: "visitor",
    minted_at: Date.now() / 1000,
  };
}

const MESSAGE_HANDLERS = {
  get_session: handleMessage,
  mint_yt_token: handleMint,
};

async function dispatchMessage(msg) {
  const handler = MESSAGE_HANDLERS[msg.type];
  if (!handler) return { ok: false, error: "unsupported_message" };
  return await handler(msg);
}

chrome.runtime.onMessage.addListener((msg, _sender, sendResponse) => {
  if (msg && msg.type === "bridge_status") {
    sendResponse({
      connected: !!(state.ws && state.ws.readyState === WebSocket.OPEN && state.authenticated),
      port: DEFAULT_PORT,
      lastError: state.lastError,
      lastErrorAt: state.lastErrorAt,
      connectedAt: state.connectedAt,
      attempts: state.attempts,
    });
    return true;
  }
  if (msg && msg.type === "bridge_reconnect") {
    if (state.ws) { try { state.ws.close(); } catch { /* noop */ } }
    state.ws = null;
    state.authenticated = false;
    state.connectionToken = null;
    connect();
    sendResponse({ ok: true });
    return true;
  }
  if (msg && msg.type === "bridge_whitelist_status") {
    whitelistStatus().then(sendResponse);
    return true;
  }
  if (msg && msg.type === "bridge_default_whitelist") {
    sendResponse({ ok: true, domains: DEFAULT_WHITELIST });
    return true;
  }
  if (msg && msg.type === "bridge_set_whitelist") {
    const domains = (msg.domains || []).map((d) => String(d).trim()).filter(Boolean);
    chrome.storage.local.set({ whitelist: domains });
    sendResponse({ ok: true, total: domains.length });
    return true;
  }
  if (msg && msg.type === "bridge_authorize_all") {
    getWhitelist().then(async (list) => {
      const origins = list.flatMap(domainPatterns);
      const granted = await chrome.permissions.request({ origins });
      if (granted) {
        await chrome.storage.local.remove("pending_origins");
      }
      sendResponse({ ok: true, granted_all: granted });
    }).catch((e) => {
      sendResponse({ ok: false, error: String(e) });
    });
    return true;
  }
  if (msg && msg.type === "mint_yt_token") {
    handleMint(msg).then(sendResponse);
    return true;
  }
  return undefined;
});

connect();

// 首次安装：写入默认白名单
chrome.runtime.onInstalled.addListener(async () => {
  const { whitelist } = await chrome.storage.local.get("whitelist");
  if (!Array.isArray(whitelist)) {
    await chrome.storage.local.set({ whitelist: DEFAULT_WHITELIST });
  }
});
