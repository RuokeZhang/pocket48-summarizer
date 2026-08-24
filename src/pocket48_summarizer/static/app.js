const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";

function apiFetch(url, options = {}) {
  const method = (options.method || "GET").toUpperCase();
  const headers = new Headers(options.headers || {});
  if (!["GET", "HEAD", "OPTIONS"].includes(method) && csrfToken) {
    headers.set("X-CSRF-Token", csrfToken);
  }
  return fetch(url, { ...options, headers }).then((response) => {
    if (response.status === 401) {
      window.location.assign("/login");
    }
    return response;
  });
}

const createForm = document.querySelector("#create-job-form");

if (createForm) {
  createForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const button = createForm.querySelector("button");
    const error = document.querySelector("#create-job-error");
    const url = new FormData(createForm).get("url");
    button.disabled = true;
    error.textContent = "";
    try {
      const response = await apiFetch("/api/jobs", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url })
      });
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error?.message || "创建任务失败");
      }
      window.location.assign(`/jobs/${encodeURIComponent(payload.id)}`);
    } catch (requestError) {
      error.textContent = requestError instanceof Error ? requestError.message : "创建任务失败";
      button.disabled = false;
    }
  });
}

const jobHero = document.querySelector("[data-job-id]");
if (jobHero) {
  const jobID = jobHero.dataset.jobId;
  let active = jobHero.dataset.jobActive === "true";
  let previousStatus = document.querySelector("#job-status")?.textContent;
  const poll = async () => {
    if (!active) return;
    try {
      const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobID)}/status`);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || "读取任务状态失败");
      const status = document.querySelector("#job-status");
      const bar = document.querySelector("#job-progress-bar");
      const message = document.querySelector("#job-progress-message");
      const percent = document.querySelector("#job-progress-percent");
      if (status) {
        status.textContent = payload.status;
        status.className = `status status-${payload.status}`;
      }
      if (bar) bar.style.width = `${payload.progress_percent}%`;
      if (message) message.textContent = payload.progress_message;
      if (percent) percent.textContent = `${payload.progress_percent}%`;
      active = payload.status === "queued" || payload.status === "running";
      if (!active || payload.status !== previousStatus && payload.status === "completed") {
        window.location.reload();
        return;
      }
      previousStatus = payload.status;
    } catch {
      // A later poll can recover from a transient localhost request failure.
    }
    window.setTimeout(poll, 2000);
  };
  window.setTimeout(poll, 1200);
}

const replayPlayer = document.querySelector("#replay-player");
const replayPlayerPanel = document.querySelector("#replay-player-panel");
const replayPlayerMessage = document.querySelector("#replay-player-message");
const replayHelpText = "点击时间线、高光或字幕中的时间，即可跳转到对应位置。";
let pendingSeekSeconds = null;
let replayUsesNativeHls = false;
let hlsPlayer = null;
let hlsNetworkRetries = 0;
let hlsMediaRetries = 0;

const applyPendingSeek = () => {
  if (!replayPlayer || pendingSeekSeconds === null) return;
  const duration = Number.isFinite(replayPlayer.duration) ? replayPlayer.duration : null;
  replayPlayer.currentTime = duration === null
    ? pendingSeekSeconds
    : Math.min(pendingSeekSeconds, Math.max(0, duration - 0.1));
  pendingSeekSeconds = null;
  void replayPlayer.play().catch(() => {
    if (replayPlayerMessage) {
      replayPlayerMessage.textContent = "已跳转到目标时间，点击播放键开始播放。";
    }
  });
};

if (replayPlayer) {
  replayPlayer.addEventListener("loadedmetadata", () => {
    if (replayPlayerMessage) replayPlayerMessage.textContent = replayHelpText;
    applyPendingSeek();
  });
  replayPlayer.addEventListener("canplay", () => {
    if (replayPlayerMessage) replayPlayerMessage.textContent = replayHelpText;
  });
  replayPlayer.addEventListener("error", () => {
    if (hlsPlayer) return;
    if (replayPlayerMessage) {
      const code = replayPlayer.error?.code;
      replayPlayerMessage.textContent =
        `回放加载失败${code ? `（媒体错误 ${code}）` : ""}，请刷新重试。`;
    }
  });
  const replaySource = replayPlayer.dataset.hlsSrc || "";
  if (window.Hls?.isSupported()) {
    hlsPlayer = new window.Hls({ enableWorker: true });
    hlsPlayer.on(window.Hls.Events.MEDIA_ATTACHED, () => {
      hlsPlayer.loadSource(replaySource);
    });
    hlsPlayer.on(window.Hls.Events.ERROR, (_event, data) => {
      if (!data.fatal || !replayPlayerMessage) return;
      if (
        data.type === window.Hls.ErrorTypes.NETWORK_ERROR
        && hlsNetworkRetries < 2
      ) {
        hlsNetworkRetries += 1;
        replayPlayerMessage.textContent = "回放网络波动，正在重试…";
        hlsPlayer.startLoad();
        return;
      }
      if (
        data.type === window.Hls.ErrorTypes.MEDIA_ERROR
        && hlsMediaRetries < 2
      ) {
        hlsMediaRetries += 1;
        replayPlayerMessage.textContent = "视频解码异常，正在恢复…";
        hlsPlayer.recoverMediaError();
        return;
      }
      replayPlayerMessage.textContent =
        `回放加载失败（${data.details || data.type}），请刷新重试。`;
    });
    hlsPlayer.attachMedia(replayPlayer);
  } else if (replayPlayer.canPlayType("application/vnd.apple.mpegurl")) {
    replayUsesNativeHls = true;
    replayPlayer.src = replaySource;
  } else if (replayPlayerMessage) {
    replayPlayerMessage.textContent = "当前浏览器不支持 HLS 回放。";
  }
}

document.addEventListener("click", (event) => {
  const timestamp = event.target.closest("[data-seek-ms]");
  if (!timestamp || !replayPlayer) return;
  pendingSeekSeconds = Math.max(0, Number(timestamp.dataset.seekMs || 0) / 1000);
  replayPlayerPanel?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (replayPlayer.readyState >= 1) {
    applyPendingSeek();
  } else if (replayUsesNativeHls) {
    replayPlayer.load();
  } else if (replayPlayerMessage) {
    replayPlayerMessage.textContent = "正在加载回放，请稍候…";
  }
});

const clipRows = document.querySelectorAll(".timeline > li[data-clip-index]");

const renderClipState = (row, payload) => {
  const button = row.querySelector(".clip-button");
  const status = row.querySelector(".clip-status");
  if (!status) return;
  status.replaceChildren();
  if (payload.status === "running") {
    if (button) {
      button.disabled = true;
      button.textContent = "剪辑中…";
    }
    status.hidden = false;
    status.textContent = "FFmpeg 正在后台生成视频。";
    return;
  }
  if (payload.status === "completed") {
    if (button) {
      button.disabled = true;
      button.textContent = "已剪好";
    }
    status.hidden = false;
    status.append(document.createTextNode(`${payload.filename} 已生成 · `));
    const link = document.createElement("a");
    link.href = payload.download_url;
    link.textContent = "下载 MP4";
    status.append(link);
    return;
  }
  if (payload.status === "failed") {
    if (button) {
      button.disabled = false;
      button.textContent = "重试剪视频";
    }
    status.hidden = false;
    status.textContent = payload.error || "视频剪辑失败";
    return;
  }
  if (button) {
    button.disabled = false;
    button.textContent = "剪视频";
  }
  status.hidden = true;
};

const pollClip = async (row) => {
  if (!jobHero) return;
  const clipIndex = row.dataset.clipIndex;
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clips/${encodeURIComponent(clipIndex)}`
    );
    const payload = await response.json();
    if (!response.ok) throw new Error(payload.error?.message || "读取剪辑状态失败");
    renderClipState(row, payload);
    if (payload.status === "running") {
      window.setTimeout(() => void pollClip(row), 2000);
    }
  } catch (requestError) {
    const status = row.querySelector(".clip-status");
    if (status) {
      status.hidden = false;
      status.textContent = requestError instanceof Error ? requestError.message : "读取剪辑状态失败";
    }
    window.setTimeout(() => void pollClip(row), 3000);
  }
};

