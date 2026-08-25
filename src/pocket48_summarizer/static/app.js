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

const formatClock = (milliseconds) => {
  const totalSeconds = Math.max(0, Math.floor(milliseconds / 1000));
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  return [hours, minutes, seconds].map((value) => String(value).padStart(2, "0")).join(":");
};

const formatFineClock = (milliseconds) => {
  const tenths = Math.max(0, Math.round(milliseconds / 100));
  const hours = Math.floor(tenths / 36000);
  const minutes = Math.floor((tenths % 36000) / 600);
  const seconds = Math.floor((tenths % 600) / 10);
  return `${String(hours).padStart(2, "0")}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${tenths % 10}`;
};

const parseClipTime = (value) => {
  const parts = String(value || "").trim().split(":");
  if (!parts.length || parts.length > 3 || parts.some((part) => part === "")) return null;
  const numeric = parts.map(Number);
  if (numeric.some((part) => !Number.isFinite(part) || part < 0)) return null;
  const seconds = numeric.pop();
  const minutes = numeric.pop() || 0;
  const hours = numeric.pop() || 0;
  if (seconds >= 60 || minutes >= 60) return null;
  return Math.round((hours * 3600 + minutes * 60 + seconds) * 10) * 100;
};

const clipRows = Array.from(document.querySelectorAll(".timeline > li[data-clip-index]"));
const clipEditor = document.querySelector("#clip-editor");
const clipEditorTopic = document.querySelector("#clip-editor-topic");
const clipEditorClose = document.querySelector("#clip-editor-close");
const clipEditorCancel = document.querySelector("#clip-editor-cancel");
const clipEditorSubmit = document.querySelector("#clip-editor-submit");
const clipEditorError = document.querySelector("#clip-editor-error");
const clipTimelineViewport = document.querySelector("#clip-timeline-viewport");
const clipRangeControl = document.querySelector("#clip-range-control");
const clipTimelineCues = document.querySelector("#clip-transcript-cues");
const clipStartHandle = document.querySelector("#clip-start-handle");
const clipEndHandle = document.querySelector("#clip-end-handle");
const clipStartHandleTime = document.querySelector("#clip-start-handle-time");
const clipEndHandleTime = document.querySelector("#clip-end-handle-time");
const clipRawMarker = document.querySelector("#clip-raw-marker");
const clipSnapMarker = document.querySelector("#clip-snap-marker");
const clipPreviewPlayhead = document.querySelector("#clip-preview-playhead");
const clipZoomOut = document.querySelector("#clip-zoom-out");
const clipZoomIn = document.querySelector("#clip-zoom-in");
const clipZoomLevel = document.querySelector("#clip-zoom-value");
const clipStartInput = document.querySelector("#clip-start-input");
const clipEndInput = document.querySelector("#clip-end-input");
const clipStartSnap = document.querySelector("#clip-start-snap");
const clipEndSnap = document.querySelector("#clip-end-snap");
const clipWindowStart = document.querySelector("#clip-window-start");
const clipWindowEnd = document.querySelector("#clip-window-end");
const clipSelectedDuration = document.querySelector("#clip-selected-duration");
const clipSnapEnabled = document.querySelector("#clip-snap-enabled");
const clipResetRange = document.querySelector("#clip-reset-range");
const clipSubtitleMode = document.querySelector("#clip-subtitle-mode");
const clipDanmakuEnabled = document.querySelector("#clip-danmaku-enabled");
const clipPreviewPlayer = document.querySelector("#clip-preview-player");
const clipPreviewSelection = document.querySelector("#clip-preview-selection");
const clipPreviewSubtitles = document.querySelector("#clip-preview-subtitles");
const clipPreviewZh = document.querySelector("#clip-preview-zh");
const clipPreviewEn = document.querySelector("#clip-preview-en");
const clipPreviewDanmaku = document.querySelector("#clip-preview-danmaku");

let clipExports = [];
let clipPollTimer = null;
let clipEditorState = null;
let clipOriginButton = null;
let clipPreviewHls = null;
let clipPreviewUsesNativeHls = false;
let clipPreviewFrame = null;
let clipPendingSeekMs = null;
let clipPendingPlay = false;
let clipSuggestionTokens = { start: 0, end: 0 };
let clipDragState = null;

const CLIP_MIN_ZOOM = 1;
const CLIP_MAX_ZOOM = 64;
const CLIP_SNAP_ENTER_PX = 12;
const CLIP_SNAP_RELEASE_PX = 22;
const CLIP_AUTO_SCROLL_EDGE_PX = 52;
const CLIP_AUTO_SCROLL_MAX_PX = 24;

const clipOverlayLabel = (clip) => {
  const subtitles = {
    off: t("noSubtitles"),
    zh: t("chineseSubtitles"),
    en: t("englishSubtitles"),
    bilingual: t("bilingualSubtitles")
  }[clip.subtitle_mode] || clip.subtitle_mode;
  return clip.include_danmaku
    ? `${subtitles} · ${t("danmaku")}`
    : subtitles;
};

const clipServerMessage = (message) => (
  message ? (i18n?.translateServerMessage(message) || message) : ""
);

