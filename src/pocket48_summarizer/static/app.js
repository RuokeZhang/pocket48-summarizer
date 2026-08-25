const csrfToken = document.querySelector('meta[name="csrf-token"]')?.content || "";
const i18n = window.P48I18n;
const t = (key, params = {}) => i18n?.t(key, params) || key;
const setLocalizedText = (element, key, params = {}) => {
  if (i18n) {
    i18n.setText(element, key, params);
  } else if (element) {
    element.textContent = key;
  }
};
const apiErrorMessage = (payload, fallbackKey) => {
  const message = payload.error?.message;
  return message
    ? (i18n?.translateServerMessage(message) || message)
    : t(fallbackKey);
};

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

const historyBack = document.querySelector("#history-back");
const historyForward = document.querySelector("#history-forward");

if (historyBack && historyForward) {
  const updateHistoryControls = () => {
    historyBack.disabled = (
      window.history.length <= 1
      && window.location.pathname === "/"
      && !window.location.search
      && !window.location.hash
    );
  };

  historyBack.addEventListener("click", () => {
    if (window.history.length > 1) {
      window.history.back();
      return;
    }
    if (
      window.location.pathname !== "/"
      || window.location.search
      || window.location.hash
    ) {
      window.location.replace("/");
    }
  });
  historyForward.addEventListener("click", () => {
    window.history.forward();
  });
  window.addEventListener("pageshow", updateHistoryControls);
  updateHistoryControls();
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
        throw new Error(apiErrorMessage(payload, "createJobFailed"));
      }
      window.location.assign(`/jobs/${encodeURIComponent(payload.id)}`);
    } catch (requestError) {
      error.textContent = requestError instanceof Error
        ? requestError.message
        : t("createJobFailed");
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
      if (!response.ok) {
        throw new Error(apiErrorMessage(payload, "readJobFailed"));
      }
      const status = document.querySelector("#job-status");
      const bar = document.querySelector("#job-progress-bar");
      const message = document.querySelector("#job-progress-message");
      const percent = document.querySelector("#job-progress-percent");
      if (status) {
        status.dataset.jobStatus = payload.status;
        status.textContent = i18n?.translateStatus(payload.status) || payload.status;
        status.className = `status status-${payload.status}`;
      }
      if (bar) bar.style.width = `${payload.progress_percent}%`;
      if (message) {
        message.dataset.operationalMessage = payload.progress_message;
        message.textContent = i18n?.translateOperationalMessage(
          payload.progress_message
        ) || payload.progress_message;
      }
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
      setLocalizedText(replayPlayerMessage, "jumpedToTarget");
    }
  });
};

if (replayPlayer) {
  replayPlayer.addEventListener("loadedmetadata", () => {
    if (replayPlayerMessage) setLocalizedText(replayPlayerMessage, "replayHelp");
    applyPendingSeek();
  });
  replayPlayer.addEventListener("canplay", () => {
    if (replayPlayerMessage) setLocalizedText(replayPlayerMessage, "replayHelp");
  });
  replayPlayer.addEventListener("error", () => {
    if (hlsPlayer) return;
    if (replayPlayerMessage) {
      const code = replayPlayer.error?.code;
      setLocalizedText(replayPlayerMessage, "mediaError", {
        detail: code ? t("mediaErrorCode", { code }) : ""
      });
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
        setLocalizedText(replayPlayerMessage, "networkRetry");
        hlsPlayer.startLoad();
        return;
      }
      if (
        data.type === window.Hls.ErrorTypes.MEDIA_ERROR
        && hlsMediaRetries < 2
      ) {
        hlsMediaRetries += 1;
        setLocalizedText(replayPlayerMessage, "mediaRecover");
        hlsPlayer.recoverMediaError();
        return;
      }
      setLocalizedText(replayPlayerMessage, "mediaError", {
        detail: t("mediaErrorDetail", {
          detail: data.details || data.type
        })
      });
    });
    hlsPlayer.attachMedia(replayPlayer);
  } else if (replayPlayer.canPlayType("application/vnd.apple.mpegurl")) {
    replayUsesNativeHls = true;
    replayPlayer.src = replaySource;
  } else if (replayPlayerMessage) {
    setLocalizedText(replayPlayerMessage, "unsupportedHls");
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
    setLocalizedText(replayPlayerMessage, "loadingReplay");
  }
});

