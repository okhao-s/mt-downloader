const dom = {
  result: null,
  status: null,
  player: null,
  streamPanel: null,
  streamList: null,
  streamCountTag: null,
  output: null,
  jobs: null,
  taskJobs: null,
  taskCountTag: null,
  activeCountTag: null,
  queuedCountTag: null,
  failedCountTag: null,
  doneCountTag: null,
  cancelledCountTag: null,
  retriedCountTag: null,
  historyTabs: null,
  historySummary: null,
  taskPanelSubtitle: null,
  settingsBody: null,
  settingsPanel: null,
  historyPanel: null,
  taskStatusTabs: null,
};

const state = {
  hls: null,
  selectedStreamIndex: null,
  selectedStreamUrl: null,
  latestParseData: null,
  autoFilledOutput: '',
  jobsTimer: null,
  historyFilter: 'all',
  taskFilter: 'active',
};

const API_TIMEOUT_MS = 45000;
const PREVIEW_TIMEOUT_MS = 35000;
const DOWNLOADING_STATUSES = ['downloading'];
const QUEUED_STATUSES = ['queued'];
const RUNNING_STATUSES = [...QUEUED_STATUSES, ...DOWNLOADING_STATUSES];
const HISTORY_DISPLAY_LIMIT = 20;
const TASK_DISPLAY_LIMIT = 12;

function $(id) {
  return document.getElementById(id);
}

function initDom() {
  dom.result = $('result');
  dom.status = $('status-pill');
  dom.player = $('player');
  dom.streamPanel = $('stream-panel');
  dom.streamList = $('stream-list');
  dom.streamCountTag = $('stream-count-tag');
  dom.output = $('output');
  dom.jobs = $('jobs');
  dom.taskJobs = $('task-jobs');
  dom.taskCountTag = $('task-count-tag');
  dom.activeCountTag = $('active-count-tag');
  dom.queuedCountTag = $('queued-count-tag');
  dom.failedCountTag = $('failed-count-tag');
  dom.doneCountTag = $('done-count-tag');
  dom.cancelledCountTag = $('cancelled-count-tag');
  dom.retriedCountTag = $('retried-count-tag');
  dom.historyTabs = $('history-tabs');
  dom.historySummary = $('history-summary');
  dom.taskPanelSubtitle = $('task-panel-subtitle');
  dom.settingsBody = $('settings-body');
  dom.settingsPanel = $('settings-panel');
  dom.historyPanel = $('history-panel');
  dom.taskStatusTabs = $('task-status-tabs');
}

async function api(path, payload, timeoutMs = API_TIMEOUT_MS) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const res = await fetch(path, {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(payload),
      signal: controller.signal,
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `request failed (${res.status})`);
    return data;
  } catch (e) {
    if (e.name === 'AbortError') throw new Error(`请求超时（>${timeoutMs / 1000}s）`);
    throw e;
  } finally {
    clearTimeout(timer);
  }
}

function collect() {
  return {
    url: $('url').value.trim(),
    output: $('output').value.trim(),
    referer: $('referer').value.trim(),
    user_agent: $('user_agent').value.trim(),
    proxy: $('proxy').value.trim(),
    stream_index: state.selectedStreamIndex,
    stream_url: state.selectedStreamUrl,
  };
}

function escapeHtml(value) {
  return String(value ?? '')
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
    .replaceAll("'", '&#39;');
}

function renderSummary(items = [], options = {}) {
  const cardClass = options.variant ? ` summary-card-${options.variant}` : '';
  if (!items.length) {
    dom.result.innerHTML = `<div class="summary-empty${cardClass}">等你贴链接。</div>`;
    return;
  }
  dom.result.innerHTML = items.map(item => `
    <div class="summary-item${item.highlight ? ' summary-item-highlight' : ''}${item.success ? ' summary-item-success' : ''}${cardClass}">
      <div class="summary-label">${escapeHtml(item.label)}</div>
      <div class="summary-value ${item.error ? 'error-text' : ''}${item.success ? ' success-text' : ''}">${escapeHtml(item.value)}</div>
    </div>
  `).join('');
}

