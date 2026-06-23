const TERMINAL_STATES = new Set(["completed", "failed"]);
const INTERRUPT_REASON_LABELS = {
  FILE_FAILED: "文件写入失败",
  FILE_ACCESS_DENIED: "文件访问被拒绝",
  FILE_NO_SPACE: "磁盘空间不足",
  FILE_NAME_TOO_LONG: "文件名或路径过长",
  FILE_TOO_LARGE: "文件过大",
  FILE_VIRUS_INFECTED: "文件被浏览器/系统拦截",
  FILE_TRANSIENT_ERROR: "文件系统临时错误",
  NETWORK_FAILED: "网络失败",
  NETWORK_TIMEOUT: "网络超时",
  NETWORK_DISCONNECTED: "网络断开",
  NETWORK_SERVER_DOWN: "服务器不可用",
  NETWORK_INVALID_REQUEST: "请求无效",
  SERVER_FAILED: "服务器响应失败",
  SERVER_BAD_CONTENT: "服务器返回内容异常",
  SERVER_UNAUTHORIZED: "服务器拒绝访问",
  SERVER_CERT_PROBLEM: "证书问题",
  SERVER_FORBIDDEN: "服务器禁止访问",
  USER_CANCELED: "用户取消",
  USER_SHUTDOWN: "浏览器关闭导致中断",
  CRASH: "浏览器崩溃导致中断"
};

const DOWNLOAD_CLEANUP_TIMERS = new Map();
const DOWNLOAD_TO_JOB = new Map();
const JOBS = new Map();
const PORTS = new Set();

function clearCleanupTimer(downloadId) {
  const timer = DOWNLOAD_CLEANUP_TIMERS.get(downloadId);
  if (timer) {
    clearTimeout(timer);
    DOWNLOAD_CLEANUP_TIMERS.delete(downloadId);
  }
}

function scheduleCleanup(downloadId, delayMs = 180000) {
  clearCleanupTimer(downloadId);
  const timer = setTimeout(() => {
    DOWNLOAD_TO_JOB.delete(downloadId);
    clearCleanupTimer(downloadId);
  }, delayMs);
  DOWNLOAD_CLEANUP_TIMERS.set(downloadId, timer);
}

function createId(prefix) {
  return `${prefix}_${Date.now()}_${Math.random().toString(36).slice(2, 10)}`;
}

