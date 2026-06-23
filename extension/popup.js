const logEl = document.getElementById("log");
const saveBtn = document.getElementById("saveBtn");
const scanBtn = document.getElementById("scanBtn");
const exportBtn = document.getElementById("exportBtn");
const copyFallbackBtn = document.getElementById("copyFallbackBtn");
const folderInput = document.getElementById("folder");
const mtBaseUrlInput = document.getElementById("mtBaseUrl");
const mtTokenInput = document.getElementById("mtToken");
const fallbackBox = document.getElementById("fallbackBox");
const fallbackText = document.getElementById("fallbackText");

let activePort = null;
let activeJobId = null;
let lastRenderedEventTs = "";
let seenFailureKeys = new Set();
let lastScanResult = null;
let lastFallbackContent = "";

const MT_CONFIG_STORAGE_KEY = "mtEndpointConfig";
const DEFAULT_MT_BASE_URL = "http://127.0.0.1:9151";

function log(msg) {
  logEl.textContent += `\n${msg}`;
  logEl.scrollTop = logEl.scrollHeight;
}

function resetLog(msg = "开始处理...") {
  logEl.textContent = msg;
}

async function loadMtConfig() {
  return new Promise((resolve) => {
    try {
      chrome.storage.local.get([MT_CONFIG_STORAGE_KEY], (result) => {
        const saved = result?.[MT_CONFIG_STORAGE_KEY] || {};
        resolve({
          baseUrl: String(saved.baseUrl || DEFAULT_MT_BASE_URL),
          token: String(saved.token || "")
        });
      });
    } catch (err) {
      resolve({ baseUrl: DEFAULT_MT_BASE_URL, token: "" });
    }
  });
}

async function saveMtConfig() {
  const payload = {
    baseUrl: String(mtBaseUrlInput?.value || DEFAULT_MT_BASE_URL).trim() || DEFAULT_MT_BASE_URL,
    token: String(mtTokenInput?.value || "").trim()
  };

  return new Promise((resolve) => {
    try {
      chrome.storage.local.set({ [MT_CONFIG_STORAGE_KEY]: payload }, () => resolve(payload));
    } catch (err) {
      resolve(payload);
    }
  });
}

async function initMtConfig() {
  const cfg = await loadMtConfig();
  if (mtBaseUrlInput) mtBaseUrlInput.value = cfg.baseUrl;
  if (mtTokenInput) mtTokenInput.value = cfg.token;
}

function setBusy(isBusy) {
  saveBtn.disabled = isBusy;
  scanBtn.disabled = isBusy;
  exportBtn.disabled = isBusy;
  folderInput.disabled = isBusy;
  if (mtBaseUrlInput) mtBaseUrlInput.disabled = isBusy;
  if (mtTokenInput) mtTokenInput.disabled = isBusy;
  if (copyFallbackBtn) copyFallbackBtn.disabled = false;
}

function sanitizeFileName(name) {
  return String(name || "")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, "_")
    .slice(0, 120);
}

function normalizeFolder(folder) {
  return sanitizeFileName(folder || "my-images") || "my-images";
}