function showParseSummary(data) {
  const firstOption = data?.stream_options?.[0] || {};
  renderSummary([
    { label: '当前状态', value: data.stream_count > 1 ? `解析成功，共找到 ${data.stream_count} 个视频` : '解析成功，已找到可下载视频', success: true, highlight: true },
    { label: '标题', value: data?.title || '未抓到标题' },
    { label: '默认文件名', value: $('output').value.trim() || '未生成' },
    { label: '首个视频信息', value: streamMetaText(firstOption) },
    { label: '下一步', value: data.stream_count > 1 ? '点上方视频列表选一个，再预览或下载' : '点上方视频列表预览，然后直接下载' },
  ], { variant: 'success' });
}

function explainFailure(message) {
  const text = String(message || '未知错误');
  if (/403|401|forbidden|unauthorized/i.test(text)) return '源站拒绝访问，通常要补 Referer、User-Agent 或代理。';
  if (/timeout|超时|timed out/i.test(text)) return '超时了，优先试代理、换线路，或者直接下载别预览。';
  if (/not found|未解析到/i.test(text)) return '这页没抠出可用 m3u8，可能不是直播放页，或者页面被风控。';
  if (/hls|manifest|frag/i.test(text)) return 'HLS 预览链路炸了，通常还能试下载，或者换另一个视频流。';
  return '先看原始错误；如果是站点限制，补 Referer / 代理 往往有用。';
}

function showPreviewErrorSummary(message, index) {
  renderSummary([
    { label: '当前状态', value: `视频 ${index + 1} 预览失败`, error: true },
    { label: '错误原因', value: message, error: true },
    { label: '建议处理', value: explainFailure(message) },
  ]);
}

function showConfigSummary(data) {
  renderSummary([
    { label: '当前状态', value: '设置已保存' },
    { label: '默认代理', value: data?.default_proxy || '未设置' },
    { label: '自动重试', value: data?.auto_retry_enabled ? '已开启' : '未开启' },
    { label: '重试策略', value: `${data?.auto_retry_max_attempts ?? 2} 次 / ${data?.auto_retry_delay_seconds ?? 30}s` },
  ]);
}

function applyConfigToForm(data = {}) {
  $('cfg_proxy').value = data?.default_proxy || '';
  $('cfg_auto_retry_enabled').checked = Boolean(data?.auto_retry_enabled);
  $('cfg_auto_retry_delay_seconds').value = Number(data?.auto_retry_delay_seconds ?? 30);
  $('cfg_auto_retry_max_attempts').value = Number(data?.auto_retry_max_attempts ?? 2);
}

async function loadConfig() {
  const res = await fetch('/api/config', { cache: 'no-store' });
  const data = await res.json();
  applyConfigToForm(data);
  return data;
}

function showError(stage, error) {
  const message = error?.message || String(error);
  renderSummary([
    { label: '阶段', value: stage, error: true },
    { label: '错误原因', value: message, error: true },
  ]);
}

function setStatus(text, type = '') {
  dom.status.textContent = text;
  dom.status.className = `status-pill${type ? ` ${type}` : ''}`;
}

function destroyHls() {
  if (state.hls) {
    state.hls.destroy();
    state.hls = null;
  }
}

function resetPlayer() {
  destroyHls();
  dom.player.onerror = null;
  dom.player.onloadedmetadata = null;
  dom.player.pause();
  dom.player.removeAttribute('src');
  dom.player.load();
}

function syncSuggestedFilename(data) {
  const nextAutoName = data?.title ? `${data.title}.mp4` : '';
  if (!nextAutoName) {
    dom.output.value = '';
    state.autoFilledOutput = '';
    return;
  }

  dom.output.value = nextAutoName;
  state.autoFilledOutput = nextAutoName;
}

function formatBytes(bytes) {
  if (!bytes || Number.isNaN(Number(bytes))) return '大小未知';
  const units = ['B', 'KB', 'MB', 'GB', 'TB'];
  let size = Number(bytes);
  let unit = 0;
  while (size >= 1024 && unit < units.length - 1) {
    size /= 1024;
    unit += 1;
  }
  return `${size.toFixed(size >= 100 || unit === 0 ? 0 : size >= 10 ? 1 : 2)} ${units[unit]}`;
}

