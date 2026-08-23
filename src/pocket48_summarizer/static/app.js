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
      const response = await fetch("/api/jobs", {
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
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobID)}/status`);
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

const retryButton = document.querySelector("#retry-job");
if (retryButton && jobHero) {
  retryButton.addEventListener("click", async () => {
    retryButton.disabled = true;
    try {
      const response = await fetch(`/api/jobs/${encodeURIComponent(jobHero.dataset.jobId)}/retry`, {
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