const retryClipExport = async (clipID, button) => {
  if (!jobHero) return;
  button.disabled = true;
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clip-exports/${encodeURIComponent(clipID)}/retry`,
      { method: "POST" }
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, "retryClipFailed"));
    }
    await loadClipExports();
  } catch (requestError) {
    window.alert(requestError instanceof Error ? requestError.message : t("retryClipFailed"));
  } finally {
    button.disabled = false;
  }
};

const renderClipExports = () => {
  for (const row of clipRows) {
    const status = row.querySelector(".clip-status");
    const button = row.querySelector(".clip-button");
    if (button) {
      button.disabled = false;
      button.textContent = t("clipVideo");
    }
    if (!status) continue;
    const rowClips = clipExports.filter(
      (clip) => String(clip.timeline_index) === String(row.dataset.clipIndex)
    );
    status.replaceChildren();
    status.hidden = rowClips.length === 0;
    if (!rowClips.length) continue;
    const list = document.createElement("div");
    list.className = "clip-export-list";
    for (const clip of rowClips) {
      const item = document.createElement("article");
      item.className = "clip-export-item";
      const copy = document.createElement("div");
      copy.className = "clip-export-copy";
      const title = document.createElement("strong");
      title.textContent = `${formatFineClock(clip.start_ms)}–${formatFineClock(clip.end_ms)} · ${clipOverlayLabel(clip)}`;
      const detail = document.createElement("small");
      detail.textContent = clip.error || clip.warning
        ? clipServerMessage(clip.error || clip.warning)
        : t(`clipStatus_${clip.status}`);
      detail.className = `clip-export-${clip.status}`;
      copy.append(title, detail);
      const actions = document.createElement("div");
      actions.className = "clip-export-actions";
      if (clip.status === "completed" && clip.download_url) {
        const link = document.createElement("a");
        link.href = clip.download_url;
        link.textContent = t("downloadMp4");
        actions.append(link);
      }
      if (clip.status === "failed" && clipEditor) {
        const retry = document.createElement("button");
        retry.type = "button";
        retry.textContent = t("retryClip");
        retry.addEventListener("click", () => void retryClipExport(clip.id, retry));
        actions.append(retry);
      }
      item.append(copy, actions);
      list.append(item);
    }
    status.append(list);
  }
};

async function loadClipExports() {
  if (!jobHero || !clipRows.length) return;
  window.clearTimeout(clipPollTimer);
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clip-exports`
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, "readClipFailed"));
    }
    clipExports = payload.clips || [];
    renderClipExports();
    if (clipExports.some((clip) => clip.status === "running")) {
      clipPollTimer = window.setTimeout(() => void loadClipExports(), 2000);
    }
  } catch (requestError) {
    if (!clipExports.length) {
      for (const row of clipRows) {
        const status = row.querySelector(".clip-status");
        if (!status) continue;
        status.hidden = false;
        status.textContent = requestError instanceof Error
          ? requestError.message
          : t("readClipFailed");
      }
    }
    clipPollTimer = window.setTimeout(() => void loadClipExports(), 5000);
  }
}

const clipValidationMessage = () => {
  if (!clipEditorState) return "";
  if (
    clipEditorState.startMs < clipEditorState.minMs
    || clipEditorState.endMs > clipEditorState.maxMs
  ) {
    return t("clipOutsideWindow");
  }
  if (clipEditorState.endMs <= clipEditorState.startMs) {
    return t("clipInvalidRange");
  }
  if (clipEditorState.endMs - clipEditorState.startMs > clipEditorState.maxDurationMs) {
    return t("clipDurationTooLong", {
      minutes: Math.round(clipEditorState.maxDurationMs / 60000)
    });
  }
  if (
    ["en", "bilingual"].includes(clipSubtitleMode?.value)
    && playbackTrack?.translation?.status !== "completed"
  ) {
    return t("clipEnglishUnavailable");
  }
  return "";
};

const clipSnapCopy = (source) => ({
  manual: t("snapManual"),
  sentence: t("snapSentence"),
  silence: t("snapSilence"),
  checking: t("snapChecking")
}[source] || "");

const updateClipEnglishOptions = () => {
  const englishReady = playbackTrack?.translation?.status === "completed";
  for (const value of ["en", "bilingual"]) {
    const option = clipSubtitleMode?.querySelector(`option[value="${value}"]`);
    if (option) option.disabled = !englishReady;
  }
};

const clipPercent = (milliseconds) => {
  if (!clipEditorState) return 0;
  const span = Math.max(1, clipEditorState.maxMs - clipEditorState.minMs);
  return Math.max(
    0,
    Math.min(100, ((milliseconds - clipEditorState.minMs) / span) * 100)
  );
};

const updateClipHandle = (boundary, handle, output) => {
  if (!clipEditorState || !handle) return;
  const value = boundary === "start"
    ? clipEditorState.startMs
    : clipEditorState.endMs;
  const minimum = boundary === "start"
    ? clipEditorState.minMs
    : clipEditorState.startMs + 100;
  const maximum = boundary === "start"
    ? clipEditorState.endMs - 100
    : clipEditorState.maxMs;
  handle.setAttribute("aria-valuemin", String(minimum));
  handle.setAttribute("aria-valuemax", String(maximum));
  handle.setAttribute("aria-valuenow", String(value));
  handle.setAttribute("aria-valuetext", formatFineClock(value));
  handle.classList.toggle(
    "is-snapped",
    clipEditorState[`${boundary}Source`] !== "manual"
  );
  if (output) output.textContent = formatFineClock(value);
};