function formatDuration(seconds) {
  if (!seconds || Number.isNaN(Number(seconds))) return null;
  const total = Math.round(Number(seconds));
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function streamMetaText(option) {
  const parts = [];
  if (option?.resolution) parts.push(option.resolution);
  if (option?.format_note) parts.push(String(option.format_note));
  const duration = formatDuration(option?.duration);
  if (duration) parts.push(duration);
  parts.push(formatBytes(option?.filesize));
  return parts.join(' · ');
}

function renderStreamList(data) {
  const options = data?.stream_options || [];
  const streams = data?.streams || [];
  if (!streams.length) {
    dom.streamPanel.classList.add('hidden');
    dom.streamList.innerHTML = '';
    dom.streamCountTag.textContent = '0 streams';
    return;
  }

  dom.streamPanel.classList.remove('hidden');
  dom.streamCountTag.textContent = `${streams.length} streams`;
  dom.streamList.innerHTML = streams.map((stream, index) => {
    const option = options.find(item => item.url === stream) || { url: stream };
    const active = stream === state.selectedStreamUrl || index === state.selectedStreamIndex;
    return `
      <button class="stream-item ${active ? 'active' : ''}" data-stream-index="${index}">
        <div class="stream-item-title">
          <span>视频 ${index + 1}</span>
          <span>${active ? '当前选中' : '点击预览'}</span>
        </div>
        <div class="stream-item-meta">${streamMetaText(option)}</div>
        <div class="stream-item-url">${stream}</div>
      </button>
    `;
  }).join('');
}

function buildPreviewUrl() {
  const payload = collect();
  return `/api/preview.m3u8?url=${encodeURIComponent(payload.url)}&referer=${encodeURIComponent(payload.referer || '')}&user_agent=${encodeURIComponent(payload.user_agent || '')}&proxy=${encodeURIComponent(payload.proxy || '')}&stream_index=${encodeURIComponent(payload.stream_index ?? '')}&stream_url=${encodeURIComponent(payload.stream_url || '')}&_=${Date.now()}`;
}

function loadPreviewNative(previewUrl) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(reject, new Error(`预览加载超时（${PREVIEW_TIMEOUT_MS / 1000}s）`)), PREVIEW_TIMEOUT_MS);

    const cleanup = () => {
      clearTimeout(timer);
      dom.player.onloadedmetadata = null;
      dom.player.onerror = null;
    };

    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      cleanup();
      fn(value);
    };

    dom.player.onloadedmetadata = () => finish(resolve, true);
    dom.player.onerror = () => finish(reject, new Error('播放器加载失败，请检查流地址、代理或站点防盗链'));
    dom.player.src = previewUrl;
    dom.player.load();
  });
}

function loadPreviewWithHls(previewUrl) {
  return new Promise((resolve, reject) => {
    let settled = false;
    const timer = setTimeout(() => finish(reject, new Error(`预览加载超时（${PREVIEW_TIMEOUT_MS / 1000}s）`)), PREVIEW_TIMEOUT_MS);

    const finish = (fn, value) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      fn(value);
    };

    state.hls = new Hls({
      manifestLoadingTimeOut: PREVIEW_TIMEOUT_MS,
      levelLoadingTimeOut: PREVIEW_TIMEOUT_MS,
      fragLoadingTimeOut: PREVIEW_TIMEOUT_MS,
      enableWorker: true,
      lowLatencyMode: false,
      backBufferLength: 30,
    });
    state.hls.on(Hls.Events.MANIFEST_PARSED, () => finish(resolve, true));
    state.hls.on(Hls.Events.ERROR, (_event, data) => {
      if (!data?.fatal) return;
      finish(reject, new Error(`预览加载失败：${data?.details || 'unknown hls error'}`));
    });
    state.hls.loadSource(previewUrl);
    state.hls.attachMedia(dom.player);
  });
}

async function loadPreview() {
  resetPlayer();
  const previewUrl = buildPreviewUrl();
  if (dom.player.canPlayType('application/vnd.apple.mpegurl')) {
    return loadPreviewNative(previewUrl);
  }
  if (window.Hls && Hls.isSupported()) {
    return loadPreviewWithHls(previewUrl);
  }
  throw new Error('当前浏览器不支持 HLS 预览');
}