for (const row of clipRows) {
  const button = row.querySelector(".clip-button");
  if (button) {
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = "正在启动…";
      try {
        const response = await apiFetch(
          `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clips/${encodeURIComponent(row.dataset.clipIndex)}`,
          { method: "POST" }
        );
        const payload = await response.json();
        if (!response.ok) throw new Error(payload.error?.message || "启动视频剪辑失败");
        renderClipState(row, payload);
        if (payload.status === "running") {
          window.setTimeout(() => void pollClip(row), 1000);
        }
      } catch (requestError) {
        renderClipState(row, {
          status: "failed",
          error: requestError instanceof Error ? requestError.message : "启动视频剪辑失败"
        });
      }
    });
  }
  void pollClip(row);
}

const retryButton = document.querySelector("#retry-job");
if (retryButton && jobHero) {
  retryButton.addEventListener("click", async () => {
    retryButton.disabled = true;
    try {
      const response = await apiFetch(`/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/retry`, {
        method: "POST"
      });
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || "重试失败");
      window.location.reload();
    } catch (requestError) {
      window.alert(requestError instanceof Error ? requestError.message : "重试失败");
      retryButton.disabled = false;
    }
  });
}

const formatClock = (milliseconds) => {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
};

const subtitleMode = document.querySelector("#subtitle-mode");
const subtitleOverlay = document.querySelector("#subtitle-overlay");
const subtitleZh = document.querySelector("#subtitle-zh");
const subtitleEn = document.querySelector("#subtitle-en");
const danmakuEnabled = document.querySelector("#danmaku-enabled");
const danmakuDensity = document.querySelector("#danmaku-density");
const danmakuOpacity = document.querySelector("#danmaku-opacity");
const liveDanmakuPanel = document.querySelector("#live-danmaku-panel");
const liveDanmakuStream = document.querySelector("#live-danmaku-stream");
const liveDanmakuEmpty = document.querySelector("#live-danmaku-empty");
const danmakuLiveCount = document.querySelector("#danmaku-live-count");
const playbackSyncState = document.querySelector("#playback-sync-state");
const syncOffset = document.querySelector("#sync-offset");
const syncOffsetValue = document.querySelector("#sync-offset-value");
const translationState = document.querySelector("#translation-state");
const translationRetry = document.querySelector("#translation-retry");
const canRequestTranslation =
  replayPlayerPanel?.dataset.canRequestTranslation === "true";