function sanitizeSegment(name, fallback, maxLen = 60) {
  const cleaned = String(name || "")
    .replace(/[\\/:*?"<>|]/g, "_")
    .replace(/\s+/g, "_")
    .replace(/_+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, maxLen);
  return cleaned || fallback;
}

function getExtFromUrl(url) {
  try {
    const cleanUrl = String(url || "").split("?")[0].split("#")[0];
    const match = cleanUrl.match(/\.([a-zA-Z0-9]{2,5})$/);
    if (match) return match[1].toLowerCase();
  } catch (error) {
    // ignore
  }
  return "jpg";
}

function buildFilename({ folder, host, title, index, url }) {
  const safeFolder = sanitizeSegment(folder || "my-images", "my-images", 40);
  const safeHost = sanitizeSegment(host || "site", "site", 40);
  const safeTitle = sanitizeSegment(title || "page", "page", 60);
  const ext = sanitizeSegment(getExtFromUrl(url), "jpg", 6);
  return `${safeFolder}/${safeHost}/${safeTitle}/${String(index + 1).padStart(3, "0")}.${ext}`;
}

function snapshotJob(job) {
  if (!job) return null;
  return {
    type: "job-state",
    jobId: job.id,
    status: job.status,
    tabId: job.tabId,
    pageUrl: job.pageUrl,
    pageTitle: job.title,
    total: job.total,
    queued: job.stats.queued,
    started: job.stats.started,
    completed: job.stats.completed,
    failed: job.stats.failed,
    active: job.active.size,
    createdAt: job.createdAt,
    updatedAt: job.updatedAt,
    recentEvents: job.events.slice(-20),
    failures: job.failures.slice(-20)
  };
}

function postToPort(port, payload) {
  try {
    port.postMessage(payload);
  } catch (error) {
    // ignore disconnected port
  }
}

function broadcastJob(job) {
  const payload = snapshotJob(job);
  if (!payload) return;
  PORTS.forEach((port) => {
    if (port.jobId === job.id) {
      postToPort(port, payload);
    }
  });
}

function pushEvent(job, level, text, extra = {}) {
  if (!job) return;
  const event = {
    ts: new Date().toISOString(),
    level,
    text,
    ...extra
  };
  job.events.push(event);
  if (job.events.length > 200) {
    job.events.splice(0, job.events.length - 200);
  }
  job.updatedAt = Date.now();
  broadcastJob(job);
}

function registerFailure(job, item, reason, extra = {}) {
  if (!job || !item) return;
  const failure = {
    index: item.index,
    url: item.url,
    filename: item.filename,
    reason,
    ts: new Date().toISOString(),
    ...extra
  };
  job.failures.push(failure);
  if (job.failures.length > 100) {
    job.failures.splice(0, job.failures.length - 100);
  }
}

function maybeFinalizeJob(job) {
  if (!job) return;
  if (job.stats.completed + job.stats.failed !== job.total) return;
  if (job.active.size) return;
  if (job.queueIndex < job.items.length) return;
  if (job.status === "completed") return;

  job.status = "completed";
  job.updatedAt = Date.now();
  pushEvent(job, job.stats.failed ? "warn" : "info", `批次结束：已完成 ${job.stats.completed}，失败 ${job.stats.failed}`);
}

function markItemFailed(job, item, reason, extra = {}) {
  if (!job || !item || TERMINAL_STATES.has(item.state)) return;
  if (item.state === "started") {
    job.active.delete(item.id);
  }
  if (item.state === "queued") {
    job.stats.queued = Math.max(0, job.stats.queued - 1);
  }
  item.state = "failed";
  item.error = reason;
  job.stats.failed += 1;
  job.updatedAt = Date.now();
  registerFailure(job, item, reason, extra);
  pushEvent(job, "error", `失败 ${item.index + 1}/${job.total}：${reason}`, {
    index: item.index,
    url: item.url,
    filename: item.filename,
    ...extra
  });
}

function formatInterruptReason(reason) {
  if (!reason) return "下载中断";
  return INTERRUPT_REASON_LABELS[reason] || `下载中断（${reason}）`;
}

async function createDownloadAndTrack(job, item) {
  pushEvent(job, "info", `开始原生下载 ${item.index + 1}/${job.total}：${item.url}`, {
    index: item.index,
    url: item.url,
    filename: item.filename
  });

  try {
    const downloadId = await chrome.downloads.download({
      url: item.url,
      filename: item.filename,
      conflictAction: "uniquify",
      saveAs: false
    });

    if (!Number.isInteger(downloadId)) {
      throw new Error("downloads.download returned empty id");
    }

    item.downloadId = downloadId;
    item.finalUrl = item.url;
    item.state = "started";
    job.stats.queued = Math.max(0, job.stats.queued - 1);
    job.stats.started += 1;
    job.active.add(item.id);
    DOWNLOAD_TO_JOB.set(downloadId, { jobId: job.id, itemId: item.id });
    scheduleCleanup(downloadId);
    pushEvent(job, "info", `已发起 ${item.index + 1}/${job.total}：${item.filename}`, {
      index: item.index,
      url: item.url,
      filename: item.filename,
      downloadId,
      finalUrl: item.finalUrl
    });
    return true;
  } catch (error) {
    const message = error?.message || String(error);
    const hint = /filename|path/i.test(message)
      ? "（可疑：下载路径/文件名不合法或过长）"
      : /denied|permission|forbidden/i.test(message)
        ? "（可疑：浏览器/系统权限或站点拒绝）"
        : "";
    markItemFailed(job, item, `${message}${hint}`, {
      finalUrl: item.url
    });
    return false;
  }
}

async function processJob(jobId) {
  const job = JOBS.get(jobId);
  if (!job || job.processing) return;
  job.processing = true;

  try {
    while (job.queueIndex < job.items.length) {
      const item = job.items[job.queueIndex];
      job.queueIndex += 1;
      await createDownloadAndTrack(job, item);
    }
  } finally {
    job.processing = false;
    maybeFinalizeJob(job);
  }
}

async function startBatchDownload(payload) {
  const urls = Array.isArray(payload?.urls) ? payload.urls : [];
  const pageUrl = payload?.pageUrl;
  if (!pageUrl || !urls.length) {
    throw new Error("missing batch download params");
  }

  const job = {
    id: createId("job"),
    status: "running",
    createdAt: Date.now(),
    updatedAt: Date.now(),
    folder: payload.folder || "my-images",
    host: payload.host || "site",
    title: payload.title || "page",
    tabId: Number.isInteger(payload.tabId) ? payload.tabId : null,
    pageUrl,
    total: urls.length,
    queueIndex: 0,
    processing: false,
    active: new Set(),
    events: [],
    failures: [],
    stats: {
      queued: urls.length,
      started: 0,
      completed: 0,
      failed: 0
    },
    items: urls.map((entry, index) => ({
      id: createId("item"),
      index,
      url: typeof entry === "string" ? entry : entry.url,
      source: typeof entry === "string" ? "legacy-url" : (entry.source || "unknown"),
      filename: buildFilename({
        folder: payload.folder,
        host: payload.host,
        title: payload.title,
        index,
        url: typeof entry === "string" ? entry : entry.url
      }),
      useOriginHeader: Boolean(payload.useOriginHeader),
      state: "queued",
      downloadId: null,
      error: ""
    }))
  };

  JOBS.set(job.id, job);
  pushEvent(job, "info", `批次已创建：共 ${job.total} 个文件，等待后台下载`, { total: job.total });
  processJob(job.id).catch((error) => {
    pushEvent(job, "error", `后台批处理异常：${error?.message || String(error)}`);
  });
  return snapshotJob(job);
}

function getJobState(jobId) {
  const job = JOBS.get(jobId);
  if (!job) {
    return { type: "job-missing", jobId, error: "job not found" };
  }
  return snapshotJob(job);
}

chrome.downloads.onChanged.addListener((delta) => {
  if (!delta || typeof delta.id !== "number") return;

  if (delta.state?.current === "complete" || delta.state?.current === "interrupted") {
    clearCleanupTimer(delta.id);
  }

  const ref = DOWNLOAD_TO_JOB.get(delta.id);
  if (!ref) return;

  const job = JOBS.get(ref.jobId);
  const item = job?.items.find((entry) => entry.id === ref.itemId);
  if (!job || !item) {
    DOWNLOAD_TO_JOB.delete(delta.id);
    return;
  }

  if (delta.filename?.current) {
    item.finalFilename = delta.filename.current;
  }

  if (delta.state?.current === "complete" && item.state !== "completed") {
    job.active.delete(item.id);
    item.state = "completed";
    job.stats.completed += 1;
    job.updatedAt = Date.now();
    pushEvent(job, "success", `已完成 ${item.index + 1}/${job.total}：${item.finalFilename || item.filename}`, {
      index: item.index,
      url: item.url,
      filename: item.finalFilename || item.filename,
      downloadId: delta.id,
      finalUrl: item.finalUrl || item.url
    });
    DOWNLOAD_TO_JOB.delete(delta.id);
    maybeFinalizeJob(job);
    return;
  }

  if (delta.state?.current === "interrupted") {
    const reason = formatInterruptReason(delta.error?.current || "");
    markItemFailed(job, item, reason, {
      downloadId: delta.id,
      interruptReason: delta.error?.current || "",
      finalUrl: item.finalUrl || item.url
    });
    DOWNLOAD_TO_JOB.delete(delta.id);
    maybeFinalizeJob(job);
  }
});

chrome.runtime.onInstalled.addListener(async () => {
  DOWNLOAD_TO_JOB.clear();
  JOBS.clear();
  for (const downloadId of DOWNLOAD_CLEANUP_TIMERS.keys()) {
    clearCleanupTimer(downloadId);
  }
});

chrome.runtime.onConnect.addListener((port) => {
  if (port.name !== "download-monitor") return;

  PORTS.add(port);
  port.onDisconnect.addListener(() => {
    PORTS.delete(port);
  });

  port.onMessage.addListener(async (message) => {
    if (!message || typeof message !== "object") return;

    try {
      if (message.type === "start-batch-download") {
        const state = await startBatchDownload(message.payload || {});
        port.jobId = state.jobId;
        postToPort(port, { type: "batch-started", state });
        return;
      }

      if (message.type === "subscribe-job") {
        port.jobId = message.jobId;
        postToPort(port, getJobState(message.jobId));
        return;
      }

      if (message.type === "get-job-state") {
        postToPort(port, getJobState(message.jobId || port.jobId));
      }
    } catch (error) {
      postToPort(port, {
        type: "job-error",
        error: error?.message || String(error)
      });
    }
  });
});