function applyParseData(data) {
  state.latestParseData = data;
  state.selectedStreamIndex = null;
  state.selectedStreamUrl = null;
  syncSuggestedFilename(data);
  renderStreamList(data);
  resetPlayer();
  showParseSummary(data);
}

async function parseUrl() {
  try {
    state.selectedStreamIndex = null;
    state.selectedStreamUrl = null;
    renderStreamList(state.latestParseData || null);

    const payload = collect();
    if (!payload.url) throw new Error('先贴链接，别让我猜你脑内 URL。');

    setStatus('解析中…', 'loading');
    const data = await api('/api/parse', payload);
    applyParseData(data);
    setStatus(data.stream_count > 1 ? `解析完成 · ${data.stream_count} 个视频，点击列表预览` : '解析完成 · 点击视频预览', 'success');
  } catch (e) {
    resetPlayer();
    renderStreamList(null);
    showError('parse', e);
    setStatus(`解析失败：${e.message}`, 'error');
  }
}

async function selectStream(index) {
  if (!state.latestParseData?.streams?.[index]) return;
  state.selectedStreamIndex = index;
  state.selectedStreamUrl = state.latestParseData.streams[index];
  renderStreamList(state.latestParseData);
  try {
    setStatus(`加载视频 ${index + 1} 预览…`, 'loading');
    await loadPreview();
    renderSummary([
      { label: '当前状态', value: `视频 ${index + 1} 已选中并完成预览` },
      { label: '将用于下载', value: `视频 ${index + 1}` },
      { label: '输出文件名', value: $('output').value.trim() || '未生成' },
    ]);
    setStatus(`已选视频 ${index + 1}`, 'success');
  } catch (e) {
    showPreviewErrorSummary(e?.message || String(e), index);
    setStatus(`预览失败：${e.message}`, 'error');
  }
}

async function downloadVideo() {
  try {
    const payload = collect();
    if (!payload.url) throw new Error('链接都没填，下载个锤子。');
    if (state.selectedStreamIndex === null || !state.selectedStreamUrl) {
      throw new Error('先在视频列表里点选一个视频，再下载。');
    }
    setStatus('创建下载任务…', 'loading');
    const data = await api('/api/download', payload, 45000);
    renderSummary([
      { label: '当前状态', value: data?.status_text || '任务已创建' },
      { label: '任务编号', value: data?.id || '未知' },
      { label: '输出文件', value: data?.output || '未知文件' },
      { label: '所用视频', value: data?.stream_index !== null && data?.stream_index !== undefined ? `视频 ${Number(data.stream_index) + 1}` : '未标记' },
    ]);
    await refreshJobs();
    setStatus('下载任务已创建', 'success');
  } catch (e) {
    showError('download', e);
    setStatus(`下载失败：${e.message}`, 'error');
  }
}

async function saveConfig() {
  try {
    setStatus('保存设置…', 'loading');
    const data = await api('/api/config', {
      default_proxy: $('cfg_proxy').value.trim(),
      auto_retry_enabled: Boolean($('cfg_auto_retry_enabled').checked),
      auto_retry_delay_seconds: Number($('cfg_auto_retry_delay_seconds').value || 30),
      auto_retry_max_attempts: Number($('cfg_auto_retry_max_attempts').value || 0),
    });
    applyConfigToForm(data);
    showConfigSummary(data);
    setStatus('设置已保存', 'success');
  } catch (e) {
    showError('config', e);
    setStatus(`保存失败：${e.message}`, 'error');
  }
}

async function retryJob(jobId) {
  if (!jobId) return;
  try {
    setStatus('重新创建任务…', 'loading');
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/retry`, {}, 30000);
    await refreshJobs();
    const newJobId = data?.new_job?.id || '未知';
    setStatus(`已重试，新的任务编号：${newJobId}`, 'success');
  } catch (e) {
    setStatus(`重试失败：${e.message}`, 'error');
  }
}

async function deleteJob(jobId) {
  if (!jobId) return;
  try {
    setStatus('删除任务…', 'loading');
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/delete`, {}, 20000);
    await refreshJobs();
    setStatus(data?.cancelling ? '已请求取消任务' : '任务已删除', 'success');
  } catch (e) {
    setStatus(`删除失败：${e.message}`, 'error');
  }
}