const densityProfiles = {
  low: { maxBubbles: 10, minGapMs: 700, contextMs: 5000 },
  normal: { maxBubbles: 22, minGapMs: 250, contextMs: 8000 },
  high: { maxBubbles: 38, minGapMs: 0, contextMs: 12000 }
};

let playbackTrack = null;
let currentTranslationStatus = "not_requested";
let translationPollTimer = null;
let translationRequestStarted = false;
let activeSubtitleKey = "";
let nextDanmakuIndex = 0;
let lastMediaTimeMs = null;
let lastDisplayedDanmakuMs = Number.NEGATIVE_INFINITY;
let videoFrameRequest = null;
let animationFrameRequest = null;

const lowerBoundByTime = (items, milliseconds, field) => {
  let low = 0;
  let high = items.length;
  while (low < high) {
    const middle = Math.floor((low + high) / 2);
    if (items[middle][field] < milliseconds) {
      low = middle + 1;
    } else {
      high = middle;
    }
  }
  return low;
};

const activeSubtitleAt = (milliseconds) => {
  const subtitles = playbackTrack?.subtitles || [];
  const insertion = lowerBoundByTime(subtitles, milliseconds + 1, "start_ms");
  const index = insertion - 1;
  if (
    index < 0
    || milliseconds < subtitles[index].start_ms
    || milliseconds >= subtitles[index].end_ms
  ) {
    return null;
  }
  return subtitles[index];
};