const updateClipRangeUI = ({ renderPreview = false } = {}) => {
  if (!clipEditorState) return;
  const {
    minMs, maxMs, startMs, endMs, aiStartMs, aiEndMs
  } = clipEditorState;
  if (clipRangeControl) {
    clipRangeControl.style.setProperty(
      "--clip-start-position",
      `${clipPercent(startMs)}%`
    );
    clipRangeControl.style.setProperty(
      "--clip-end-position",
      `${clipPercent(endMs)}%`
    );
    clipRangeControl.style.setProperty(
      "--clip-ai-start-position",
      `${clipPercent(aiStartMs)}%`
    );
    clipRangeControl.style.setProperty(
      "--clip-ai-end-position",
      `${clipPercent(aiEndMs)}%`
    );
    clipRangeControl.classList.toggle("is-dragging", Boolean(clipDragState));
  }
  if (clipDragState && clipRangeControl) {
    clipRangeControl.style.setProperty(
      "--clip-raw-position",
      `${clipPercent(clipDragState.rawMs)}%`
    );
    if (clipDragState.candidate) {
      clipRangeControl.style.setProperty(
        "--clip-snap-position",
        `${clipPercent(clipDragState.candidate.anchorMs)}%`
      );
    }
  }
  if (clipRawMarker) clipRawMarker.hidden = !clipDragState;
  if (clipSnapMarker) {
    clipSnapMarker.hidden = !clipDragState?.candidate;
  }
  clipStartHandle?.classList.toggle(
    "is-active",
    clipDragState?.boundary === "start"
  );
  clipEndHandle?.classList.toggle(
    "is-active",
    clipDragState?.boundary === "end"
  );
  updateClipHandle("start", clipStartHandle, clipStartHandleTime);
  updateClipHandle("end", clipEndHandle, clipEndHandleTime);
  if (clipStartInput) clipStartInput.value = formatFineClock(startMs);
  if (clipEndInput) clipEndInput.value = formatFineClock(endMs);
  if (clipWindowStart) clipWindowStart.textContent = formatFineClock(minMs);
  if (clipWindowEnd) clipWindowEnd.textContent = formatFineClock(maxMs);
  if (clipSelectedDuration) {
    clipSelectedDuration.textContent = formatFineClock(endMs - startMs);
  }
  if (clipStartSnap) {
    clipStartSnap.textContent = clipSnapCopy(clipEditorState.startSource);
  }
  if (clipEndSnap) {
    clipEndSnap.textContent = clipSnapCopy(clipEditorState.endSource);
  }
  const error = clipValidationMessage();
  if (clipEditorError) {
    clipEditorError.textContent = error || clipEditorState.notice || "";
  }
  if (clipEditorSubmit) {
    clipEditorSubmit.disabled = Boolean(error || clipEditorState.submitting);
  }
  if (clipZoomOut) {
    clipZoomOut.disabled = clipEditorState.zoom <= CLIP_MIN_ZOOM;
  }
  if (clipZoomIn) {
    clipZoomIn.disabled = clipEditorState.zoom >= CLIP_MAX_ZOOM;
  }
  if (renderPreview) renderClipPreview();
};

const setClipBoundary = (
  boundary,
  value,
  source = "manual",
  {
    resetRequest = true,
    invalidateSuggestion = source === "manual",
    clearNotice = source === "manual",
    renderPreview = false
  } = {}
) => {
  if (!clipEditorState || !Number.isFinite(Number(value))) return null;
  const rounded = Math.round(Number(value) / 100) * 100;
  let clamped = Math.max(
    clipEditorState.minMs,
    Math.min(rounded, clipEditorState.maxMs)
  );
  if (boundary === "start") {
    clamped = Math.min(clamped, clipEditorState.endMs - 100);
    clipEditorState.startMs = clamped;
    clipEditorState.startSource = source;
  } else {
    clamped = Math.max(clamped, clipEditorState.startMs + 100);
    clipEditorState.endMs = clamped;
    clipEditorState.endSource = source;
  }
  if (invalidateSuggestion) clipSuggestionTokens[boundary] += 1;
  if (resetRequest) clipEditorState.requestId = null;
  if (clearNotice) clipEditorState.notice = "";
  updateClipRangeUI({ renderPreview });
  return clamped;
};

const clipBoundaryLimits = (boundary) => {
  if (!clipEditorState) return { minimum: 0, maximum: 0 };
  return boundary === "start"
    ? {
      minimum: clipEditorState.minMs,
      maximum: clipEditorState.endMs - 100
    }
    : {
      minimum: clipEditorState.startMs + 100,
      maximum: clipEditorState.maxMs
    };
};

const clipSentenceAnchor = (boundary, subtitle) => (
  boundary === "start" ? subtitle.start_ms : subtitle.end_ms
);

const nearestClipSentence = (
  boundary,
  targetMs,
  thresholdMs = clipEditorState?.snapThresholdMs
) => {
  const subtitles = playbackTrack?.subtitles || [];
  if (!subtitles.length || !clipEditorState) return null;
  const field = boundary === "start" ? "start_ms" : "end_ms";
  const insertion = lowerBoundByTime(subtitles, targetMs, field);
  const { minimum, maximum } = clipBoundaryLimits(boundary);
  const candidates = [subtitles[insertion - 1], subtitles[insertion]]
    .filter((subtitle) => {
      const anchorMs = subtitle ? Number(subtitle[field]) : Number.NaN;
      return Number.isFinite(anchorMs)
        && anchorMs >= minimum
        && anchorMs <= maximum;
    })
    .sort((left, right) => (
      Math.abs(left[field] - targetMs) - Math.abs(right[field] - targetMs)
      || left.sequence - right.sequence
    ));
  const nearest = candidates[0];
  return nearest
    && Math.abs(nearest[field] - targetMs) <= Number(thresholdMs)
    ? nearest
    : null;
};

const seekClipPreview = (milliseconds) => {
  if (!clipPreviewPlayer || !clipEditorState) return;
  const targetMs = Math.max(
    clipEditorState.minMs,
    Math.min(milliseconds, clipEditorState.maxMs)
  );
  clipPreviewPlayer.pause();
  if (clipPreviewPlayer.readyState >= 1) {
    clipPreviewPlayer.currentTime = targetMs / 1000;
    renderClipPreview();
  } else {
    clipPendingSeekMs = targetMs;
  }
  if (clipRangeControl) {
    clipRangeControl.style.setProperty(
      "--clip-preview-position",
      `${clipPercent(targetMs)}%`
    );
  }
  if (clipPreviewPlayhead) clipPreviewPlayhead.hidden = false;
};

const showClipRefinement = () => {
  if (!clipEditorState || !clipRangeControl) return;
  window.clearTimeout(clipEditorState.refineTimer);
  clipRangeControl.classList.add("is-refining");
  clipEditorState.refineTimer = window.setTimeout(() => {
    clipRangeControl.classList.remove("is-refining");
  }, 220);
};

