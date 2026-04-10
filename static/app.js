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
  jobClockTimer: null,
  latestJobsSnapshot: [],
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

function collectLiveRecordPayload() {
  return {
    url: $('url').value.trim(),
    stream_url: state.selectedStreamUrl || '',
    output: $('output').value.trim(),
    referer: $('referer').value.trim(),
    user_agent: $('user_agent').value.trim(),
    proxy: $('proxy').value.trim(),
    segment_minutes: Number($('live_segment_minutes')?.value || 0),
    max_reconnect_attempts: Number($('live_max_reconnect_attempts')?.value || 0),
    restart_delay_seconds: Number($('cfg_record_restart_delay_seconds')?.value || 5),
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
  if (data?.media_type === 'image') {
    renderSummary([
      { label: '当前状态', value: `解析成功，共找到 ${Number(data?.image_count || (data?.images || []).length || 0)} 张图片`, success: true, highlight: true },
      { label: '标题', value: data?.title || '未抓到标题' },
      { label: '默认文件名前缀', value: $('output').value.trim() || '未生成' },
      { label: '下载目录', value: '/downloads/image' },
      { label: '下一步', value: '这是图片帖，直接点开始下载就行。' },
    ], { variant: 'success' });
    return;
  }
  const preferredIndex = getPreferredStreamIndex(data);
  const preferredOption = preferredIndex !== null ? ((data?.stream_options || [])[preferredIndex] || {}) : {};
  const mediaCount = Number(data?.media_entries?.length || 0);
  const videoCount = Number(data?.stream_count || 0);
  const isUaaLive = data?.platform === 'uaa';
  const multiVideoPost = isXUrl(data?.source_url) && mediaCount > 1;
  const isSingleHighest = !multiVideoPost && (isUaaLive || shouldCollapseToBestOnly(data?.source_url) || videoCount <= 1);
  renderSummary([
    { label: '当前状态', value: multiVideoPost ? `解析成功，共找到 ${mediaCount} 个视频媒体` : (isSingleHighest ? '解析成功，已锁定最高画质' : `解析成功，共找到 ${videoCount} 个视频`), success: true, highlight: true },
    { label: '标题', value: data?.title || '未抓到标题' },
    { label: '默认文件名', value: $('output').value.trim() || '未生成' },
    { label: multiVideoPost ? '当前选中' : (isSingleHighest ? '当前画质' : '推荐画质'), value: multiVideoPost ? `视频 ${Number((preferredIndex ?? 0) + 1)}` : streamMetaText(preferredOption) },
    { label: '下一步', value: multiVideoPost ? '这是多视频帖子，点上方列表选第几个视频，再预览或下载。' : (isSingleHighest ? '直接下载/录制就行。' : '点上方视频列表选一个，再预览或下载') },
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
    { label: '直播录制', value: `切片 ${Number(data?.record_segment_minutes ?? 30)} 分钟 / 重连 ${Number(data?.record_max_reconnect_attempts ?? 6)} 次 / 等待 ${Number(data?.record_restart_delay_seconds ?? 5)}s` },
    { label: '企业微信', value: data?.wecom_ready ? '配置已就绪' : (data?.wecom_enabled ? '已开启，但参数还没填完整' : '未启用') },
  ]);
}

function updateTwitterCookiesHint(data = {}) {
  const hint = $('twitter-cookies-hint');
  if (!hint) return;
  const path = data?.xck || data?.twitter_cookies_path || '/app/data/cookies/twitter.cookies.txt';
  hint.textContent = data?.twitter_cookies_exists
    ? `已检测到 cookies：${path}。X/Twitter 下载会自动带上。`
    : `未上传 cookies，X/Twitter 先按游客模式试。建议上传浏览器导出的 cookies.txt。`;
}

function updateYouTubeCookiesHint(data = {}) {
  const hint = $('youtube-cookies-hint');
  if (!hint) return;
  const path = data?.youtubeck || data?.youtube_cookies_path || '/app/data/cookies/youtube.cookies.txt';
  hint.textContent = data?.youtube_cookies_exists
    ? `已检测到 cookies：${path}。YouTube 下载会自动带上。`
    : `未上传 cookies，YouTube 先按公开视频模式试。遇到年龄限制/私密限制再补 cookies。`;
}

function updateBilibiliCookiesHint(data = {}) {
  const hint = $('bilibili-cookies-hint');
  if (!hint) return;
  const path = data?.bilibilick || data?.bilibili_cookies_path || '/app/data/cookies/bilibili.cookies.txt';
  hint.textContent = data?.bilibili_cookies_exists
    ? `已检测到 cookies：${path}。Bilibili 解析和下载会自动带上。`
    : `未上传 cookies，Bilibili 可能触发 412 或拿不到视频流。建议上传浏览器导出的 cookies.txt。`;
}

function updateWecomHints(data = {}) {
  const status = $('wecom-status-hint');
  if (status) {
    if (data?.wecom_forward_enabled) {
      status.textContent = '已启用自定义企业微信转发：started/done/failed 主动通知会优先走转发地址。';
    } else {
      status.textContent = data?.wecom_ready
        ? '企业微信配置已就绪，可以去企业微信里校验回调并开始收消息。'
        : '企业微信配置未完成。把参数填完整后再保存。';
    }
  }

  const secretHint = $('wecom-secret-hint');
  if (secretHint) {
    secretHint.textContent = data?.wecom_secret_masked
      ? `已保存：${data.wecom_secret_masked}。留空并保存可清空。`
      : '未保存。Secret 只在保存时写入，接口返回会做掩码。';
  }

  const tokenHint = $('wecom-token-hint');
  if (tokenHint) {
    tokenHint.textContent = data?.wecom_token_masked
      ? `已保存：${data.wecom_token_masked}。留空并保存可清空。`
      : '未保存。Token 只在保存时写入，接口返回会做掩码。';
  }

  const aesHint = $('wecom-aes-hint');
  if (aesHint) {
    aesHint.textContent = data?.wecom_encoding_aes_key_masked
      ? `已保存：${data.wecom_encoding_aes_key_masked}。留空并保存可清空。`
      : '未保存。EncodingAESKey 只在保存时写入，接口返回会做掩码。';
  }

  const forwardTokenHint = $('wecom-forward-token-hint');
  if (forwardTokenHint) {
    forwardTokenHint.textContent = data?.wecom_forward_token_masked
      ? `已保存：${data.wecom_forward_token_masked}。留空并保存可清空。`
      : '未保存。转发 Token 只在保存时写入，接口返回会做掩码。';
  }
}

function applyConfigToForm(data = {}) {
  $('cfg_proxy').value = data?.default_proxy || '';
  $('cfg_auto_retry_enabled').checked = Boolean(data?.auto_retry_enabled);
  $('cfg_auto_retry_delay_seconds').value = Number(data?.auto_retry_delay_seconds ?? 30);
  $('cfg_auto_retry_max_attempts').value = Number(data?.auto_retry_max_attempts ?? 2);
  if ($('cfg_xck')) {
    $('cfg_xck').value = data?.xck || data?.twitter_cookies_path || '/app/data/cookies/twitter.cookies.txt';
  }
  if ($('cfg_record_segment_minutes')) {
    $('cfg_record_segment_minutes').value = Number(data?.record_segment_minutes ?? 30);
  }
  if ($('cfg_record_max_reconnect_attempts')) {
    $('cfg_record_max_reconnect_attempts').value = Number(data?.record_max_reconnect_attempts ?? 6);
  }
  if ($('cfg_record_restart_delay_seconds')) {
    $('cfg_record_restart_delay_seconds').value = Number(data?.record_restart_delay_seconds ?? 5);
  }
  if ($('live_segment_minutes')) {
    $('live_segment_minutes').value = Number(data?.record_segment_minutes ?? 30);
  }
  if ($('live_max_reconnect_attempts')) {
    $('live_max_reconnect_attempts').value = Number(data?.record_max_reconnect_attempts ?? 6);
  }
  if ($('cfg_youtubeck')) {
    $('cfg_youtubeck').value = data?.youtubeck || data?.youtube_cookies_path || '/app/data/cookies/youtube.cookies.txt';
  }
  if ($('cfg_bilibilick')) {
    $('cfg_bilibilick').value = data?.bilibilick || data?.bilibili_cookies_path || '/app/data/cookies/bilibili.cookies.txt';
  }
  if ($('cfg_wecom_enabled')) {
    $('cfg_wecom_enabled').checked = Boolean(data?.wecom_enabled);
  }
  if ($('cfg_wecom_corp_id')) {
    $('cfg_wecom_corp_id').value = data?.wecom_corp_id || '';
  }
  if ($('cfg_wecom_agent_id')) {
    $('cfg_wecom_agent_id').value = data?.wecom_agent_id || '';
  }
  if ($('cfg_wecom_secret')) {
    $('cfg_wecom_secret').value = '';
  }
  if ($('cfg_wecom_token')) {
    $('cfg_wecom_token').value = '';
  }
  if ($('cfg_wecom_encoding_aes_key')) {
    $('cfg_wecom_encoding_aes_key').value = '';
  }
  if ($('cfg_wecom_callback_url')) {
    $('cfg_wecom_callback_url').value = data?.wecom_callback_url || '';
  }
  if ($('cfg_wecom_forward_url')) {
    $('cfg_wecom_forward_url').value = data?.wecom_forward_url || '';
  }
  if ($('cfg_wecom_forward_token')) {
    $('cfg_wecom_forward_token').value = '';
  }
  updateTwitterCookiesHint(data);
  updateYouTubeCookiesHint(data);
  updateBilibiliCookiesHint(data);
  updateWecomHints(data);
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
  const nextAutoName = String(data?.suggested_output || '').trim();
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

function normalizeResolution(option = {}) {
  const width = Number(option?.width || 0);
  const height = Number(option?.height || 0);
  if (width > 0 && height > 0) {
    const roundedWidth = Math.round(width / 2) * 2;
    const roundedHeight = Math.round(height / 2) * 2;
    return `${roundedWidth}x${roundedHeight}`;
  }
  return option?.resolution || '';
}

function streamMetaText(option) {
  const parts = [];
  const resolution = normalizeResolution(option);
  if (resolution) parts.push(resolution);
  if (option?.format_note) parts.push(String(option.format_note));
  const duration = formatDuration(option?.duration);
  if (duration) parts.push(duration);
  parts.push(formatBytes(option?.filesize));
  return parts.join(' · ');
}

function getPreferredStreamIndex(data) {
  const streams = data?.streams || [];
  const options = data?.stream_options || [];
  if (!streams.length) return null;

  let bestIndex = 0;
  let bestScore = -1;
  for (let index = 0; index < streams.length; index += 1) {
    const stream = streams[index];
    const option = options.find(item => item.url === stream) || {};
    const width = Number(option?.width || 0);
    const height = Number(option?.height || 0);
    const pixels = width * height;
    const tbr = Number(option?.tbr || 0);
    const filesize = Number(option?.filesize || option?.filesize_approx || 0);
    const score = pixels * 1000000 + tbr * 1000 + filesize;
    if (score > bestScore) {
      bestScore = score;
      bestIndex = index;
    }
  }
  return bestIndex;
}

function isXUrl(url = '') {
  return /https?:\/\/(?:www\.)?(?:x\.com|twitter\.com)\//i.test(String(url || ''));
}

function isYouTubeUrl(url = '') {
  return /https?:\/\/(?:www\.)?(?:youtube\.com|youtu\.be)\//i.test(String(url || ''));
}

function isBilibiliUrl(url = '') {
  return /https?:\/\/(?:www\.)?(?:bilibili\.com|b23\.tv)\//i.test(String(url || ''));
}

function shouldCollapseToBestOnly(url = '', data = null) {
  if (isXUrl(url) && Number(data?.media_entries?.length || 0) > 1) return false;
  return isXUrl(url) || isYouTubeUrl(url) || isBilibiliUrl(url);
}

function collapseStreamsForDisplay(data) {
  if (!data || !shouldCollapseToBestOnly(data?.source_url, data)) return data;
  const preferredIndex = getPreferredStreamIndex(data);
  if (preferredIndex === null || !data?.streams?.[preferredIndex]) return data;
  const preferredUrl = data.streams[preferredIndex];
  const preferredOption = (data.stream_options || []).find(item => item.url === preferredUrl);
  return {
    ...data,
    streams: [preferredUrl],
    stream_options: preferredOption ? [preferredOption] : [],
    stream_count: 1,
    preferred_stream_original_index: preferredIndex,
  };
}

function renderStreamList(data) {
  const options = data?.stream_options || [];
  const streams = data?.streams || [];
  if (data?.media_type === 'image') {
    dom.streamPanel.classList.remove('hidden');
    dom.streamCountTag.textContent = `${Number(data?.image_count || (data?.images || []).length || 0)} images`;
    dom.streamList.innerHTML = `<div class="summary-empty">这是图片帖，不需要选视频流，直接下载。</div>`;
    return;
  }
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
          <span>${shouldCollapseToBestOnly(data?.source_url, data) || data?.platform === 'uaa' ? '最高画质' : `视频 ${index + 1}`}</span>
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
  const displayData = collapseStreamsForDisplay(data);
  state.latestParseData = displayData;
  const preferredIndex = getPreferredStreamIndex(displayData);
  if (preferredIndex !== null && displayData?.streams?.[preferredIndex]) {
    state.selectedStreamIndex = preferredIndex;
    state.selectedStreamUrl = displayData.streams[preferredIndex];
  } else {
    state.selectedStreamIndex = null;
    state.selectedStreamUrl = null;
  }
  syncSuggestedFilename(displayData);
  renderStreamList(displayData);
  resetPlayer();
  showParseSummary(displayData);
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
    const shownCount = state.latestParseData?.stream_count ?? data.stream_count;
    const shownMediaCount = Number(state.latestParseData?.media_entries?.length || 0);
    if (isXUrl(payload.url) && shownMediaCount > 1) {
      setStatus(`解析完成 · 显示 ${shownMediaCount} 个视频媒体`, 'success');
    } else {
      const isUaaLive = state.latestParseData?.platform === 'uaa';
      setStatus((isUaaLive || shownCount <= 1) ? '解析完成 · 仅显示最高画质' : `解析完成 · 显示 ${shownCount} 个可用视频`, 'success');
    }
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
    const isImageMode = state.latestParseData?.media_type === 'image';
    if (!isImageMode && (state.selectedStreamIndex === null || !state.selectedStreamUrl)) {
      throw new Error('先解析出可用视频，再下载。');
    }
    setStatus('创建下载任务…', 'loading');
    const data = await api('/api/download', payload, 45000);
    renderSummary([
      { label: '当前状态', value: data?.status_text || '任务已创建' },
      { label: '任务编号', value: data?.id || '未知' },
      { label: '输出文件', value: data?.output || '未知文件' },
      { label: '下载类型', value: data?.media_type === 'image' ? `图片 · ${Number(data?.image_count || 0)} 张` : (data?.stream_index !== null && data?.stream_index !== undefined ? `视频 ${Number(data.stream_index) + 1}` : '未标记') },
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
    const wecomSecretInput = $('cfg_wecom_secret')?.value.trim();
    const wecomTokenInput = $('cfg_wecom_token')?.value.trim();
    const wecomAesInput = $('cfg_wecom_encoding_aes_key')?.value.trim();
    const wecomForwardTokenInput = $('cfg_wecom_forward_token')?.value.trim();
    const data = await api('/api/config', {
      default_proxy: $('cfg_proxy').value.trim(),
      auto_retry_enabled: Boolean($('cfg_auto_retry_enabled').checked),
      auto_retry_delay_seconds: Number($('cfg_auto_retry_delay_seconds').value || 30),
      auto_retry_max_attempts: Number($('cfg_auto_retry_max_attempts').value || 0),
      record_segment_minutes: Number($('cfg_record_segment_minutes')?.value || 0),
      record_max_reconnect_attempts: Number($('cfg_record_max_reconnect_attempts')?.value || 0),
      record_restart_delay_seconds: Number($('cfg_record_restart_delay_seconds')?.value || 5),
      xck: $('cfg_xck')?.value.trim() || '/app/data/cookies/twitter.cookies.txt',
      youtubeck: $('cfg_youtubeck')?.value.trim() || '/app/data/cookies/youtube.cookies.txt',
      bilibilick: $('cfg_bilibilick')?.value.trim() || '/app/data/cookies/bilibili.cookies.txt',
      wecom_enabled: Boolean($('cfg_wecom_enabled')?.checked),
      wecom_corp_id: $('cfg_wecom_corp_id')?.value.trim() || '',
      wecom_agent_id: $('cfg_wecom_agent_id')?.value.trim() || '',
      wecom_secret: wecomSecretInput === '' ? '__KEEP__' : wecomSecretInput,
      wecom_token: wecomTokenInput === '' ? '__KEEP__' : wecomTokenInput,
      wecom_encoding_aes_key: wecomAesInput === '' ? '__KEEP__' : wecomAesInput,
      wecom_callback_url: $('cfg_wecom_callback_url')?.value.trim() || '',
      wecom_forward_url: $('cfg_wecom_forward_url')?.value.trim() || '',
      wecom_forward_token: wecomForwardTokenInput === '' ? '__KEEP__' : wecomForwardTokenInput,
    });
    applyConfigToForm(data);
    showConfigSummary(data);
    setStatus('设置已保存', 'success');
  } catch (e) {
    showError('config', e);
    setStatus(`保存失败：${e.message}`, 'error');
  }
}

async function uploadTwitterCookies() {
  const fileInput = $('twitter_cookies_file');
  const file = fileInput?.files?.[0];
  if (!file) {
    setStatus('先选一个 cookies.txt 文件', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    setStatus('上传 cookies.txt…', 'loading');
    const res = await fetch('/api/upload/twitter-cookies', {
      method: 'POST',
      body: formData,
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `upload failed (${res.status})`);
    await loadConfig();
    renderSummary([
      { label: '当前状态', value: 'Twitter cookies 已上传', success: true, highlight: true },
      { label: '保存路径', value: data?.path || '/app/data/cookies/twitter.cookies.txt' },
      { label: '文件大小', value: `${Number(data?.size || 0)} bytes` },
      { label: '下一步', value: '重新贴 X / Twitter 链接直接下载，它会自动带 cookies。' },
    ], { variant: 'success' });
    setStatus('cookies 已上传', 'success');
  } catch (e) {
    setStatus(`上传失败：${e.message}`, 'error');
  }
}

async function uploadYouTubeCookies() {
  const fileInput = $('youtube_cookies_file');
  const file = fileInput?.files?.[0];
  if (!file) {
    setStatus('先选一个 YouTube cookies.txt 文件', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    setStatus('上传 YouTube cookies.txt…', 'loading');
    const res = await fetch('/api/upload/youtube-cookies', {
      method: 'POST',
      body: formData,
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `upload failed (${res.status})`);
    await loadConfig();
    renderSummary([
      { label: '当前状态', value: 'YouTube cookies 已上传', success: true, highlight: true },
      { label: '保存路径', value: data?.path || '/app/data/cookies/youtube.cookies.txt' },
      { label: '文件大小', value: `${Number(data?.size || 0)} bytes` },
      { label: '下一步', value: '重新贴 YouTube 链接下载；遇到年龄限制视频时会自动带 cookies。' },
    ], { variant: 'success' });
    setStatus('YouTube cookies 已上传', 'success');
  } catch (e) {
    setStatus(`上传失败：${e.message}`, 'error');
  }
}

async function uploadBilibiliCookies() {
  const fileInput = $('bilibili_cookies_file');
  const file = fileInput?.files?.[0];
  if (!file) {
    setStatus('先选一个 Bilibili cookies.txt 文件', 'error');
    return;
  }

  const formData = new FormData();
  formData.append('file', file);

  try {
    setStatus('上传 Bilibili cookies.txt…', 'loading');
    const res = await fetch('/api/upload/bilibili-cookies', {
      method: 'POST',
      body: formData,
      cache: 'no-store'
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || data.error || `upload failed (${res.status})`);
    await loadConfig();
    renderSummary([
      { label: '当前状态', value: 'Bilibili cookies 已上传', success: true, highlight: true },
      { label: '保存路径', value: data?.path || '/app/data/cookies/bilibili.cookies.txt' },
      { label: '文件大小', value: `${Number(data?.size || 0)} bytes` },
      { label: '下一步', value: '重新贴 Bilibili 链接解析或下载；它会自动带 cookies。' },
    ], { variant: 'success' });
    setStatus('Bilibili cookies 已上传', 'success');
  } catch (e) {
    setStatus(`上传失败：${e.message}`, 'error');
  }
}

async function startLiveRecord() {
  try {
    const payload = collectLiveRecordPayload();
    if (!payload.stream_url) throw new Error('先填直播流地址。');
    setStatus('创建录制任务…', 'loading');
    const data = await api('/api/live-record', payload, 45000);
    renderSummary([
      { label: '当前状态', value: data?.status_text || '录制任务已创建' },
      { label: '任务编号', value: data?.id || '未知' },
      { label: '输出文件', value: data?.output || '未知文件' },
      { label: '录制策略', value: `切片 ${Number(data?.segment_minutes || 0)} 分钟 / 重连 ${Number(data?.max_reconnect_attempts || 0)} 次` },
    ]);
    await refreshJobs();
    setStatus('录制任务已创建', 'success');
  } catch (e) {
    showError('live-record', e);
    setStatus(`录制失败：${e.message}`, 'error');
  }
}

async function stopJob(jobId) {
  if (!jobId) return;
  try {
    setStatus('停止任务…', 'loading');
    const data = await api(`/api/jobs/${encodeURIComponent(jobId)}/stop`, {}, 20000);
    await refreshJobs();
    setStatus(data?.stopping ? '已请求停止任务' : '任务已停止', 'success');
  } catch (e) {
    setStatus(`停止失败：${e.message}`, 'error');
  }
}

async function showJobLog(jobId) {
  if (!jobId) return;
  try {
    const data = await fetch(`/api/jobs/${encodeURIComponent(jobId)}/log?lines=80`, { cache: 'no-store' }).then(r => r.json());
    const content = String(data?.log || '').trim() || '暂无日志';
    window.alert(content);
  } catch (e) {
    setStatus(`查看日志失败：${e.message}`, 'error');
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
    downloading: job?.job_type === 'live_record' ? '录制中' : '下载中',
    done: job?.job_type === 'live_record' ? '录制完成' : '已完成',
    failed: job?.job_type === 'live_record' ? '录制失败' : '失败',
    cancelled: job?.job_type === 'live_record' ? '已停止' : '已取消',
    retried: '已重试',
  };
  return statusMap[job?.status] || '未知状态';
}

function humanizeJobMeta(job) {
  const raw = String(job?.status_text || '').trim();
  if (!raw) return '';
  return raw
    .replace(/^开始录制直播…\s*/g, '开始录制直播 · ')
    .replace(/^录制中\s*·\s*/g, '录制中 · ')
    .replace(/^录制断开，/g, '录制断开，')
    .replace(/^已请求停止录制，等待 ffmpeg 退出…/g, '已请求停止录制，等待 ffmpeg 退出…')
    .replace(/^正在下载…\s*/g, '')
    .replace(/^激进模式下载中…\s*/g, '正在并发抓分片 · ')
    .replace(/^开始抓取视频（带 cookies）\s*·\s*/g, '开始抓取视频（带登录态） · ')
    .replace(/^开始抓取视频\s*·\s*/g, '开始抓取视频 · ')
    .replace(/^开始直连下载\s*·\s*/g, '开始直连下载 · ')
    .replace(/^开始下载\s*·\s*/g, '开始下载 · ')
    .replace(/^已开始下载视频/g, '已开始下载视频')
    .replace(/^主方案失败，已切到兼容下载\s*·\s*/g, '主方案失败，已切到兼容下载 · ')
    .replace(/^排队中\s*·\s*/g, '')
    .replace(/\b并发槽\s*/g, '下载槽位 ')
    .replace(/\b分片\s*/g, '已完成分片 ')
    .replace(/\b视频进度\s*/g, '视频进度 ')
    .replace(/\b已下载\s*/g, '已下载 ')
    .replace(/\b速度\s*/g, '下载速度 ')
    .replace(/\b下载速度\s+/g, '下载速度 ')
    .replace(/\b总大小\s*/g, '总大小 ')
    .replace(/\b剩余\s*/g, '剩余 ')
    .replace(/当前并行 /g, '当前下载槽位 ')
    .replace(/当前下载槽位 /g, '当前下载槽位 ')
    .replace(/，前面还有 /g, '，前面还有 ');
}

function formatRelativeDuration(ms) {
  const totalSeconds = Math.max(0, Math.floor(ms / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) return `${hours}小时${String(minutes).padStart(2, '0')}分`;
  if (minutes > 0) return `${minutes}分${String(seconds).padStart(2, '0')}秒`;
  return `${seconds}秒`;
}

function parseIsoTime(value) {
  if (!value) return null;
  const ts = Date.parse(String(value));
  return Number.isNaN(ts) ? null : ts;
}

function formatJobTime(job) {
  const now = Date.now();
  const createdAt = parseIsoTime(job?.created_at);
  const startedAt = parseIsoTime(job?.started_at);
  const finishedAt = parseIsoTime(job?.finished_at);
  const updatedAt = parseIsoTime(job?.updated_at);

  if (job?.status === 'downloading' && startedAt) {
    return `已运行 ${formatRelativeDuration(now - startedAt)}`;
  }
  if (job?.status === 'queued' && createdAt) {
    return `已等待 ${formatRelativeDuration(now - createdAt)}`;
  }
  if ((job?.status === 'done' || job?.status === 'failed' || job?.status === 'cancelled') && finishedAt) {
    const finishedAgo = formatRelativeDuration(now - finishedAt);
    if (startedAt) {
      return `${finishedAgo}前 · 耗时 ${formatRelativeDuration(finishedAt - startedAt)}`;
    }
    return `${finishedAgo}前`;
  }
  if (updatedAt) {
    return `${formatRelativeDuration(now - updatedAt)}前更新`;
  }
  if (createdAt) {
    return `${formatRelativeDuration(now - createdAt)}前创建`;
  }
  return '';
}

function jobSortTimestamp(job) {
  return parseIsoTime(job?.updated_at) || parseIsoTime(job?.finished_at) || parseIsoTime(job?.started_at) || parseIsoTime(job?.created_at) || 0;
}

function sortJobsByRecent(jobs = []) {
  return [...jobs].sort((a, b) => jobSortTimestamp(b) - jobSortTimestamp(a));
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
  const metaText = truncateText(humanizeJobMeta(job), compact ? 92 : 160);
  const retryMeta = Number(job?.retry_count || 0) > 0 ? ` · 第 ${Number(job.retry_count)} 次重试` : '';
  const errorText = job?.error ? truncateText(job.error, compact ? 120 : 240) : '';
  const actionLabel = status === 'downloading' ? (job?.job_type === 'live_record' ? '停止' : '取消') : '删除';
  const retryButton = status === 'failed'
    ? `<button class="btn btn-inline btn-secondary-lite" onclick="retryJob('${escapeHtml(job.id || '')}')">重试</button>`
    : '';
  const logButton = `<button class="btn btn-inline btn-secondary-lite" onclick="showJobLog('${escapeHtml(job.id || '')}')">日志</button>`;
  const stopOrDeleteAction = status === 'downloading'
    ? `<button class="btn btn-inline btn-danger" onclick="stopJob('${escapeHtml(job.id || '')}')">${actionLabel}</button>`
    : `<button class="btn btn-inline btn-danger" onclick="deleteJob('${escapeHtml(job.id || '')}')">${actionLabel}</button>`;

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
        ${logButton}
        ${stopOrDeleteAction}
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

function rerenderJobClocks() {
  if (!state.latestJobsSnapshot?.length) return;
  const sortedJobs = sortJobsByRecent(state.latestJobsSnapshot);
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
}

function startJobClock() {
  if (state.jobClockTimer) return;
  state.jobClockTimer = setInterval(() => {
    rerenderJobClocks();
  }, 1000);
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
  state.latestJobsSnapshot = Array.isArray(jobs) ? jobs : [];
  const sortedJobs = sortJobsByRecent(state.latestJobsSnapshot);
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
window.startLiveRecord = startLiveRecord;
window.stopJob = stopJob;
window.showJobLog = showJobLog;
window.retryJob = retryJob;
window.deleteJob = deleteJob;
window.clearHistory = clearHistory;
window.saveConfig = saveConfig;
window.uploadTwitterCookies = uploadTwitterCookies;
window.uploadYouTubeCookies = uploadYouTubeCookies;
window.uploadBilibiliCookies = uploadBilibiliCookies;
window.toggleSettingsPanel = toggleSettingsPanel;
window.toggleHistoryPanel = toggleHistoryPanel;

window.addEventListener('load', async () => {
  initDom();
  bindEvents();
  startJobClock();
  setTaskFilter(state.taskFilter);
  setHistoryFilter(state.historyFilter);
  try {
    await loadConfig();
  } catch (_) {}
  refreshJobs().catch(() => {});
});