const renderSubtitle = (milliseconds) => {
  if (!subtitleOverlay || !subtitleZh || !subtitleEn || !subtitleMode) return;
  const mode = subtitleMode.value;
  const subtitle = mode === "off" ? null : activeSubtitleAt(milliseconds);
  const key = subtitle
    ? `${mode}:${subtitle.sequence}:${subtitle.en || ""}`
    : `${mode}:none`;
  if (key === activeSubtitleKey) return;
  activeSubtitleKey = key;
  if (!subtitle) {
    subtitleOverlay.hidden = true;
    subtitleZh.textContent = "";
    subtitleEn.textContent = "";
    return;
  }
  const showZh = mode === "zh" || mode === "bilingual";
  const showEn = mode === "en" || mode === "bilingual";
  subtitleZh.hidden = !showZh;
  subtitleEn.hidden = !showEn || !subtitle.en;
  subtitleZh.textContent = showZh ? subtitle.zh : "";
  subtitleEn.textContent = showEn && subtitle.en ? subtitle.en : "";
  subtitleOverlay.hidden = !showZh && (!showEn || !subtitle.en);
};

const updateDanmakuCount = () => {
  if (!liveDanmakuStream || !danmakuLiveCount) return;
  danmakuLiveCount.textContent = String(
    liveDanmakuStream.querySelectorAll(".danmaku-bubble").length
  );
};

const clearDanmakuStream = (message = "") => {
  if (!liveDanmakuStream || !liveDanmakuEmpty) return;
  liveDanmakuStream.querySelectorAll(".danmaku-bubble").forEach((bubble) => bubble.remove());
  liveDanmakuEmpty.textContent = message;
  liveDanmakuEmpty.hidden = !message;
  updateDanmakuCount();
};

const appendDanmakuBubble = (entry, isContext = false) => {
  if (!liveDanmakuStream || !liveDanmakuEmpty || !danmakuDensity) return;
  liveDanmakuEmpty.hidden = true;
  const bubble = document.createElement("article");
  bubble.className = `danmaku-bubble${isContext ? " is-context" : ""}`;
  const header = document.createElement("header");
  const author = document.createElement("span");
  author.textContent = entry.author || "匿名";
  const time = document.createElement("time");
  time.textContent = formatClock(entry.timestamp_ms);
  const text = document.createElement("p");
  text.textContent = entry.text;
  header.append(author, time);
  bubble.append(header, text);
  liveDanmakuStream.append(bubble);
  const profile = densityProfiles[danmakuDensity.value] || densityProfiles.normal;
  const bubbles = liveDanmakuStream.querySelectorAll(".danmaku-bubble");
  for (let index = 0; index < bubbles.length - profile.maxBubbles; index += 1) {
    bubbles[index].remove();
  }
  updateDanmakuCount();
};

const rebuildDanmakuContext = (milliseconds) => {
  if (!liveDanmakuStream || !danmakuEnabled || !danmakuDensity) return;
  const entries = playbackTrack?.danmaku || [];
  if (!danmakuEnabled.checked) {
    clearDanmakuStream("弹幕已关闭。");
    return;
  }
  const profile = densityProfiles[danmakuDensity.value] || densityProfiles.normal;
  clearDanmakuStream("");
  const startIndex = lowerBoundByTime(
    entries,
    Math.max(0, milliseconds - profile.contextMs),
    "timestamp_ms"
  );
  nextDanmakuIndex = lowerBoundByTime(entries, milliseconds + 1, "timestamp_ms");
  lastDisplayedDanmakuMs = Number.NEGATIVE_INFINITY;
  for (let index = startIndex; index < nextDanmakuIndex; index += 1) {
    const entry = entries[index];
    if (
      profile.minGapMs > 0
      && entry.timestamp_ms - lastDisplayedDanmakuMs < profile.minGapMs
    ) {
      continue;
    }
    appendDanmakuBubble(entry, true);
    lastDisplayedDanmakuMs = entry.timestamp_ms;
  }
  if (!liveDanmakuStream.querySelector(".danmaku-bubble")) {
    liveDanmakuEmpty.textContent = "当前时间附近没有弹幕。";
    liveDanmakuEmpty.hidden = false;
  }
};