async function clearHistory() {
  try {
    setStatus('清空历史…', 'loading');
    const data = await api('/api/jobs/clear-history', {}, 20000);
    await refreshJobs();
    setStatus(`已清空 ${Number(data?.removed || 0)} 条历史`, 'success');
  } catch (e) {
    setStatus(`清空失败：${e.message}`, 'error');
  }
}

function setTaskFilter(filter) {
  state.taskFilter = filter || 'active';
  if (!dom.taskStatusTabs) return;
  dom.taskStatusTabs.querySelectorAll('[data-task-filter]').forEach(button => {
    button.classList.toggle('active', button.dataset.taskFilter === state.taskFilter);
  });
}

function getTaskFilterJobs(groups) {
  switch (state.taskFilter) {
    case 'queued':
      return groups.queuedJobs;
    case 'failed':
      return groups.failedJobs;
    case 'done':
      return groups.doneJobs;
    case 'cancelled':
      return groups.cancelledJobs;
    case 'retried':
      return groups.retriedJobs;
    case 'active':
    default:
      return groups.activeJobs;
  }
}

function taskFilterLabel(filter) {
  const map = {
    active: '下载中任务',
    queued: '等待中任务',
    failed: '失败任务',
    done: '已完成任务',
    cancelled: '已取消任务',
    retried: '已重试任务',
  };
  return map[filter] || '任务';
}

function taskFilterEmptyText(filter) {
  const map = {
    active: '当前没有下载中的任务。',
    queued: '当前没有等待中的任务。',
    failed: '当前没有失败任务。',
    done: '当前没有已完成任务。',
    cancelled: '当前没有已取消任务。',
    retried: '当前没有已重试任务。',
  };
  return map[filter] || '当前没有任务。';
}

function setHistoryFilter(filter) {
  state.historyFilter = filter || 'all';
  if (!dom.historyTabs) return;
  dom.historyTabs.querySelectorAll('[data-filter]').forEach(button => {
    button.classList.toggle('active', button.dataset.filter === state.historyFilter);
  });
}

function jobStatusText(job) {
  const statusMap = {
    queued: '排队中',
    downloading: '下载中',
    done: '已完成',
    failed: '失败',
    cancelled: '已取消',
    retried: '已重试',
  };
  return job?.status_text || statusMap[job?.status] || '未知状态';
}

function formatJobTime(job) {
  return job?.updated_at || job?.finished_at || job?.started_at || job?.created_at || '';
}

function sortJobsByRecent(jobs = []) {
  return [...jobs].sort((a, b) => String(formatJobTime(b)).localeCompare(String(formatJobTime(a))));
}

function summarizeSource(job) {
  const source = String(job?.source_url || '');
  if (!source) return '';
  try {
    const url = new URL(source);
    return `${url.hostname}${url.pathname !== '/' ? url.pathname : ''}`;
  } catch {
    return source;
  }
}

function truncateText(value, max = 120) {
  const text = String(value || '');
  if (text.length <= max) return text;
  return `${text.slice(0, max - 1)}…`;
}

function buildJobCard(job, { compact = false } = {}) {
  const status = job?.status || 'queued';
  const progress = Math.max(0, Math.min(100, Number(job.progress ?? 0)));
  const sourceSummary = truncateText(summarizeSource(job), compact ? 52 : 88);
  const metaText = truncateText(jobStatusText(job), compact ? 92 : 160);
  const retryMeta = Number(job?.retry_count || 0) > 0 ? ` · 第 ${Number(job.retry_count)} 次重试` : '';
  const errorText = job?.error ? truncateText(job.error, compact ? 120 : 240) : '';
  const actionLabel = status === 'downloading' ? '取消' : '删除';
  const retryButton = status === 'failed'
    ? `<button class="btn btn-inline btn-secondary-lite" onclick="retryJob('${escapeHtml(job.id || '')}')">重试</button>`
    : '';

  return `
    <article class="job ${compact ? 'job-compact' : ''} job-status-${escapeHtml(status)}">
      <div class="job-top">
        <strong title="${escapeHtml(job.output || '未命名文件')}">${escapeHtml(job.output || '未命名文件')}</strong>
        <span>${escapeHtml(formatJobTime(job))}</span>
      </div>
      <div class="job-status-row">
        <span class="job-badge ${escapeHtml(status)}">${escapeHtml(jobStatusText(job))}</span>
        <span class="job-progress-text">${progress}%</span>
      </div>
      <div class="job-progress"><div class="job-progress-bar ${escapeHtml(status)}" style="width:${progress}%"></div></div>
      <div class="job-meta-grid">
        <div class="job-meta-text" title="${escapeHtml(job.status_text || '')}">${escapeHtml(metaText + retryMeta)}</div>
        ${sourceSummary ? `<div class="job-source" title="${escapeHtml(job.source_url || '')}">${escapeHtml(sourceSummary)}</div>` : ''}
      </div>
      ${errorText ? `<div class="job-error" title="${escapeHtml(job.error)}">${escapeHtml(errorText)}</div>` : ''}
      <div class="job-actions">
        ${retryButton}
        <button class="btn btn-inline btn-danger" onclick="deleteJob('${escapeHtml(job.id || '')}')">${actionLabel}</button>
      </div>
    </article>
  `;
}