const clipRows = document.querySelectorAll(".timeline > li[data-clip-index]");

const renderClipState = (row, payload) => {
  row._clipPayload = payload;
  const button = row.querySelector(".clip-button");
  const status = row.querySelector(".clip-status");
  if (!status) return;
  status.replaceChildren();
  if (payload.status === "running") {
    if (button) {
      button.disabled = true;
      button.textContent = t("clipping");
    }
    status.hidden = false;
    status.textContent = t("ffmpegClipping");
    return;
  }
  if (payload.status === "completed") {
    if (button) {
      button.disabled = true;
      button.textContent = t("clipped");
    }
    status.hidden = false;
    status.append(document.createTextNode(
      t("generatedFile", { filename: payload.filename })
    ));
    const link = document.createElement("a");
    link.href = payload.download_url;
    link.textContent = t("downloadMp4");
    status.append(link);
    return;
  }
  if (payload.status === "failed") {
    if (button) {
      button.disabled = false;
      button.textContent = t("retryClip");
    }
    status.hidden = false;
    status.textContent = payload.error || t("clipFailed");
    return;
  }
  if (button) {
    button.disabled = false;
    button.textContent = t("clipVideo");
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
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, "readClipFailed"));
    }
    renderClipState(row, payload);
    if (payload.status === "running") {
      window.setTimeout(() => void pollClip(row), 2000);
    }
  } catch (requestError) {
    const status = row.querySelector(".clip-status");
    if (status) {
      status.hidden = false;
      status.textContent = requestError instanceof Error
        ? requestError.message
        : t("readClipFailed");
    }
    window.setTimeout(() => void pollClip(row), 3000);
  }
};