const renderDanmaku = (milliseconds, reset = false) => {
  if (!danmakuEnabled || !danmakuDensity) return;
  const entries = playbackTrack?.danmaku || [];
  if (!danmakuEnabled.checked) return;
  if (
    reset
    || lastMediaTimeMs === null
    || milliseconds < lastMediaTimeMs
    || milliseconds - lastMediaTimeMs > 1500
  ) {
    rebuildDanmakuContext(milliseconds);
    lastMediaTimeMs = milliseconds;
    return;
  }
  const profile = densityProfiles[danmakuDensity.value] || densityProfiles.normal;
  while (
    nextDanmakuIndex < entries.length
    && entries[nextDanmakuIndex].timestamp_ms <= milliseconds
  ) {
    const entry = entries[nextDanmakuIndex];
    if (
      entry.timestamp_ms > lastMediaTimeMs
      && (
        profile.minGapMs === 0
        || entry.timestamp_ms - lastDisplayedDanmakuMs >= profile.minGapMs
      )
    ) {
      appendDanmakuBubble(entry);
      lastDisplayedDanmakuMs = entry.timestamp_ms;
    }
    nextDanmakuIndex += 1;
  }
  lastMediaTimeMs = milliseconds;
};

const playbackMediaTimeMs = () => {
  const mediaTime = replayPlayer && Number.isFinite(replayPlayer.currentTime)
    ? replayPlayer.currentTime * 1000
    : 0;
  return Math.max(0, mediaTime + Number(syncOffset?.value || 0));
};

const renderPlaybackAt = (mediaTimeMs, reset = false) => {
  const adjusted = Math.max(
    0,
    mediaTimeMs + Number(syncOffset?.value || 0)
  );
  renderSubtitle(adjusted);
  renderDanmaku(adjusted, reset);
};

const cancelPlaybackClock = () => {
  if (
    replayPlayer
    && videoFrameRequest !== null
    && "cancelVideoFrameCallback" in replayPlayer
  ) {
    replayPlayer.cancelVideoFrameCallback(videoFrameRequest);
  }
  if (animationFrameRequest !== null) {
    window.cancelAnimationFrame(animationFrameRequest);
  }
  videoFrameRequest = null;
  animationFrameRequest = null;
};

const startPlaybackClock = () => {
  if (!replayPlayer || replayPlayer.paused || replayPlayer.ended) return;
  cancelPlaybackClock();
  if ("requestVideoFrameCallback" in replayPlayer) {
    const onVideoFrame = (_now, metadata) => {
      renderPlaybackAt(metadata.mediaTime * 1000);
      if (!replayPlayer.paused && !replayPlayer.ended) {
        videoFrameRequest = replayPlayer.requestVideoFrameCallback(onVideoFrame);
      }
    };
    videoFrameRequest = replayPlayer.requestVideoFrameCallback(onVideoFrame);
    return;
  }
  const onAnimationFrame = () => {
    renderPlaybackAt(replayPlayer.currentTime * 1000);
    if (!replayPlayer.paused && !replayPlayer.ended) {
      animationFrameRequest = window.requestAnimationFrame(onAnimationFrame);
    }
  };
  animationFrameRequest = window.requestAnimationFrame(onAnimationFrame);
};