function renderJobList(container, jobs, { compact = false, emptyText = '还没有下载任务。', limit = jobs.length } = {}) {
  if (!container) return;
  if (!jobs.length) {
    container.innerHTML = `<p class="muted">${escapeHtml(emptyText)}</p>`;
    return;
  }
  const visibleJobs = jobs.slice(0, limit);
  container.innerHTML = visibleJobs.map(job => buildJobCard(job, { compact })).join('');
}

function filterHistoryJobs(historyJobs, activeJobs = []) {
  switch (state.historyFilter) {
    case 'active':
      return activeJobs;
    case 'failed':
      return historyJobs.filter(job => job.status === 'failed');
    case 'done':
      return historyJobs.filter(job => job.status === 'done');
    case 'cancelled':
      return historyJobs.filter(job => job.status === 'cancelled');
    case 'all':
    default:
      return historyJobs;
  }
}

function updateHistorySummary({ activeCount, queuedCount, failedCount, doneCount, cancelledCount, retriedCount, shownCount, totalHistoryCount, currentTaskCount }) {
  if (dom.taskCountTag) {
    dom.taskCountTag.textContent = `${currentTaskCount} 条`;
  }
  if (dom.activeCountTag) dom.activeCountTag.textContent = String(activeCount);
  if (dom.queuedCountTag) dom.queuedCountTag.textContent = String(queuedCount);
  if (dom.failedCountTag) dom.failedCountTag.textContent = String(failedCount);
  if (dom.doneCountTag) dom.doneCountTag.textContent = String(doneCount);
  if (dom.cancelledCountTag) dom.cancelledCountTag.textContent = String(cancelledCount);
  if (dom.retriedCountTag) dom.retriedCountTag.textContent = String(retriedCount);
  if (dom.taskPanelSubtitle) {
    dom.taskPanelSubtitle.textContent = `${taskFilterLabel(state.taskFilter)} · 当前显示 ${currentTaskCount} 条`;
  }
  if (dom.historySummary) {
    const filterMap = {
      all: '全部历史',
      active: '活跃任务',
      failed: '失败任务',
      done: '已完成任务',
      cancelled: '已取消任务',
    };
    dom.historySummary.textContent = `${filterMap[state.historyFilter] || '全部历史'} · 显示 ${shownCount}/${totalHistoryCount}`;
  }
}