const refineClipBoundary = async (boundary, anchorMs) => {
  if (!clipEditorState || !clipSnapEnabled?.checked || !jobHero) return;
  const state = clipEditorState;
  state[`${boundary}Source`] = "checking";
  updateClipRangeUI();
  const token = ++clipSuggestionTokens[boundary];
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clip-boundaries/suggest`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          timeline_index: state.timelineIndex,
          boundary,
          target_ms: anchorMs
        })
      }
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, "clipSnapFailed"));
    }
    if (
      clipEditorState !== state
      || token !== clipSuggestionTokens[boundary]
      || !clipSnapEnabled?.checked
    ) return;
    showClipRefinement();
    setClipBoundary(boundary, payload.suggested_ms, payload.source, {
      invalidateSuggestion: false,
      clearNotice: false
    });
  } catch (requestError) {
    if (
      clipEditorState !== state
      || token !== clipSuggestionTokens[boundary]
    ) return;
    setClipBoundary(boundary, anchorMs, "sentence", {
      invalidateSuggestion: false,
      clearNotice: false
    });
    state.notice = requestError instanceof Error
      ? requestError.message
      : t("clipSnapFailed");
    updateClipRangeUI();
  }
};

const commitClipBoundary = (
  boundary,
  targetMs,
  { allowSnap = true } = {}
) => {
  if (!clipEditorState) return;
  const nearest = allowSnap && clipSnapEnabled?.checked
    ? nearestClipSentence(boundary, targetMs)
    : null;
  const anchorMs = nearest
    ? clipSentenceAnchor(boundary, nearest)
    : targetMs;
  const finalMs = setClipBoundary(
    boundary,
    anchorMs,
    nearest ? "sentence" : "manual"
  );
  if (finalMs === null) return;
  seekClipPreview(finalMs);
  if (nearest) void refineClipBoundary(boundary, anchorMs);
};

const renderClipTimelineCues = () => {
  if (!clipTimelineCues || !clipEditorState) return;
  const fragment = document.createDocumentFragment();
  for (const subtitle of playbackTrack?.subtitles || []) {
    if (
      subtitle.end_ms <= clipEditorState.minMs
      || subtitle.start_ms >= clipEditorState.maxMs
    ) continue;
    const startMs = Math.max(subtitle.start_ms, clipEditorState.minMs);
    const endMs = Math.min(subtitle.end_ms, clipEditorState.maxMs);
    const cue = document.createElement("span");
    cue.className = "clip-transcript-cue";
    cue.style.left = `${clipPercent(startMs)}%`;
    cue.style.width = `${Math.max(.08, clipPercent(endMs) - clipPercent(startMs))}%`;
    cue.textContent = subtitle.zh || subtitle.en || "";
    cue.title = `${formatFineClock(subtitle.start_ms)} ${cue.textContent}`;
    fragment.append(cue);
  }
  clipTimelineCues.replaceChildren(fragment);
};

const scrollClipTimelineTo = (milliseconds) => {
  if (!clipTimelineViewport || !clipRangeControl || !clipEditorState) return;
  const viewportRect = clipTimelineViewport.getBoundingClientRect();
  const controlRect = clipRangeControl.getBoundingClientRect();
  const controlOffset = controlRect.left
    - viewportRect.left
    + clipTimelineViewport.scrollLeft;
  const canvasX = controlOffset
    + (clipPercent(milliseconds) / 100) * controlRect.width;
  clipTimelineViewport.scrollLeft = Math.max(
    0,
    canvasX - clipTimelineViewport.clientWidth / 2
  );
};

const setClipTimelineZoom = (value, anchorMs = null) => {
  if (!clipEditorState || !clipRangeControl) return;
  const zoom = Math.max(
    CLIP_MIN_ZOOM,
    Math.min(CLIP_MAX_ZOOM, Number(value) || CLIP_MIN_ZOOM)
  );
  clipEditorState.zoom = Math.round(zoom * 100) / 100;
  clipRangeControl.style.width = `${clipEditorState.zoom * 100}%`;
  if (clipZoomLevel) {
    clipZoomLevel.textContent = `${clipEditorState.zoom >= 10
      ? Math.round(clipEditorState.zoom)
      : clipEditorState.zoom.toFixed(1)}×`;
  }
  updateClipRangeUI();
  const anchor = anchorMs ?? (
    clipEditorState.startMs + clipEditorState.endMs
  ) / 2;
  scrollClipTimelineTo(anchor);
};

const fitClipTimeline = () => {
  if (!clipEditorState) return;
  const span = Math.max(1, clipEditorState.maxMs - clipEditorState.minMs);
  const selected = Math.max(
    100,
    clipEditorState.endMs - clipEditorState.startMs
  );
  setClipTimelineZoom(
    Math.max(CLIP_MIN_ZOOM, Math.min(CLIP_MAX_ZOOM, .68 * span / selected))
  );
};

const clipTimeFromClientX = (clientX) => {
  if (!clipEditorState || !clipRangeControl) return 0;
  const rect = clipRangeControl.getBoundingClientRect();
  const fraction = rect.width > 0
    ? Math.max(0, Math.min(1, (clientX - rect.left) / rect.width))
    : 0;
  return clipEditorState.minMs
    + fraction * (clipEditorState.maxMs - clipEditorState.minMs);
};

const clipSnapThresholdForPixels = (
  pixels,
  maximumMs = clipEditorState?.snapThresholdMs
) => {
  if (!clipEditorState || !clipRangeControl) return 0;
  const width = Math.max(1, clipRangeControl.getBoundingClientRect().width);
  const pixelThreshold = (
    pixels / width
  ) * (clipEditorState.maxMs - clipEditorState.minMs);
  return Math.min(Number(maximumMs), pixelThreshold);
};

const updateClipDragAt = (clientX) => {
  if (!clipDragState || !clipEditorState) return;
  const { boundary } = clipDragState;
  const { minimum, maximum } = clipBoundaryLimits(boundary);
  const rawMs = Math.max(
    minimum,
    Math.min(clipTimeFromClientX(clientX), maximum)
  );
  clipDragState.rawMs = rawMs;
  clipDragState.candidate = null;
  let valueMs = rawMs;
  let source = "manual";
  if (clipSnapEnabled?.checked) {
    const releaseMs = clipSnapThresholdForPixels(
      CLIP_SNAP_RELEASE_PX,
      clipEditorState.snapThresholdMs * 2
    );
    const entering = nearestClipSentence(
      boundary,
      rawMs,
      clipSnapThresholdForPixels(CLIP_SNAP_ENTER_PX)
    );
    if (clipDragState.locked && entering) {
      const enteringAnchorMs = clipSentenceAnchor(boundary, entering);
      const switchMarginMs = clipSnapThresholdForPixels(
        3,
        Number.POSITIVE_INFINITY
      );
      if (
        enteringAnchorMs !== clipDragState.locked.anchorMs
        && Math.abs(enteringAnchorMs - rawMs) + switchMarginMs
          < Math.abs(clipDragState.locked.anchorMs - rawMs)
      ) {
        clipDragState.locked = {
          subtitle: entering,
          anchorMs: enteringAnchorMs
        };
      }
    }
    if (
      clipDragState.locked
      && Math.abs(clipDragState.locked.anchorMs - rawMs) > releaseMs
    ) {
      clipDragState.locked = null;
    }
    const nearby = nearestClipSentence(boundary, rawMs, releaseMs);
    if (!clipDragState.locked && entering) {
      clipDragState.locked = {
        subtitle: entering,
        anchorMs: clipSentenceAnchor(boundary, entering)
      };
    }
    clipDragState.candidate = clipDragState.locked || (
      nearby
        ? {
          subtitle: nearby,
          anchorMs: clipSentenceAnchor(boundary, nearby)
        }
        : null
    );
    if (clipDragState.locked) {
      valueMs = clipDragState.locked.anchorMs;
      source = "sentence";
    }
  } else {
    clipDragState.locked = null;
  }
  setClipBoundary(boundary, valueMs, source, {
    invalidateSuggestion: false,
    clearNotice: false
  });
};

const clipAutoScrollVelocity = (clientX) => {
  if (!clipTimelineViewport) return 0;
  const rect = clipTimelineViewport.getBoundingClientRect();
  if (clientX < rect.left + CLIP_AUTO_SCROLL_EDGE_PX) {
    const pressure = Math.min(
      1,
      (rect.left + CLIP_AUTO_SCROLL_EDGE_PX - clientX)
      / CLIP_AUTO_SCROLL_EDGE_PX
    );
    return -Math.max(2, pressure * CLIP_AUTO_SCROLL_MAX_PX);
  }
  if (clientX > rect.right - CLIP_AUTO_SCROLL_EDGE_PX) {
    const pressure = Math.min(
      1,
      (clientX - rect.right + CLIP_AUTO_SCROLL_EDGE_PX)
      / CLIP_AUTO_SCROLL_EDGE_PX
    );
    return Math.max(2, pressure * CLIP_AUTO_SCROLL_MAX_PX);
  }
  return 0;
};

const runClipDragFrame = () => {
  if (!clipDragState || !clipTimelineViewport) return;
  clipDragState.frame = null;
  if (!clipDragState.moved) return;
  const velocity = clipAutoScrollVelocity(clipDragState.clientX);
  const maximumScroll = Math.max(
    0,
    clipTimelineViewport.scrollWidth - clipTimelineViewport.clientWidth
  );
  const previousScroll = clipTimelineViewport.scrollLeft;
  if (velocity) {
    clipTimelineViewport.scrollLeft = Math.max(
      0,
      Math.min(maximumScroll, previousScroll + velocity)
    );
  }
  updateClipDragAt(clipDragState.clientX);
  if (velocity && clipTimelineViewport.scrollLeft !== previousScroll) {
    clipDragState.frame = window.requestAnimationFrame(runClipDragFrame);
  }
};

const queueClipDragFrame = () => {
  if (!clipDragState || clipDragState.frame !== null) return;
  clipDragState.frame = window.requestAnimationFrame(runClipDragFrame);
};

const startClipDrag = (boundary, event) => {
  if (
    !clipEditorState
    || clipEditorState.submitting
    || event.button !== 0
  ) return;
  event.preventDefault();
  clipSuggestionTokens[boundary] += 1;
  clipEditorState.notice = "";
  clipPreviewPlayer?.pause();
  const handle = boundary === "start" ? clipStartHandle : clipEndHandle;
  clipDragState = {
    boundary,
    pointerId: event.pointerId,
    startClientX: event.clientX,
    clientX: event.clientX,
    rawMs: boundary === "start"
      ? clipEditorState.startMs
      : clipEditorState.endMs,
    moved: false,
    candidate: null,
    locked: null,
    frame: null
  };
  handle?.setPointerCapture(event.pointerId);
  updateClipRangeUI();
};

const moveClipDrag = (event) => {
  if (!clipDragState || event.pointerId !== clipDragState.pointerId) return;
  event.preventDefault();
  clipDragState.clientX = event.clientX;
  if (Math.abs(event.clientX - clipDragState.startClientX) >= 2) {
    clipDragState.moved = true;
  }
  queueClipDragFrame();
};

const finishClipDrag = (event, canceled = false) => {
  if (!clipDragState || event.pointerId !== clipDragState.pointerId) return;
  event.preventDefault();
  if (clipDragState.frame !== null) {
    window.cancelAnimationFrame(clipDragState.frame);
  }
  const moved = clipDragState.moved || (
    !canceled
    && Math.abs(event.clientX - clipDragState.startClientX) >= 2
  );
  if (!moved) {
    clipDragState = null;
    updateClipRangeUI();
    return;
  }
  clipDragState.moved = true;
  if (!canceled) clipDragState.clientX = event.clientX;
  updateClipDragAt(clipDragState.clientX);
  const drag = clipDragState;
  const locked = drag.locked && clipSnapEnabled?.checked
    ? drag.locked
    : null;
  const finalMs = locked ? locked.anchorMs : drag.rawMs;
  clipDragState = null;
  const committedMs = setClipBoundary(
    drag.boundary,
    finalMs,
    locked ? "sentence" : "manual",
    { invalidateSuggestion: false, clearNotice: false }
  );
  if (committedMs === null) return;
  seekClipPreview(committedMs);
  if (!canceled && locked) {
    void refineClipBoundary(drag.boundary, locked.anchorMs);
  }
};

const clipSubtitleAt = (milliseconds) => {
  const subtitles = playbackTrack?.subtitles || [];
  const insertion = lowerBoundByTime(subtitles, milliseconds + 1, "start_ms");
  const subtitle = subtitles[insertion - 1];
  return subtitle
    && milliseconds >= subtitle.start_ms
    && milliseconds < subtitle.end_ms
    ? subtitle
    : null;
};

const renderClipPreview = () => {
  if (!clipEditorState || !clipPreviewPlayer) return;
  let milliseconds = (
    clipPreviewPlayer.readyState >= 1
    && Number.isFinite(clipPreviewPlayer.currentTime)
  )
    ? clipPreviewPlayer.currentTime * 1000
    : clipEditorState.startMs;
  if (
    milliseconds < clipEditorState.minMs
    || milliseconds > clipEditorState.maxMs
  ) {
    milliseconds = clipEditorState.startMs;
  }
  if (clipRangeControl) {
    clipRangeControl.style.setProperty(
      "--clip-preview-position",
      `${clipPercent(milliseconds)}%`
    );
  }
  if (clipPreviewPlayhead) clipPreviewPlayhead.hidden = false;
  const mode = clipSubtitleMode?.value || "zh";
  const subtitle = mode === "off" ? null : clipSubtitleAt(milliseconds);
  if (clipPreviewSubtitles && clipPreviewZh && clipPreviewEn) {
    const showZh = subtitle && ["zh", "bilingual"].includes(mode);
    const showEn = subtitle && ["en", "bilingual"].includes(mode) && subtitle.en;
    clipPreviewZh.hidden = !showZh;
    clipPreviewEn.hidden = !showEn;
    clipPreviewZh.textContent = showZh ? subtitle.zh : "";
    clipPreviewEn.textContent = showEn ? subtitle.en : "";
    clipPreviewSubtitles.hidden = !showZh && !showEn;
    clipPreviewSubtitles.classList.toggle(
      "is-with-danmaku",
      Boolean(clipDanmakuEnabled?.checked)
    );
  }
  if (clipPreviewDanmaku) {
    clipPreviewDanmaku.replaceChildren();
    clipPreviewDanmaku.hidden = !clipDanmakuEnabled?.checked;
    if (clipDanmakuEnabled?.checked) {
      const entries = playbackTrack?.danmaku || [];
      const startIndex = lowerBoundByTime(entries, Math.max(0, milliseconds - 5000), "timestamp_ms");
      const endIndex = lowerBoundByTime(entries, milliseconds + 1, "timestamp_ms");
      let lastTimestamp = Number.NEGATIVE_INFINITY;
      const visible = [];
      for (let index = startIndex; index < endIndex; index += 1) {
        const entry = entries[index];
        if (entry.timestamp_ms - lastTimestamp < 450) continue;
        visible.push(entry);
        lastTimestamp = entry.timestamp_ms;
      }
      for (const entry of visible.slice(-5)) {
        const bubble = document.createElement("article");
        const author = document.createElement("strong");
        author.textContent = entry.author || t("anonymous");
        const text = document.createElement("p");
        text.textContent = entry.text;
        bubble.append(author, text);
        clipPreviewDanmaku.append(bubble);
      }
    }
  }
};

const cancelClipPreviewClock = () => {
  if (clipPreviewFrame !== null) {
    window.cancelAnimationFrame(clipPreviewFrame);
  }
  clipPreviewFrame = null;
};

const startClipPreviewClock = () => {
  cancelClipPreviewClock();
  const tick = () => {
    if (!clipEditorState || !clipPreviewPlayer || clipPreviewPlayer.paused) {
      clipPreviewFrame = null;
      return;
    }
    if (clipPreviewPlayer.currentTime * 1000 >= clipEditorState.endMs) {
      clipPreviewPlayer.pause();
      clipPreviewPlayer.currentTime = clipEditorState.endMs / 1000;
      renderClipPreview();
      clipPreviewFrame = null;
      return;
    }
    renderClipPreview();
    clipPreviewFrame = window.requestAnimationFrame(tick);
  };
  clipPreviewFrame = window.requestAnimationFrame(tick);
};

const attachClipPreview = () => {
  if (!clipPreviewPlayer || !replayPlayer) return;
  const source = replayPlayer.dataset.hlsSrc || "";
  if (!source) {
    if (clipEditorError) clipEditorError.textContent = t("clipPreviewUnavailable");
    return;
  }
  clipPreviewUsesNativeHls = false;
  if (window.Hls?.isSupported()) {
    clipPreviewHls = new window.Hls({ enableWorker: true });
    clipPreviewHls.on(window.Hls.Events.MEDIA_ATTACHED, () => {
      clipPreviewHls.loadSource(source);
    });
    clipPreviewHls.on(window.Hls.Events.ERROR, (_event, data) => {
      if (data?.fatal && clipEditorError) {
        clipEditorError.textContent = t("clipPreviewFailed");
      }
    });
    clipPreviewHls.attachMedia(clipPreviewPlayer);
  } else if (clipPreviewPlayer.canPlayType("application/vnd.apple.mpegurl")) {
    clipPreviewUsesNativeHls = true;
    clipPreviewPlayer.src = source;
  } else if (clipEditorError) {
    clipEditorError.textContent = t("clipPreviewUnavailable");
  }
};

const destroyClipPreview = () => {
  cancelClipPreviewClock();
  clipPreviewPlayer?.pause();
  if (clipPreviewHls) {
    clipPreviewHls.destroy();
    clipPreviewHls = null;
  }
  if (clipPreviewPlayer) {
    clipPreviewPlayer.removeAttribute("src");
    clipPreviewPlayer.load();
  }
  clipPendingSeekMs = null;
  clipPendingPlay = false;
  if (clipPreviewPlayhead) clipPreviewPlayhead.hidden = true;
};

const closeClipEditor = () => {
  if (clipEditor?.open) clipEditor.close();
};

const openClipEditor = async (row, button) => {
  if (!clipEditor || !replayPlayer) return;
  if (!playbackTrack) await loadPlaybackTrack();
  replayPlayer.pause();
  clipOriginButton = button;
  const aiStartMs = Number(row.dataset.clipStartMs || 0);
  const aiEndMs = Number(row.dataset.clipEndMs || aiStartMs + 100);
  const configuredDurationMs = Number(clipEditor.dataset.durationMs || 0);
  const mediaDurationMs = Number(replayPlayer.duration) * 1000;
  const contextMs = Number(clipEditor.dataset.contextMs || 600000);
  const durationMs = configuredDurationMs > 0
    ? configuredDurationMs
    : mediaDurationMs > 0
    ? mediaDurationMs
    : aiEndMs + contextMs;
  clipEditorState = {
    timelineIndex: Number(row.dataset.clipIndex),
    title: row.dataset.clipTitle || "",
    aiStartMs,
    aiEndMs,
    minMs: Math.max(0, aiStartMs - contextMs),
    maxMs: Math.min(durationMs, aiEndMs + contextMs),
    maxDurationMs: Number(clipEditor.dataset.maxClipMs || 600000),
    snapThresholdMs: Number(clipEditor.dataset.snapThresholdMs || 1000),
    startMs: aiStartMs,
    endMs: aiEndMs,
    startSource: "manual",
    endSource: "manual",
    requestId: null,
    submitting: false,
    notice: "",
    zoom: CLIP_MIN_ZOOM,
    refineTimer: null
  };
  clipSuggestionTokens = { start: 0, end: 0 };
  clipDragState = null;
  if (clipEditorTopic) clipEditorTopic.textContent = clipEditorState.title;
  if (clipSnapEnabled) clipSnapEnabled.checked = true;
  if (clipSubtitleMode) clipSubtitleMode.value = "zh";
  if (clipDanmakuEnabled) clipDanmakuEnabled.checked = false;
  updateClipEnglishOptions();
  if (clipRangeControl) clipRangeControl.style.width = "100%";
  if (clipTimelineViewport) clipTimelineViewport.scrollLeft = 0;
  clipEditor.showModal();
  updateClipRangeUI({ renderPreview: true });
  renderClipTimelineCues();
  attachClipPreview();
  clipPendingSeekMs = aiStartMs;
  if (clipPreviewPlayhead) clipPreviewPlayhead.hidden = false;
  fitClipTimeline();
};

for (const row of clipRows) {
  const button = row.querySelector(".clip-button");
  button?.addEventListener("click", () => void openClipEditor(row, button));
}
void loadClipExports();

const bindClipHandle = (boundary, handle) => {
  if (!handle) return;
  handle.addEventListener("pointerdown", (event) => {
    startClipDrag(boundary, event);
  });
  handle.addEventListener("pointermove", moveClipDrag);
  handle.addEventListener("pointerup", (event) => {
    finishClipDrag(event);
  });
  handle.addEventListener("pointercancel", (event) => {
    finishClipDrag(event, true);
  });
  handle.addEventListener("keydown", (event) => {
    if (!clipEditorState || !["ArrowLeft", "ArrowRight"].includes(event.key)) {
      return;
    }
    event.preventDefault();
    const direction = event.key === "ArrowLeft" ? -1 : 1;
    const stepMs = event.shiftKey ? 1000 : 100;
    const current = boundary === "start"
      ? clipEditorState.startMs
      : clipEditorState.endMs;
    setClipBoundary(boundary, current + direction * stepMs, "manual");
  });
  handle.addEventListener("keyup", (event) => {
    if (!clipEditorState || !["ArrowLeft", "ArrowRight"].includes(event.key)) {
      return;
    }
    const current = boundary === "start"
      ? clipEditorState.startMs
      : clipEditorState.endMs;
    seekClipPreview(current);
  });
};

bindClipHandle("start", clipStartHandle);
bindClipHandle("end", clipEndHandle);

clipZoomOut?.addEventListener("click", () => {
  if (!clipEditorState) return;
  setClipTimelineZoom(clipEditorState.zoom / 1.5);
});
clipZoomIn?.addEventListener("click", () => {
  if (!clipEditorState) return;
  setClipTimelineZoom(clipEditorState.zoom * 1.5);
});
clipTimelineViewport?.addEventListener("wheel", (event) => {
  if (!clipEditorState || (!event.ctrlKey && !event.metaKey)) return;
  event.preventDefault();
  setClipTimelineZoom(
    event.deltaY < 0
      ? clipEditorState.zoom * 1.18
      : clipEditorState.zoom / 1.18
  );
}, { passive: false });

const commitClipTimeInput = (boundary, input) => {
  const parsed = parseClipTime(input.value);
  if (parsed === null) {
    if (clipEditorState) clipEditorState.notice = t("clipTimeInvalid");
    updateClipRangeUI();
    return;
  }
  commitClipBoundary(boundary, parsed);
};
clipStartInput?.addEventListener("change", () => commitClipTimeInput("start", clipStartInput));
clipEndInput?.addEventListener("change", () => commitClipTimeInput("end", clipEndInput));

document.querySelectorAll("[data-clip-adjust]").forEach((button) => {
  button.addEventListener("click", () => {
    if (!clipEditorState) return;
    const [boundary, delta] = button.dataset.clipAdjust.split(":");
    const current = boundary === "start"
      ? clipEditorState.startMs
      : clipEditorState.endMs;
    const adjusted = setClipBoundary(
      boundary,
      current + Number(delta),
      "manual"
    );
    if (adjusted !== null) seekClipPreview(adjusted);
  });
});

clipSnapEnabled?.addEventListener("change", () => {
  if (!clipEditorState) return;
  clipSuggestionTokens.start += 1;
  clipSuggestionTokens.end += 1;
  clipEditorState.notice = "";
  if (!clipSnapEnabled.checked) {
    clipEditorState.startSource = "manual";
    clipEditorState.endSource = "manual";
  }
  updateClipRangeUI();
});

clipResetRange?.addEventListener("click", () => {
  if (!clipEditorState) return;
  clipSuggestionTokens.start += 1;
  clipSuggestionTokens.end += 1;
  clipEditorState.startMs = clipEditorState.aiStartMs;
  clipEditorState.endMs = clipEditorState.aiEndMs;
  clipEditorState.startSource = "manual";
  clipEditorState.endSource = "manual";
  clipEditorState.requestId = null;
  clipEditorState.notice = "";
  updateClipRangeUI();
  seekClipPreview(clipEditorState.startMs);
  scrollClipTimelineTo(
    (clipEditorState.startMs + clipEditorState.endMs) / 2
  );
});

clipSubtitleMode?.addEventListener("change", () => {
  if (clipEditorState) clipEditorState.requestId = null;
  updateClipRangeUI({ renderPreview: true });
});
clipDanmakuEnabled?.addEventListener("change", () => {
  if (clipEditorState) clipEditorState.requestId = null;
  updateClipRangeUI({ renderPreview: true });
});

clipPreviewPlayer?.addEventListener("loadedmetadata", () => {
  if (clipPendingSeekMs !== null) {
    clipPreviewPlayer.currentTime = clipPendingSeekMs / 1000;
    clipPendingSeekMs = null;
  }
  if (clipPendingPlay) {
    clipPendingPlay = false;
    clipPreviewPlayer.play().catch(() => {});
  }
  renderClipPreview();
});
clipPreviewPlayer?.addEventListener("play", startClipPreviewClock);
clipPreviewPlayer?.addEventListener("pause", () => {
  cancelClipPreviewClock();
  renderClipPreview();
});
clipPreviewPlayer?.addEventListener("seeked", renderClipPreview);
clipPreviewPlayer?.addEventListener("error", () => {
  if (clipEditor?.open && clipEditorError) {
    clipEditorError.textContent = t("clipPreviewFailed");
  }
});
clipPreviewSelection?.addEventListener("click", () => {
  if (!clipEditorState || !clipPreviewPlayer) return;
  const seekAndPlay = () => {
    clipPreviewPlayer.currentTime = clipEditorState.startMs / 1000;
    clipPreviewPlayer.play().catch(() => {
      if (clipEditorError) clipEditorError.textContent = t("clipPreviewFailed");
    });
  };
  if (clipPreviewPlayer.readyState >= 1) {
    seekAndPlay();
  } else {
    clipPendingSeekMs = clipEditorState.startMs;
    clipPendingPlay = true;
    if (clipPreviewUsesNativeHls) clipPreviewPlayer.load();
  }
});

clipEditorClose?.addEventListener("click", closeClipEditor);
clipEditorCancel?.addEventListener("click", closeClipEditor);
clipEditor?.addEventListener("close", () => {
  if (clipDragState?.frame !== null) {
    window.cancelAnimationFrame(clipDragState.frame);
  }
  clipDragState = null;
  if (clipEditorState?.refineTimer) {
    window.clearTimeout(clipEditorState.refineTimer);
  }
  clipRangeControl?.classList.remove("is-dragging", "is-refining");
  if (clipRawMarker) clipRawMarker.hidden = true;
  if (clipSnapMarker) clipSnapMarker.hidden = true;
  clipTimelineCues?.replaceChildren();
  destroyClipPreview();
  clipEditorState = null;
  clipSuggestionTokens.start += 1;
  clipSuggestionTokens.end += 1;
  clipOriginButton?.focus();
  clipOriginButton = null;
});

clipEditorSubmit?.addEventListener("click", async () => {
  if (!clipEditorState || !jobHero) return;
  const validation = clipValidationMessage();
  if (validation) {
    if (clipEditorError) clipEditorError.textContent = validation;
    return;
  }
  clipSuggestionTokens.start += 1;
  clipSuggestionTokens.end += 1;
  if (clipEditorState.startSource === "checking") {
    clipEditorState.startSource = "sentence";
  }
  if (clipEditorState.endSource === "checking") {
    clipEditorState.endSource = "sentence";
  }
  clipEditorState.submitting = true;
  updateClipRangeUI();
  clipEditorSubmit.textContent = t("starting");
  clipEditorState.requestId ||= (
    window.crypto?.randomUUID?.()
    || `clip-${Date.now()}-${Math.random().toString(16).slice(2)}`
  );
  try {
    const response = await apiFetch(
      `/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/clip-exports`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          request_id: clipEditorState.requestId,
          timeline_index: clipEditorState.timelineIndex,
          start_ms: clipEditorState.startMs,
          end_ms: clipEditorState.endMs,
          subtitle_mode: clipSubtitleMode?.value || "zh",
          include_danmaku: Boolean(clipDanmakuEnabled?.checked)
        })
      }
    );
    const payload = await response.json();
    if (!response.ok) {
      throw new Error(apiErrorMessage(payload, "startClipFailed"));
    }
    clipExports = [
      payload,
      ...clipExports.filter((clip) => clip.id !== payload.id)
    ];
    renderClipExports();
    closeClipEditor();
    window.setTimeout(() => void loadClipExports(), 1000);
  } catch (requestError) {
    clipEditorState.notice = requestError instanceof Error
      ? requestError.message
      : t("startClipFailed");
  } finally {
    if (clipEditorState) {
      clipEditorState.submitting = false;
      updateClipRangeUI();
    } else {
      clipEditorSubmit.disabled = false;
    }
    clipEditorSubmit.textContent = t("createClipExport");
  }
});

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
    updateClipEnglishOptions();
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
  renderClipExports();
  updateClipRangeUI();
  updateDanmakuAuthorFilter();
});