const renderTranslationState = (translation) => {
  if (!translationState || !translationRetry) return;
  currentTranslationStatus = translation?.status || "not_requested";
  translationRetry.hidden = true;
  if (currentTranslationStatus === "completed") {
    translationState.textContent = "英文字幕已就绪";
    return;
  }
  if (
    currentTranslationStatus === "queued"
    || currentTranslationStatus === "running"
  ) {
    translationState.textContent = "英文字幕正在后台生成";
    return;
  }
  if (currentTranslationStatus === "failed") {
    translationState.textContent = translation?.error || "英文字幕生成失败";
    if (canRequestTranslation) {
      translationRetry.textContent = "重试翻译";
      translationRetry.hidden = false;
    }
    return;
  }
  translationState.textContent = canRequestTranslation
    ? "开始播放后自动生成英文字幕"
    : "登录后可为历史直播生成英文字幕";
  if (canRequestTranslation) {
    translationRetry.textContent = "现在生成";
    translationRetry.hidden = false;
  }
};

const scheduleTranslationPoll = () => {
  if (
    !jobHero
    || !["queued", "running"].includes(currentTranslationStatus)
  ) {
    return;
  }
  window.clearTimeout(translationPollTimer);
  translationPollTimer = window.setTimeout(async () => {
    try {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/translations/en`
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(payload.error?.message || "读取英文字幕状态失败");
      }
      renderTranslationState(payload);
      if (payload.status === "completed") {
        await loadPlaybackTrack();
        return;
      }
    } catch {
      // Translation is optional; keep the Chinese playback available.
    }
    scheduleTranslationPoll();
  }, 3500);
};

const requestEnglishTranslation = async () => {
  if (!jobHero || !canRequestTranslation) return;
  translationRequestStarted = true;
  if (translationRetry) translationRetry.disabled = true;
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/translations/en`,
      { method: "POST" }
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || "启动英文字幕生成失败");
    }
    renderTranslationState(payload);
    scheduleTranslationPoll();
  } catch (requestError) {
    if (translationState) {
      translationState.textContent = requestError instanceof Error
        ? requestError.message
        : "启动英文字幕生成失败";
    }
    if (translationRetry) translationRetry.hidden = false;
  } finally {
    if (translationRetry) translationRetry.disabled = false;
  }
};