function shellEscapeSingle(value) {
  return String(value || "").replace(/'/g, `'"'"'`);
}

function buildFallbackContent(scan, folder) {
  const host = sanitizeFileName(scan.host || "site") || "site";
  const title = sanitizeFileName(scan.title || "page") || "page";
  const targetDir = `${normalizeFolder(folder)}/${host}/${title}`;
  const links = scan.urls.map((item) => item.url);
  const wgetLines = [
    `mkdir -p '${shellEscapeSingle(targetDir)}'`,
    ...links.map((url, index) => `wget -c --content-disposition -O '${shellEscapeSingle(targetDir)}/${String(index + 1).padStart(3, "0")}' '${shellEscapeSingle(url)}'`)
  ];
  const curlLines = [
    `mkdir -p '${shellEscapeSingle(targetDir)}'`,
    ...links.map((url, index) => `curl -L --fail --output '${shellEscapeSingle(targetDir)}/${String(index + 1).padStart(3, "0")}' '${shellEscapeSingle(url)}'`)
  ];

  return [
    `# Save Current Page Images fallback`,
    `# page: ${scan.pageUrl}`,
    `# total: ${links.length}`,
    ``,
    `## URLS`,
    ...links.map((url, index) => `${index + 1}. ${url}`),
    ``,
    `## wget`,
    ...wgetLines,
    ``,
    `## curl`,
    ...curlLines
  ].join("\n");
}

async function copyText(text) {
  if (!text) return false;
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch (err) {
    try {
      fallbackText.focus();
      fallbackText.select();
      document.execCommand("copy");
      return true;
    } catch (e) {
      return false;
    }
  }
}

async function showFallback(scan, folder, reason = "") {
  if (!scan?.urls?.length) return;
  lastFallbackContent = buildFallbackContent(scan, folder);
  fallbackText.value = lastFallbackContent;
  fallbackBox.classList.add("active");
  const copied = await copyText(lastFallbackContent);
  if (reason) {
    log(reason);
  }
  log(`已生成兜底内容：${scan.urls.length} 条链接${copied ? "，并已自动复制到剪贴板" : ""}`);
  log("兜底里同时带纯链接列表和 wget/curl 命令，浏览器直下不稳时可直接拿去跑。");
}

function connectDownloadPort() {
  if (activePort) {
    try {
      activePort.disconnect();
    } catch (e) {}
  }

  const port = chrome.runtime.connect({ name: "download-monitor" });
  activePort = port;

  port.onDisconnect.addListener(() => {
    if (activePort === port) {
      activePort = null;
      if (activeJobId) {
        log("后台连接已断开，当前窗口不再实时显示进度；已下发的下载仍会继续");
        setBusy(false);
      }
    }
  });

  port.onMessage.addListener(async (message) => {
    if (!message || typeof message !== "object") return;

    if (message.type === "job-error") {
      log(`后台错误：${message.error || "unknown error"}`);
      if (lastScanResult?.urls?.length) {
        await showFallback(lastScanResult, folderInput.value || "my-images", "后台任务失败，已切到兜底导出模式");
      }
      setBusy(false);
      return;
    }

    if (message.type === "batch-started") {
      activeJobId = message.state?.jobId || null;
      renderJobState(message.state, true);
      return;
    }

    if (message.type === "job-missing") {
      log("后台任务不存在，可能 service worker 已重启；请重新执行");
      setBusy(false);
      return;
    }

    if (message.type === "job-state") {
      await renderJobState(message, false);
    }
  });

  return port;
}

async function renderJobState(state, initial) {
  if (!state) return;

  const summary = `状态：已排队 ${state.queued} / 已发起 ${state.started} / 已完成 ${state.completed} / 失败 ${state.failed} / 总数 ${state.total}`;

  if (initial) {
    log(`后台批次已创建：${state.jobId}`);
    log(summary);
  }

  const events = Array.isArray(state.recentEvents) ? state.recentEvents : [];
  let startIndex = 0;
  if (lastRenderedEventTs) {
    const found = events.findIndex((item) => item.ts === lastRenderedEventTs);
    startIndex = found >= 0 ? found + 1 : Math.max(0, events.length - 5);
  }
  const newEvents = events.slice(startIndex);
  newEvents.forEach((event) => {
    log(event.text);
  });
  if (events.length) {
    lastRenderedEventTs = events[events.length - 1].ts;
  }

  const failures = Array.isArray(state.failures) ? state.failures : [];
  failures.forEach((item) => {
    const key = `${item.index}|${item.reason}|${item.ts}`;
    if (seenFailureKeys.has(key)) return;
    seenFailureKeys.add(key);
    log(`失败明细 ${item.index + 1}: ${item.reason}`);
    if (item.filename) {
      log(`目标文件：${item.filename}`);
    }
    if (item.url) {
      log(`原始链接：${item.url}`);
    }
  });

  if (state.status === "completed") {
    log(summary);
    if (state.failed > 0) {
      log("最终成功口径=浏览器 downloads.onChanged -> complete；失败项已自动给出兜底导出。");
      if (lastScanResult?.urls?.length) {
        await showFallback(lastScanResult, folderInput.value || "my-images", "检测到失败项，已自动生成完整兜底内容");
      }
    } else {
      log("全部文件已真实落地完成（以 downloads.onChanged complete 为准）");
    }
    setBusy(false);
  }
}

async function getActiveTab() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error("没拿到当前标签页");
  }
  return tab;
}