async function refreshJobs({ silent = false } = {}) {
  const res = await fetch('/api/jobs', { cache: 'no-store' });
  const jobs = await res.json();
  const sortedJobs = sortJobsByRecent(jobs);
  const activeJobs = sortedJobs.filter(job => DOWNLOADING_STATUSES.includes(job.status));
  const queuedJobs = sortedJobs.filter(job => QUEUED_STATUSES.includes(job.status));
  const historyJobs = sortedJobs.filter(job => !RUNNING_STATUSES.includes(job.status));
  const failedJobs = historyJobs.filter(job => job.status === 'failed');
  const doneJobs = historyJobs.filter(job => job.status === 'done');
  const cancelledJobs = historyJobs.filter(job => job.status === 'cancelled');
  const retriedJobs = historyJobs.filter(job => job.status === 'retried');
  const filteredHistoryJobs = filterHistoryJobs(historyJobs, [...activeJobs, ...queuedJobs]);
  const taskGroups = { activeJobs, queuedJobs, failedJobs, doneJobs, cancelledJobs, retriedJobs };
  const taskJobs = getTaskFilterJobs(taskGroups);

  renderJobList(dom.taskJobs, taskJobs, {
    compact: true,
    emptyText: taskFilterEmptyText(state.taskFilter),
    limit: TASK_DISPLAY_LIMIT,
  });
  renderJobList(dom.jobs, filteredHistoryJobs, { emptyText: '还没有符合筛选条件的历史任务。', limit: HISTORY_DISPLAY_LIMIT });

  updateHistorySummary({
    activeCount: activeJobs.length,
    queuedCount: queuedJobs.length,
    failedCount: failedJobs.length,
    doneCount: doneJobs.length,
    cancelledCount: cancelledJobs.length,
    retriedCount: retriedJobs.length,
    shownCount: Math.min(filteredHistoryJobs.length, HISTORY_DISPLAY_LIMIT),
    totalHistoryCount: historyJobs.length,
    currentTaskCount: Math.min(taskJobs.length, TASK_DISPLAY_LIMIT),
  });

  if (!silent) {
    if (activeJobs.length > 0) {
      setStatus(`任务刷新完成 · 下载中 ${activeJobs.length} 个`, 'loading');
    } else if (queuedJobs.length > 0) {
      setStatus(`任务刷新完成 · 等待中 ${queuedJobs.length} 个`, 'loading');
    } else if (failedJobs.length > 0) {
      setStatus(`任务刷新完成 · 有 ${failedJobs.length} 个失败任务待处理`, 'error');
    } else {
      setStatus(`任务刷新完成 · ${taskFilterLabel(state.taskFilter)}`, 'success');
    }
  }

  if (state.jobsTimer) {
    clearTimeout(state.jobsTimer);
    state.jobsTimer = null;
  }
  if (activeJobs.length > 0 || queuedJobs.length > 0) {
    state.jobsTimer = setTimeout(() => refreshJobs({ silent: true }).catch(() => {}), 1500);
  }
}

function toggleSettingsPanel(force) {
  if (!dom.settingsPanel) return;
  const shouldShow = typeof force === 'boolean' ? force : dom.settingsPanel.classList.contains('hidden');
  dom.settingsPanel.classList.toggle('hidden', !shouldShow);
}

function toggleHistoryPanel(force) {
  if (!dom.historyPanel) return;
  const shouldShow = typeof force === 'boolean' ? force : dom.historyPanel.classList.contains('hidden');
  dom.historyPanel.classList.toggle('hidden', !shouldShow);
}

function bindEvents() {
  dom.streamList.addEventListener('click', (event) => {
    const button = event.target.closest('[data-stream-index]');
    if (!button) return;
    selectStream(Number(button.dataset.streamIndex));
  });

  dom.output.addEventListener('input', () => {
    if (dom.output.value.trim() !== state.autoFilledOutput) {
      state.autoFilledOutput = '';
    }
  });

  if (dom.taskStatusTabs) {
    dom.taskStatusTabs.addEventListener('click', (event) => {
      const button = event.target.closest('[data-task-filter]');
      if (!button) return;
      setTaskFilter(button.dataset.taskFilter);
      refreshJobs().catch(() => {});
    });
  }

  if (dom.historyTabs) {
    dom.historyTabs.addEventListener('click', (event) => {
      const button = event.target.closest('[data-filter]');
      if (!button) return;
      setHistoryFilter(button.dataset.filter);
      refreshJobs().catch(() => {});
    });
  }
}

window.parseUrl = parseUrl;
window.downloadVideo = downloadVideo;
window.retryJob = retryJob;
window.deleteJob = deleteJob;
window.clearHistory = clearHistory;
window.saveConfig = saveConfig;
window.toggleSettingsPanel = toggleSettingsPanel;
window.toggleHistoryPanel = toggleHistoryPanel;

window.addEventListener('load', async () => {
  initDom();
  bindEvents();
  setTaskFilter(state.taskFilter);
  setHistoryFilter(state.historyFilter);
  try {
    await loadConfig();
  } catch (_) {}
  refreshJobs().catch(() => {});
});