async function loadPlaybackTrack() {
  if (!jobHero || !replayPlayer || !playbackSyncState) return;
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/playback-track`
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(payload.error?.message || "加载同步播放数据失败");
    }
    playbackTrack = payload;
    activeSubtitleKey = "";
    lastMediaTimeMs = null;
    renderTranslationState(payload.translation);
    renderPlaybackAt(replayPlayer.currentTime * 1000, true);
    const precision = "requestVideoFrameCallback" in replayPlayer
      ? "逐视频帧同步"
      : "媒体时钟同步";
    playbackSyncState.textContent =
      `${precision} · ${payload.subtitles.length} 条字幕 · ${payload.danmaku.length} 条弹幕`;
    playbackSyncState.className = "sync-state ready";
    scheduleTranslationPoll();
  } catch (requestError) {
    playbackSyncState.textContent = requestError instanceof Error
      ? requestError.message
      : "同步播放数据加载失败";
    playbackSyncState.className = "sync-state error";
  }
}

if (replayPlayer && replayPlayerPanel) {
  void loadPlaybackTrack();
  replayPlayer.addEventListener("play", () => {
    if (
      canRequestTranslation
      && !translationRequestStarted
      && ["not_requested", "failed"].includes(currentTranslationStatus)
    ) {
      void requestEnglishTranslation();
    }
    renderPlaybackAt(replayPlayer.currentTime * 1000, true);
    startPlaybackClock();
  });
  replayPlayer.addEventListener("pause", () => {
    cancelPlaybackClock();
    renderPlaybackAt(replayPlayer.currentTime * 1000);
  });
  replayPlayer.addEventListener("ended", cancelPlaybackClock);
  replayPlayer.addEventListener("seeked", () => {
    renderPlaybackAt(replayPlayer.currentTime * 1000, true);
    startPlaybackClock();
  });
  replayPlayer.addEventListener("loadedmetadata", () => {
    renderPlaybackAt(replayPlayer.currentTime * 1000, true);
  });
}

subtitleMode?.addEventListener("change", () => {
  activeSubtitleKey = "";
  renderSubtitle(playbackMediaTimeMs());
});

danmakuEnabled?.addEventListener("change", () => {
  liveDanmakuPanel?.classList.toggle("is-disabled", !danmakuEnabled.checked);
  lastMediaTimeMs = null;
  if (danmakuEnabled.checked) {
    rebuildDanmakuContext(playbackMediaTimeMs());
  } else {
    clearDanmakuStream("弹幕已关闭。");
  }
});

danmakuDensity?.addEventListener("change", () => {
  lastMediaTimeMs = null;
  rebuildDanmakuContext(playbackMediaTimeMs());
});

danmakuOpacity?.addEventListener("input", () => {
  liveDanmakuStream?.style.setProperty(
    "--danmaku-opacity",
    String(Number(danmakuOpacity.value) / 100)
  );
});

syncOffset?.addEventListener("input", () => {
  const milliseconds = Number(syncOffset.value);
  if (syncOffsetValue) {
    syncOffsetValue.textContent = `${milliseconds >= 0 ? "+" : ""}${(milliseconds / 1000).toFixed(1)}s`;
  }
  activeSubtitleKey = "";
  lastMediaTimeMs = null;
  renderPlaybackAt(replayPlayer?.currentTime * 1000 || 0, true);
});

translationRetry?.addEventListener("click", () => {
  translationRequestStarted = false;
  void requestEnglishTranslation();
});

const showLoadFailure = (countElement, count, retry) => {
  countElement.textContent = `（已显示 ${count} 条，加载失败）`;
  const button = document.createElement("button");
  button.type = "button";
  button.className = "load-retry";
  button.textContent = "继续加载";
  button.addEventListener("click", () => {
    button.remove();
    void retry();
  });
  countElement.after(button);
};

const transcriptList = document.querySelector("#transcript-list");
const transcriptCount = document.querySelector("#transcript-count");

const loadAllTranscript = async () => {
  if (!jobHero || !transcriptList || !transcriptCount) return;
  let offset = Number(transcriptList.dataset.loaded || 0);
  let hasMore = transcriptList.dataset.hasMore === "true";
  if (!hasMore) {
    transcriptCount.textContent = `（共 ${offset} 条）`;
    return;
  }
  transcriptCount.textContent = `（已显示 ${offset} 条，正在加载全部…）`;
  try {
    while (hasMore) {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/transcript?offset=${offset}&limit=500`
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || "加载字幕失败");
      const fragment = document.createDocumentFragment();
      for (const segment of payload.segments) {
        const article = document.createElement("article");
        article.id = `segment-${segment.sequence}`;
        const time = document.createElement("button");
        time.type = "button";
        time.className = "timestamp-link";
        time.dataset.seekMs = String(segment.start_ms);
        time.title = `跳转到 ${formatClock(segment.start_ms)}`;
        time.textContent = formatClock(segment.start_ms);
        const text = document.createElement("p");
        text.textContent = segment.text;
        article.append(time, text);
        fragment.append(article);
      }
      transcriptList.append(fragment);
      offset = payload.next_offset;
      hasMore = payload.has_more;
      transcriptList.dataset.loaded = String(offset);
      transcriptList.dataset.hasMore = String(hasMore);
      transcriptCount.textContent = hasMore
        ? `（已显示 ${offset} 条，正在加载全部…）`
        : `（共 ${offset} 条）`;
    }
  } catch {
    showLoadFailure(transcriptCount, offset, loadAllTranscript);
  }
};

const danmakuList = document.querySelector("#danmaku-list");
const danmakuCount = document.querySelector("#danmaku-count");
const danmakuAuthorFilter = document.querySelector("#danmaku-author-filter");
const clearDanmakuAuthor = document.querySelector("#clear-danmaku-author");
let activeDanmakuAuthor = null;