async function scanVisibleImagesOnPage(tabId) {
  const results = await chrome.scripting.executeScript({
    target: { tabId },
    func: () => {
      const normalizeUrl = (raw) => {
        if (!raw) return "";
        try {
          return new URL(raw, location.href).href;
        } catch (err) {
          return "";
        }
      };

      const parseUrl = (raw) => {
        try {
          return new URL(raw, location.href);
        } catch (err) {
          return null;
        }
      };

      const isHttpUrl = (url) => /^https?:/i.test(url || "");
      const isImageLikeUrl = (url) => /\.(?:png|jpe?g|gif|webp|bmp|svg|avif)(?:[?#]|$)/i.test(url || "");
      const blockedUrlKeywords = [
        "emoji",
        "smiley",
        "smilies",
        "/avatar/",
        "uc_server/avatar.php",
        "ads",
        "doubleclick",
        "googlesyndication"
      ];
      const blockedClassKeywords = [
        "emoji",
        "smiley",
        "avatar",
        "icon",
        "logo",
        "badge"
      ];
      const blockedAncestorKeywords = [
        "tab",
        "tabs",
        "nav",
        "navbar",
        "menu",
        "header",
        "footer",
        "sidebar",
        "aside",
        "breadcrumb",
        "pager",
        "pagination",
        "toolbar",
        "thumb",
        "thumbnail",
        "gallery-nav",
        "banner",
        "advert",
        "ad-",
        "ads",
        "logo",
        "icon",
        "avatar",
        "profile",
        "author",
        "comment",
        "reply",
        "signature"
      ];
      const preferredContainerKeywords = [
        "content",
        "main",
        "article",
        "post",
        "thread",
        "message",
        "messagebody",
        "postbody",
        "viewthread",
        "forum_content",
        "read",
        "detail",
        "entry",
        "rich_media",
        "pic",
        "image",
        "img",
        "photo",
        "viewer"
      ];
      const hardRejectTags = new Set(["HEADER", "FOOTER", "NAV", "ASIDE"]);

      const getRectArea = (rect) => Math.max(0, rect.width || 0) * Math.max(0, rect.height || 0);
      const isVisible = (el) => {
        if (!el || !el.isConnected) return false;
        const style = getComputedStyle(el);
        if (style.display === "none" || style.visibility === "hidden" || Number(style.opacity || 1) === 0) return false;
        const rect = el.getBoundingClientRect();
        if (!rect.width || !rect.height) return false;
        return true;
      };

      const nodeKeywordText = (el) => {
        if (!el) return "";
        return [
          el.id,
          el.className,
          el.getAttribute?.("role"),
          el.getAttribute?.("aria-label"),
          el.getAttribute?.("data-role"),
          el.getAttribute?.("data-type")
        ].filter(Boolean).join(" ").toLowerCase();
      };

      const hostPriority = (url) => {
        const parsed = parseUrl(url);
        const host = parsed?.hostname?.toLowerCase() || "";
        if (!host) return 0;
        if (host.includes("tu.ymawv.la")) return 4;
        if (host.startsWith("tu.")) return 3;
        if (host === location.hostname.toLowerCase()) return 2;
        return 1;
      };

      const isBlockedByUrl = (url) => blockedUrlKeywords.some((word) => url.toLowerCase().includes(word));

      const collectAnchorImageHref = (el) => {
        const a = el.closest("a[href]");
        if (!a) return "";
        const href = normalizeUrl(a.getAttribute("href") || a.href || "");
        if (!isHttpUrl(href)) return "";
        return isImageLikeUrl(href) ? href : "";
      };

      const getElementCandidates = (img) => {
        return [
          { url: normalizeUrl(img.currentSrc), source: "img.currentSrc" },
          { url: normalizeUrl(img.src), source: "img.src" },
          { url: normalizeUrl(img.getAttribute("data-src")), source: "img.data-src" },
          { url: normalizeUrl(img.getAttribute("data-original")), source: "img.data-original" },
          { url: normalizeUrl(img.getAttribute("data-lazy-src")), source: "img.data-lazy-src" },
          { url: normalizeUrl(img.getAttribute("file")), source: "img.file" },
          { url: normalizeUrl(img.getAttribute("zoomfile")), source: "img.zoomfile" },
          { url: collectAnchorImageHref(img), source: "anchor.href" }
        ].filter((item) => item.url && isHttpUrl(item.url));
      };

      const getBlockedAncestorInfo = (el) => {
        let depth = 0;
        for (let node = el; node && node !== document.body; node = node.parentElement) {
          if (!(node instanceof HTMLElement)) continue;
          if (hardRejectTags.has(node.tagName)) {
            return { blocked: true, reason: `${node.tagName.toLowerCase()} ancestor`, depth };
          }
          const text = nodeKeywordText(node);
          if (blockedAncestorKeywords.some((word) => text.includes(word))) {
            return { blocked: true, reason: `ancestor keyword:${text.slice(0, 120)}`, depth };
          }
          depth += 1;
        }
        return { blocked: false, reason: "", depth: -1 };
      };

      const scoreContainer = (el) => {
        if (!(el instanceof HTMLElement)) return -Infinity;
        if (!isVisible(el)) return -Infinity;
        const rect = el.getBoundingClientRect();
        const area = getRectArea(rect);
        if (area < 40000) return -Infinity;

        const text = nodeKeywordText(el);
        let score = 0;
        if (el.tagName === "MAIN") score += 40;
        if (el.tagName === "ARTICLE") score += 28;
        if (el.id === "content" || el.classList.contains("content")) score += 20;
        if (preferredContainerKeywords.some((word) => text.includes(word))) score += 18;
        if (rect.width >= window.innerWidth * 0.45) score += 18;
        if (rect.height >= window.innerHeight * 0.28) score += 18;
        if (rect.top < window.innerHeight * 0.75) score += 6;
        if (rect.left < window.innerWidth * 0.35) score += 4;
        if (blockedAncestorKeywords.some((word) => text.includes(word))) score -= 50;
        if (hardRejectTags.has(el.tagName)) score -= 80;
        return score;
      };

      const allContainers = [document.body, ...document.querySelectorAll("main, article, section, div, td")];
      const rankedContainers = allContainers
        .map((el) => ({ el, score: scoreContainer(el) }))
        .filter((item) => Number.isFinite(item.score) && item.score > -20)
        .sort((a, b) => b.score - a.score);

      const preferredContainer = rankedContainers[0]?.el || document.body;
      const preferredRect = preferredContainer.getBoundingClientRect();
      const isInsidePreferredContainer = (el) => preferredContainer === document.body || preferredContainer.contains(el);
      const overlapsPreferredContainer = (rect) => {
        return !(rect.right < preferredRect.left || rect.left > preferredRect.right || rect.bottom < preferredRect.top || rect.top > preferredRect.bottom);
      };

      const seen = new Set();
      const items = [];
      const push = (item) => {
        if (!item?.url || !isHttpUrl(item.url)) return;
        if (seen.has(item.url)) return;
        seen.add(item.url);
        items.push(item);
      };

      document.querySelectorAll("img").forEach((img) => {
        if (!(img instanceof HTMLImageElement)) return;
        if (!isVisible(img)) return;

        const rect = img.getBoundingClientRect();
        const area = getRectArea(rect);
        if (area < 900) return;

        const ownText = `${img.className || ""} ${img.id || ""} ${img.alt || ""} ${img.title || ""}`.toLowerCase();
        if (blockedClassKeywords.some((word) => ownText.includes(word))) return;

        const ancestorInfo = getBlockedAncestorInfo(img);
        if (ancestorInfo.blocked) return;

        const candidates = getElementCandidates(img).filter((item) => !isBlockedByUrl(item.url));
        if (!candidates.length) return;

        const insidePreferred = isInsidePreferredContainer(img);
        const overlapPreferred = overlapsPreferredContainer(rect);
        const hostSorted = [...candidates].sort((a, b) => hostPriority(b.url) - hostPriority(a.url));
        const picked = hostSorted[0];
        const priority = hostPriority(picked.url);

        if (!insidePreferred && !overlapPreferred && priority < 3) return;
        if (!insidePreferred && !overlapPreferred && area < 40000) return;
        if (priority < 3 && area < 3600) return;

        let score = area;
        if (insidePreferred) score += 200000000;
        else if (overlapPreferred) score += 100000000;
        score += priority * 50000000;
        score -= ancestorInfo.depth >= 0 ? ancestorInfo.depth * 500 : 0;

        push({
          url: picked.url,
          source: picked.source,
          kind: "img",
          width: Math.round(rect.width),
          height: Math.round(rect.height),
          text: (img.alt || img.title || "").trim().slice(0, 80),
          priority,
          inPreferredContainer: insidePreferred,
          score
        });
      });

      const bgCandidates = [...document.querySelectorAll("body *")]
        .filter((el) => isVisible(el))
        .slice(0, 2500);

      bgCandidates.forEach((el) => {
        if (!(el instanceof HTMLElement)) return;
        const ancestorInfo = getBlockedAncestorInfo(el);
        if (ancestorInfo.blocked) return;

        const rect = el.getBoundingClientRect();
        const area = getRectArea(rect);
        if (area < 2500) return;
        const style = getComputedStyle(el);
        const bg = style.backgroundImage;
        if (!bg || bg === "none") return;

        const matches = [...bg.matchAll(/url\(["']?(.*?)["']?\)/g)];
        matches.forEach((m) => {
          const url = normalizeUrl(m[1]);
          if (!isHttpUrl(url)) return;
          if (isBlockedByUrl(url)) return;

          const insidePreferred = isInsidePreferredContainer(el);
          const overlapPreferred = overlapsPreferredContainer(rect);
          const priority = hostPriority(url);
          if (!insidePreferred && !overlapPreferred && priority < 3) return;
          if (priority < 3 && area < 12000) return;

          let score = area;
          if (insidePreferred) score += 200000000;
          else if (overlapPreferred) score += 100000000;
          score += priority * 50000000;

          push({
            url,
            source: "background-image",
            kind: "background",
            width: Math.round(rect.width),
            height: Math.round(rect.height),
            text: (el.getAttribute("aria-label") || el.title || "").trim().slice(0, 80),
            priority,
            inPreferredContainer: insidePreferred,
            score
          });
        });
      });

      items.sort((a, b) => (b.score || 0) - (a.score || 0));

      const tuPreferredItems = items.filter((item) => item.priority >= 3);
      const finalItems = tuPreferredItems.length ? tuPreferredItems : items;

      return {
        title: document.title || "page",
        host: location.hostname || "site",
        pageUrl: location.href,
        urls: finalItems,
        debug: {
          totalImgs: document.images.length,
          visibleCandidates: items.length,
          finalCount: finalItems.length,
          tuPreferredCount: tuPreferredItems.length,
          imgCount: finalItems.filter((item) => item.kind === "img").length,
          bgCount: finalItems.filter((item) => item.kind === "background").length,
          preferredContainerTag: preferredContainer?.tagName || "BODY",
          preferredContainerId: preferredContainer?.id || "",
          preferredContainerClass: preferredContainer?.className || "",
          sampleSources: finalItems.slice(0, 12)
        }
      };
    }
  });

  return results?.[0]?.result;
}

async function doScan({ reset = true } = {}) {
  if (reset) {
    resetLog("开始扫描...");
  }

  const tab = await getActiveTab();
  const data = await scanVisibleImagesOnPage(tab.id);
  if (!data) {
    throw new Error("页面扫描失败");
  }

  lastScanResult = { ...data, tabId: tab.id };

  const { host, pageUrl, urls, debug } = data;
  log(`已扫描当前已打开页面`);
  log(`站点：${host}`);
  log(`页面：${pageUrl}`);
  log(`找到 ${urls.length} 张候选内容图`);
  if (debug) {
    log(`明细：img ${debug.imgCount}，background-image ${debug.bgCount}，页面总 img ${debug.totalImgs}`);
    log(`正文容器：${debug.preferredContainerTag || "BODY"}#${debug.preferredContainerId || ""}.${String(debug.preferredContainerClass || "").trim() || "(no-class)"}`);
    log(`tu.* 命中：${debug.tuPreferredCount || 0}，扫描候选总数：${debug.visibleCandidates}，最终保留：${debug.finalCount || urls.length}`);
    debug.sampleSources.slice(0, 8).forEach((item, index) => {
      log(`样本 ${index + 1}: ${item.source} | host优先级 ${item.priority || 0} | ${item.width}x${item.height} | ${item.url}`);
    });
  }

  if (!urls.length) {
    log("当前正文区没扫到可下载图片。先确认页面已解密显示，再点一次；如果站点把真图塞进 canvas/video，这版还抓不到。");
  }

  return lastScanResult;
}

async function exportLinks() {
  setBusy(true);
  lastRenderedEventTs = "";
  seenFailureKeys = new Set();
  activeJobId = null;

  try {
    resetLog("开始导出链接...");
    const scan = await doScan({ reset: false });
    if (!scan?.urls?.length) {
      setBusy(false);
      return;
    }

    const folder = normalizeFolder((folderInput.value || "my-images").trim());
    const text = scan.urls
      .map((item, index) => `${index + 1}. ${item.url}`)
      .join("\n");

    const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
    const objectUrl = URL.createObjectURL(blob);
    const host = sanitizeFileName(scan.host || "site") || "site";
    const title = sanitizeFileName(scan.title || "page") || "page";
    const filename = `${folder}/${host}/${title}/current-page-image-links.txt`;

    await chrome.downloads.download({
      url: objectUrl,
      filename,
      conflictAction: "uniquify",
      saveAs: false
    });

    log(`已导出 ${scan.urls.length} 条链接`);
    log(`文件：${filename}`);
    await showFallback(scan, folder, "已同步生成可复制兜底文本");
    setBusy(false);
  } catch (err) {
    log(`导出失败：${err?.message || String(err)}`);
    setBusy(false);
  }
}

async function scanOnly() {
  setBusy(true);
  lastRenderedEventTs = "";
  seenFailureKeys = new Set();
  activeJobId = null;

  try {
    await doScan({ reset: true });
    setBusy(false);
  } catch (err) {
    log(`扫描失败：${err?.message || String(err)}`);
    setBusy(false);
  }
}

function buildMtReservedPayload(scan, folder) {
  return {
    source: "save-page-images-extension",
    pageUrl: scan.pageUrl,
    pageTitle: scan.title,
    pageHost: scan.host,
    suggestedSubdir: normalizeFolder(folder),
    referer: scan.pageUrl,
    links: (scan.urls || []).map((item) => ({
      url: item.url,
      source: item.source || "unknown",
      width: item.width || 0,
      height: item.height || 0,
      priority: item.priority || 0,
      kind: item.kind || "img"
    }))
  };
}

async function pushToMt(mtConfig, payload) {
  const baseUrl = String(mtConfig?.baseUrl || DEFAULT_MT_BASE_URL).trim().replace(/\/$/, "");
  const headers = { "Content-Type": "application/json" };
  const token = String(mtConfig?.token || "").trim();
  if (token) headers["X-MT-Token"] = token;

  const res = await fetch(`${baseUrl}/api/picture/push`, {
    method: "POST",
    headers,
    body: JSON.stringify(payload)
  });

  let data = null;
  try {
    data = await res.json();
  } catch (err) {
    data = null;
  }

  if (!res.ok) {
    const detail = data?.detail || data?.message || `HTTP ${res.status}`;
    throw new Error(detail);
  }

  return data || { ok: true };
}

async function scanAndDownload() {
  resetLog("开始处理...");
  lastRenderedEventTs = "";
  seenFailureKeys = new Set();
  activeJobId = null;
  fallbackBox.classList.remove("active");
  fallbackText.value = "";
  lastFallbackContent = "";
  setBusy(true);

  try {
    const folder = normalizeFolder((folderInput.value || "my-images").trim());
    const scan = await doScan({ reset: false });

    if (!scan?.urls?.length) {
      setBusy(false);
      return;
    }

    const mtConfig = await saveMtConfig();
    const reservedPayload = buildMtReservedPayload(scan, folder);
    window.__MT_RESERVED_PAYLOAD__ = reservedPayload;

    log(`准备推送到 mt：${scan.urls.length} 张`);
    log(`目标子目录：picture/${reservedPayload.suggestedSubdir}/`);
    log(`mt 地址：${mtConfig.baseUrl}`);

    const result = await pushToMt(mtConfig, reservedPayload);
    const accepted = Number(result?.accepted || reservedPayload.links.length || 0);
    const jobId = result?.job?.id || "-";
    const downloadDir = result?.download_dir || `picture/${reservedPayload.suggestedSubdir}`;
    log(`mt 已接收：${accepted} 张`);
    log(`任务 ID：${jobId}`);
    log(`输出目录：${downloadDir}`);

    await showFallback(scan, folder, "已同步推送 mt；同时保留兜底导出内容");
    setBusy(false);
  } catch (err) {
    log(`执行失败：${err?.message || String(err)}`);
    if (lastScanResult?.urls?.length) {
      await showFallback(lastScanResult, folderInput.value || "my-images", "插件流程异常，已直接切到兜底模式");
    }
    setBusy(false);
  }
}

if (copyFallbackBtn) {
  copyFallbackBtn.addEventListener("click", async () => {
    const ok = await copyText(lastFallbackContent || fallbackText.value || "");
    log(ok ? "兜底内容已复制到剪贴板" : "复制失败，请手动全选 fallback 文本");
  });
}

if (mtBaseUrlInput) {
  mtBaseUrlInput.addEventListener("change", saveMtConfig);
  mtBaseUrlInput.addEventListener("blur", saveMtConfig);
}
if (mtTokenInput) {
  mtTokenInput.addEventListener("change", saveMtConfig);
  mtTokenInput.addEventListener("blur", saveMtConfig);
}

initMtConfig();
scanBtn.addEventListener("click", scanOnly);
exportBtn.addEventListener("click", exportLinks);
saveBtn.addEventListener("click", scanAndDownload);
