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
let pendingSeekSeconds = null;

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
  replayPlayer.addEventListener("loadedmetadata", applyPendingSeek);
  replayPlayer.addEventListener("error", () => {
    if (replayPlayerMessage) {
      replayPlayerMessage.textContent =
        "当前浏览器无法直接播放此 HLS 回放；可尝试 iPhone、iPad 或 Safari。";
    }
  });
}

document.addEventListener("click", (event) => {
  const timestamp = event.target.closest("[data-seek-ms]");
  if (!timestamp || !replayPlayer) return;
  pendingSeekSeconds = Math.max(0, Number(timestamp.dataset.seekMs || 0) / 1000);
  replayPlayerPanel?.scrollIntoView({ behavior: "smooth", block: "start" });
  if (replayPlayer.readyState >= 1) {
    applyPendingSeek();
  } else {
    replayPlayer.load();
  }
});

const clipRows = document.querySelectorAll(".timeline > li[data-clip-index]");

const renderClipState = (row, payload) => {
  const button = row.querySelector(".clip-button");
  const status = row.querySelector(".clip-status");
  if (!button || !status) return;
  status.replaceChildren();
  if (payload.status === "running") {
    button.disabled = true;
    button.textContent = "剪辑中…";
    status.hidden = false;
    status.textContent = "FFmpeg 正在后台生成视频。";
    return;
  }
  if (payload.status === "completed") {
    button.disabled = true;
    button.textContent = "已剪好";
    status.hidden = false;
    status.append(document.createTextNode(`${payload.filename} 已生成 · `));
    const link = document.createElement("a");
    link.href = payload.download_url;
    link.textContent = "下载 MP4";
    status.append(link);
    return;
  }
  if (payload.status === "failed") {
    button.disabled = false;
    button.textContent = "重试剪视频";
    status.hidden = false;
    status.textContent = payload.error || "视频剪辑失败";
    return;
  }
  button.disabled = false;
  button.textContent = "剪视频";
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
  if (!button) continue;
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