const updateDanmakuAuthorFilter = () => {
  if (!danmakuList || !danmakuCount || !danmakuAuthorFilter) return;
  const articles = danmakuList.querySelectorAll("article[data-author]");
  let matchingCount = 0;
  for (const article of articles) {
    const matches = activeDanmakuAuthor === null || article.dataset.author === activeDanmakuAuthor;
    article.hidden = !matches;
    if (matches) matchingCount += 1;
  }
  const totalCount = Number(danmakuList.dataset.loaded || articles.length);
  const hasMore = danmakuList.dataset.hasMore === "true";
  if (activeDanmakuAuthor === null) {
    danmakuAuthorFilter.hidden = true;
    danmakuCount.textContent = hasMore
      ? `（已显示 ${totalCount} 条，正在加载全部…）`
      : `（共 ${totalCount} 条）`;
    return;
  }
  danmakuAuthorFilter.hidden = false;
  const label = danmakuAuthorFilter.querySelector("span");
  if (label) {
    label.textContent = hasMore
      ? `正在加载粉丝 ${activeDanmakuAuthor} 的全部弹幕…`
      : `粉丝 ${activeDanmakuAuthor} · 本场共 ${matchingCount} 条`;
  }
  danmakuCount.textContent = hasMore
    ? `（已找到 ${matchingCount} 条，正在加载全部…）`
    : `（已筛选 ${matchingCount} 条，本场共 ${totalCount} 条）`;
};

if (danmakuList) {
  danmakuList.addEventListener("click", (event) => {
    const author = event.target.closest(".danmaku-author");
    if (!author) return;
    activeDanmakuAuthor = author.dataset.danmakuAuthor || "";
    updateDanmakuAuthorFilter();
    danmakuList.scrollTop = 0;
  });
}

if (clearDanmakuAuthor) {
  clearDanmakuAuthor.addEventListener("click", () => {
    activeDanmakuAuthor = null;
    updateDanmakuAuthorFilter();
    if (danmakuList) danmakuList.scrollTop = 0;
  });
}

const loadAllDanmaku = async () => {
  if (!jobHero || !danmakuList || !danmakuCount) return;
  let count = Number(danmakuList.dataset.loaded || 0);
  let afterMs = Number(danmakuList.dataset.afterMs || -1);
  let hasMore = danmakuList.dataset.hasMore === "true";
  if (!hasMore) {
    danmakuCount.textContent = `（共 ${count} 条）`;
    return;
  }
  danmakuCount.textContent = `（已显示 ${count} 条，正在加载全部…）`;
  try {
    while (hasMore) {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/danmaku?after_ms=${afterMs}&limit=500`
      );
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error?.message || "加载弹幕失败");
      const fragment = document.createDocumentFragment();
      for (const entry of payload.entries) {
        const article = document.createElement("article");
        article.dataset.author = entry.author || "";
        const time = document.createElement("time");
        time.textContent = formatClock(entry.timestamp_ms);
        const text = document.createElement("p");
        if (entry.author) {
          const author = document.createElement("button");
          author.type = "button";
          author.className = "danmaku-author";
          author.dataset.danmakuAuthor = entry.author;
          author.textContent = entry.author;
          text.append(author);
        }
        text.append(document.createTextNode(`${entry.author ? " " : ""}${entry.text}`));
        article.append(time, text);
        fragment.append(article);
      }
      danmakuList.append(fragment);
      count += payload.entries.length;
      afterMs = payload.next_after_ms;
      hasMore = payload.has_more;
      danmakuList.dataset.loaded = String(count);
      danmakuList.dataset.afterMs = String(afterMs);
      danmakuList.dataset.hasMore = String(hasMore);
      updateDanmakuAuthorFilter();
    }
  } catch {
    showLoadFailure(danmakuCount, count, loadAllDanmaku);
  }
};

void loadAllTranscript();
void loadAllDanmaku();