for (const row of clipRows) {
  const button = row.querySelector(".clip-button");
  if (button) {
    button.addEventListener("click", async () => {
      button.disabled = true;
      button.textContent = t("starting");
      try {
        const response = await apiFetch(
          `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clips/${encodeURIComponent(row.dataset.clipIndex)}`,
          { method: "POST" }
        );
        const payload = await response.json();
        if (!response.ok) {
          throw new Error(apiErrorMessage(payload, "startClipFailed"));
        }
        renderClipState(row, payload);
        if (payload.status === "running") {
          window.setTimeout(() => void pollClip(row), 1000);
        }
      } catch (requestError) {
        renderClipState(row, {
          status: "failed",
          error: requestError instanceof Error
            ? requestError.message
            : t("startClipFailed")
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
      if (!response.ok) {
        throw new Error(apiErrorMessage(payload, "retryFailed"));
      }
      window.location.reload();
    } catch (requestError) {
      window.alert(
        requestError instanceof Error ? requestError.message : t("retryFailed")
      );
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
const playbackLayout = document.querySelector("#playback-layout");
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
const mobileDensityProfiles = {
  low: { maxBubbles: 3, minGapMs: 900, contextMs: 3500 },
  normal: { maxBubbles: 5, minGapMs: 450, contextMs: 5000 },
  high: { maxBubbles: 8, minGapMs: 150, contextMs: 6500 }
};
const mobileDanmakuMedia = window.matchMedia(
  "(max-width: 760px), (max-width: 900px) and (pointer: coarse)"
);

const activeDanmakuProfile = () => {
  const profiles = mobileDanmakuMedia.matches
    ? mobileDensityProfiles
    : densityProfiles;
  return profiles[danmakuDensity?.value] || profiles.normal;
};

let playbackTrack = null;
let playbackSyncPayload = null;
let lastTranslationPayload = null;
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

const clearDanmakuStream = (messageKey = "") => {
  if (!liveDanmakuStream || !liveDanmakuEmpty) return;
  liveDanmakuStream.querySelectorAll(".danmaku-bubble").forEach((bubble) => bubble.remove());
  if (messageKey) {
    setLocalizedText(liveDanmakuEmpty, messageKey);
  } else {
    liveDanmakuEmpty.textContent = "";
    delete liveDanmakuEmpty.dataset.i18nRuntime;
    delete liveDanmakuEmpty.dataset.i18nRuntimeParams;
  }
  liveDanmakuEmpty.hidden = !messageKey;
  updateDanmakuCount();
};

const appendDanmakuBubble = (entry, isContext = false) => {
  if (!liveDanmakuStream || !liveDanmakuEmpty || !danmakuDensity) return;
  liveDanmakuEmpty.hidden = true;
  const bubble = document.createElement("article");
  bubble.className = `danmaku-bubble${isContext ? " is-context" : ""}`;
  const header = document.createElement("header");
  const author = document.createElement("span");
  author.textContent = entry.author || t("anonymous");
  if (!entry.author) author.dataset.i18n = "anonymous";
  const time = document.createElement("time");
  time.textContent = formatClock(entry.timestamp_ms);
  const text = document.createElement("p");
  text.textContent = entry.text;
  header.append(author, time);
  bubble.append(header, text);
  liveDanmakuStream.append(bubble);
  const profile = activeDanmakuProfile();
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
    clearDanmakuStream("danmakuDisabled");
    return;
  }
  const profile = activeDanmakuProfile();
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
    setLocalizedText(liveDanmakuEmpty, "noNearbyDanmaku");
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
  const profile = activeDanmakuProfile();
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
  lastTranslationPayload = translation;
  currentTranslationStatus = translation?.status || "not_requested";
  translationRetry.hidden = true;
  if (currentTranslationStatus === "completed") {
    setLocalizedText(translationState, "englishReady");
    return;
  }
  if (
    currentTranslationStatus === "queued"
    || currentTranslationStatus === "running"
  ) {
    setLocalizedText(translationState, "englishGenerating");
    return;
  }
  if (currentTranslationStatus === "failed") {
    translationState.textContent = translation?.error
      ? (i18n?.translateServerMessage(translation.error) || translation.error)
      : t("englishFailed");
    if (canRequestTranslation) {
      setLocalizedText(translationRetry, "retryTranslation");
      translationRetry.hidden = false;
    }
    return;
  }
  setLocalizedText(
    translationState,
    canRequestTranslation ? "autoEnglishOnPlay" : "loginForEnglish"
  );
  if (canRequestTranslation) {
    setLocalizedText(translationRetry, "generateNow");
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
        throw new Error(apiErrorMessage(payload, "readTranslationFailed"));
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
      throw new Error(apiErrorMessage(payload, "startTranslationFailed"));
    }
    renderTranslationState(payload);
    scheduleTranslationPoll();
  } catch (requestError) {
    if (translationState) {
      translationState.textContent = requestError instanceof Error
        ? requestError.message
        : t("startTranslationFailed");
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
      throw new Error(apiErrorMessage(payload, "loadPlaybackTrackFailed"));
    }
    playbackTrack = payload;
    activeSubtitleKey = "";
    lastMediaTimeMs = null;
    renderTranslationState(payload.translation);
    renderPlaybackAt(replayPlayer.currentTime * 1000, true);
    playbackSyncPayload = payload;
    renderPlaybackSyncSummary();
    playbackSyncState.className = "sync-state ready";
    scheduleTranslationPoll();
  } catch (requestError) {
    playbackSyncState.textContent = requestError instanceof Error
      ? requestError.message
      : t("playbackTrackFailed");
    playbackSyncState.className = "sync-state error";
  }
}

function renderPlaybackSyncSummary() {
  if (!playbackSyncState || !playbackSyncPayload || !replayPlayer) return;
  const precision = "requestVideoFrameCallback" in replayPlayer
    ? t("frameSync")
    : t("mediaClockSync");
  setLocalizedText(playbackSyncState, "syncSummary", {
    precision,
    subtitles: playbackSyncPayload.subtitles.length,
    danmaku: playbackSyncPayload.danmaku.length
  });
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

const updateDanmakuPanelVisibility = () => {
  if (!danmakuEnabled || !liveDanmakuPanel || !playbackLayout) return;
  const collapsed = !danmakuEnabled.checked;
  liveDanmakuPanel.hidden = collapsed;
  playbackLayout.classList.toggle("is-danmaku-collapsed", collapsed);
};

danmakuEnabled?.addEventListener("change", () => {
  updateDanmakuPanelVisibility();
  lastMediaTimeMs = null;
  if (danmakuEnabled.checked) {
    rebuildDanmakuContext(playbackMediaTimeMs());
  } else {
    clearDanmakuStream("danmakuDisabled");
  }
});

danmakuDensity?.addEventListener("change", () => {
  lastMediaTimeMs = null;
  rebuildDanmakuContext(playbackMediaTimeMs());
});

const handleMobileDanmakuLayoutChange = () => {
  lastMediaTimeMs = null;
  rebuildDanmakuContext(playbackMediaTimeMs());
};
if (typeof mobileDanmakuMedia.addEventListener === "function") {
  mobileDanmakuMedia.addEventListener(
    "change",
    handleMobileDanmakuLayoutChange
  );
} else {
  mobileDanmakuMedia.addListener(handleMobileDanmakuLayoutChange);
}

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

updateDanmakuPanelVisibility();

const showLoadFailure = (countElement, count, retry) => {
  setLocalizedText(countElement, "displayedLoadFailed", { count });
  const button = document.createElement("button");
  button.type = "button";
  button.className = "load-retry";
  button.dataset.i18n = "continueLoading";
  button.textContent = t("continueLoading");
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
    setLocalizedText(transcriptCount, "totalCount", { count: offset });
    return;
  }
  setLocalizedText(transcriptCount, "loadingAll", { count: offset });
  try {
    while (hasMore) {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/transcript?offset=${offset}&limit=500`
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(apiErrorMessage(payload, "loadTranscriptFailed"));
      }
      const fragment = document.createDocumentFragment();
      for (const segment of payload.segments) {
        const article = document.createElement("article");
        article.id = `segment-${segment.sequence}`;
        const time = document.createElement("button");
        time.type = "button";
        time.className = "timestamp-link";
        time.dataset.seekMs = String(segment.start_ms);
        time.dataset.i18nTitle = "jumpTo";
        time.dataset.i18nParamTime = formatClock(segment.start_ms);
        time.title = t("jumpTo", {
          time: formatClock(segment.start_ms)
        });
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
      setLocalizedText(
        transcriptCount,
        hasMore ? "loadingAll" : "totalCount",
        { count: offset }
      );
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
    setLocalizedText(
      danmakuCount,
      hasMore ? "loadingAll" : "totalCount",
      { count: totalCount }
    );
    return;
  }
  danmakuAuthorFilter.hidden = false;
  const label = danmakuAuthorFilter.querySelector("span");
  if (label) {
    setLocalizedText(
      label,
      hasMore ? "loadingFanDanmaku" : "fanDanmakuTotal",
      {
        author: activeDanmakuAuthor,
        count: matchingCount
      }
    );
  }
  setLocalizedText(
    danmakuCount,
    hasMore ? "foundLoading" : "filteredTotal",
    {
      count: matchingCount,
      total: totalCount
    }
  );
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
    setLocalizedText(danmakuCount, "totalCount", { count });
    return;
  }
  setLocalizedText(danmakuCount, "loadingAll", { count });
  try {
    while (hasMore) {
      const response = await apiFetch(
        `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/danmaku?after_ms=${afterMs}&limit=500`
      );
      const payload = await response.json();
      if (!response.ok) {
        throw new Error(apiErrorMessage(payload, "loadDanmakuFailed"));
      }
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

document.addEventListener("p48:languagechange", () => {
  if (lastTranslationPayload) renderTranslationState(lastTranslationPayload);
  renderPlaybackSyncSummary();
  for (const row of clipRows) {
    if (row._clipPayload) renderClipState(row, row._clipPayload);
  }
  updateDanmakuAuthorFilter();
});
