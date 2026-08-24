(() => {
  const dictionaries = {
    zh: {
      brandSubtitle: "公开回放字幕与总结",
      siteTitle: "口袋48直播总结",
      loginDocumentTitle: "登录 · 口袋48直播总结",
      requestFailedTitle: "请求失败",
      logout: "退出",
      switchLanguage: "切换为英文",
      homeHeroTitle: "从直播回放到字幕、总结与精彩片段",
      homeHeroLede: "粘贴公开回放链接，自动整理完整字幕与弹幕，生成 AI 时间线和高光；支持在线播放、按时间跳转，并一键剪辑视频。",
      quotaUnlimited: "当前额度：无限任务，单个回放最长由站点配置限制。",
      quotaDaily: "当前额度：每天 {count} 个任务，单个回放最长由站点配置限制。",
      publicAccess: "已完成的回放、字幕与总结可公开浏览；登录后可以提交新回放，并从时间线一键剪视频。",
      configNotReady: "处理配置尚未就绪",
      configFill: "请在环境配置中补齐：",
      shareUrlLabel: "口袋48成员直播分享链接",
      startProcessing: "开始处理",
      publicReplayOnly: "仅支持已结束、无需登录即可访问的公开回放。",
      summarizeNewReplay: "想总结新的直播？",
      inviteQuota: "邀请账号每天可以提交 {count} 个新任务，并可从时间线一键剪视频。",
      login: "登录",
      resultsAndMine: "公开结果与我的任务",
      recentResults: "最近公开结果",
      liveTime: "直播时间",
      waitingReplay: "等待解析回放",
      unknownMember: "未知主播",
      noPublicResults: "还没有公开结果",
      resultsAppearHere: "完成后的直播总结会显示在这里。",
      loginTitle: "登录口袋48直播总结",
      inviteOnlyCopy: "当前为邀请制试用，请使用管理员创建的账号。",
      username: "用户名",
      password: "密码",
      backHome: "返回首页",
      backToJobs: "← 返回任务列表",
      waitingMember: "等待解析主播",
      parsingReplay: "正在解析回放",
      liveId: "直播 ID",
      chinaTime: "中国时间",
      retryJob: "重试任务",
      cleanupWarning: "结果已保存，但临时文件清理需要注意",
      synchronizedReplay: "同步回放",
      loadingPlaybackData: "正在载入字幕与弹幕",
      playbackSettings: "播放显示设置",
      subtitles: "字幕",
      chinese: "中文",
      english: "English",
      bilingual: "中英双语",
      off: "关闭",
      showDanmaku: "显示弹幕",
      danmakuDensity: "弹幕密度",
      densityLow: "清爽",
      densityNormal: "标准",
      densityHigh: "丰富",
      advancedSync: "高级同步",
      offset: "偏移",
      synchronizedDanmaku: "同步弹幕",
      danmakuStartsOnPlay: "播放后，弹幕会在这里随视频出现。",
      replayHelp: "点击时间线、高光或字幕中的时间，即可跳转到对应位置。",
      generateEnglish: "生成英文字幕",
      openRawHls: "打开原始 HLS 回放",
      overview: "摘要",
      timeline: "时间线",
      clipVideo: "剪视频",
      highlights: "高光",
      danmakuReference: "弹幕参考：",
      danmakuPeaks: "弹幕活跃高峰",
      peakStats: "{count} 条弹幕 · 强度 {score}",
      transcript: "字幕",
      transcriptUnavailable: "字幕尚未生成。",
      danmaku: "弹幕",
      showAll: "显示全部",
      danmakuUnavailable: "没有可用弹幕。",
      jobEvents: "处理记录",
      createJobFailed: "创建任务失败",
      readJobFailed: "读取任务状态失败",
      jumpedToTarget: "已跳转到目标时间，点击播放键开始播放。",
      mediaError: "回放加载失败{detail}，请刷新重试。",
      mediaErrorCode: "（媒体错误 {code}）",
      mediaErrorDetail: "（{detail}）",
      networkRetry: "回放网络波动，正在重试…",
      mediaRecover: "视频解码异常，正在恢复…",
      unsupportedHls: "当前浏览器不支持 HLS 回放。",
      loadingReplay: "正在加载回放，请稍候…",
      clipping: "剪辑中…",
      ffmpegClipping: "FFmpeg 正在后台生成视频。",
      clipped: "已剪好",
      generatedFile: "{filename} 已生成 · ",
      downloadMp4: "下载 MP4",
      retryClip: "重试剪视频",
      clipFailed: "视频剪辑失败",
      readClipFailed: "读取剪辑状态失败",
      starting: "正在启动…",
      startClipFailed: "启动视频剪辑失败",
      retryFailed: "重试失败",
      anonymous: "匿名",
      danmakuDisabled: "弹幕已关闭。",
      noNearbyDanmaku: "当前时间附近没有弹幕。",
      englishReady: "英文字幕已就绪",
      englishGenerating: "英文字幕正在后台生成",
      englishFailed: "英文字幕生成失败",
      retryTranslation: "重试翻译",
      autoEnglishOnPlay: "开始播放后自动生成英文字幕",
      loginForEnglish: "登录后可为历史直播生成英文字幕",
      generateNow: "现在生成",
      readTranslationFailed: "读取英文字幕状态失败",
      startTranslationFailed: "启动英文字幕生成失败",
      loadPlaybackTrackFailed: "加载同步播放数据失败",
      frameSync: "逐视频帧同步",
      mediaClockSync: "媒体时钟同步",
      syncSummary: "{precision} · {subtitles} 条字幕 · {danmaku} 条弹幕",
      playbackTrackFailed: "同步播放数据加载失败",
      displayedLoadFailed: "（已显示 {count} 条，加载失败）",
      continueLoading: "继续加载",
      totalCount: "（共 {count} 条）",
      loadingAll: "（已显示 {count} 条，正在加载全部…）",
      loadTranscriptFailed: "加载字幕失败",
      jumpTo: "跳转到 {time}",
      loadingFanDanmaku: "正在加载粉丝 {author} 的全部弹幕…",
      fanDanmakuTotal: "粉丝 {author} · 本场共 {count} 条",
      foundLoading: "（已找到 {count} 条，正在加载全部…）",
      filteredTotal: "（已筛选 {count} 条，本场共 {total} 条）",
      loadDanmakuFailed: "加载弹幕失败"
    },
    en: {
      brandSubtitle: "Public replay captions and summaries",
      siteTitle: "Pocket48 Replay Summarizer",
      loginDocumentTitle: "Log in · Pocket48 Replay Summarizer",
      requestFailedTitle: "Request failed",
      logout: "Log out",
      switchLanguage: "Switch to Chinese",
      homeHeroTitle: "Turn livestream replays into captions, summaries, and clips",
      homeHeroLede: "Paste a public replay link to organize its full transcript and danmaku, generate an AI timeline and highlights, watch online, jump by timestamp, and create clips.",
      quotaUnlimited: "Current quota: unlimited jobs. Replay length follows the site limit.",
      quotaDaily: "Current quota: {count} jobs per day. Replay length follows the site limit.",
      publicAccess: "Completed replays, captions, and summaries are public. Log in to submit a replay or create timeline clips.",
      configNotReady: "Processing configuration is not ready",
      configFill: "Complete these environment settings:",
      shareUrlLabel: "Pocket48 member livestream share link",
      startProcessing: "Start processing",
      publicReplayOnly: "Only completed public replays that do not require login are supported.",
      summarizeNewReplay: "Want to summarize a new replay?",
      inviteQuota: "Invited accounts can submit {count} new jobs per day and create timeline clips.",
      login: "Log in",
      resultsAndMine: "Public results and my jobs",
      recentResults: "Recent public results",
      liveTime: "Live time",
      waitingReplay: "Waiting to resolve replay",
      unknownMember: "Unknown member",
      noPublicResults: "No public results yet",
      resultsAppearHere: "Completed livestream summaries will appear here.",
      loginTitle: "Log in to Pocket48 Replay Summarizer",
      inviteOnlyCopy: "This beta is invite-only. Use an account created by the administrator.",
      username: "Username",
      password: "Password",
      backHome: "Back to home",
      backToJobs: "← Back to jobs",
      waitingMember: "Waiting to resolve member",
      parsingReplay: "Resolving replay",
      liveId: "Live ID",
      chinaTime: "China time",
      retryJob: "Retry job",
      cleanupWarning: "Results are saved, but temporary-file cleanup needs attention",
      synchronizedReplay: "Synchronized replay",
      loadingPlaybackData: "Loading captions and danmaku",
      playbackSettings: "Playback display settings",
      subtitles: "Captions",
      chinese: "Chinese",
      english: "English",
      bilingual: "Bilingual",
      off: "Off",
      showDanmaku: "Show danmaku",
      danmakuDensity: "Danmaku density",
      densityLow: "Light",
      densityNormal: "Standard",
      densityHigh: "Rich",
      advancedSync: "Advanced sync",
      offset: "Offset",
      synchronizedDanmaku: "Synchronized danmaku",
      danmakuStartsOnPlay: "Danmaku will appear here as the video plays.",
      replayHelp: "Click a timeline, highlight, or caption timestamp to jump to it.",
      generateEnglish: "Generate English captions",
      openRawHls: "Open the raw HLS replay",
      overview: "Overview",
      timeline: "Timeline",
      clipVideo: "Create clip",
      highlights: "Highlights",
      danmakuReference: "Danmaku context: ",
      danmakuPeaks: "Danmaku activity peaks",
      peakStats: "{count} comments · intensity {score}",
      transcript: "Transcript",
      transcriptUnavailable: "Captions are not available yet.",
      danmaku: "Danmaku",
      showAll: "Show all",
      danmakuUnavailable: "No danmaku is available.",
      jobEvents: "Job events",
      createJobFailed: "Could not create the job",
      readJobFailed: "Could not read job status",
      jumpedToTarget: "Jumped to the target time. Press play to continue.",
      mediaError: "Replay failed to load{detail}. Refresh and try again.",
      mediaErrorCode: " (media error {code})",
      mediaErrorDetail: " ({detail})",
      networkRetry: "Replay network issue. Retrying…",
      mediaRecover: "Video decode issue. Recovering…",
      unsupportedHls: "This browser does not support HLS playback.",
      loadingReplay: "Loading the replay…",
      clipping: "Clipping…",
      ffmpegClipping: "FFmpeg is generating the video in the background.",
      clipped: "Clip ready",
      generatedFile: "{filename} is ready · ",
      downloadMp4: "Download MP4",
      retryClip: "Retry clip",
      clipFailed: "Video clipping failed",
      readClipFailed: "Could not read clip status",
      starting: "Starting…",
      startClipFailed: "Could not start video clipping",
      retryFailed: "Retry failed",
      anonymous: "Anonymous",
      danmakuDisabled: "Danmaku is off.",
      noNearbyDanmaku: "No danmaku near this time.",
      englishReady: "English captions are ready",
      englishGenerating: "English captions are being generated",
      englishFailed: "English caption generation failed",
      retryTranslation: "Retry translation",
      autoEnglishOnPlay: "English captions will be generated when playback starts",
      loginForEnglish: "Log in to generate English captions for historical replays",
      generateNow: "Generate now",
      readTranslationFailed: "Could not read English caption status",
      startTranslationFailed: "Could not start English caption generation",
      loadPlaybackTrackFailed: "Could not load synchronized playback data",
      frameSync: "Video-frame sync",
      mediaClockSync: "Media-clock sync",
      syncSummary: "{precision} · {subtitles} captions · {danmaku} comments",
      playbackTrackFailed: "Synchronized playback data failed to load",
      displayedLoadFailed: "({count} shown; loading failed)",
      continueLoading: "Continue loading",
      totalCount: "({count} total)",
      loadingAll: "({count} shown; loading all…)",
      loadTranscriptFailed: "Could not load captions",
      jumpTo: "Jump to {time}",
      loadingFanDanmaku: "Loading all danmaku from {author}…",
      fanDanmakuTotal: "{author} · {count} comments in this replay",
      foundLoading: "({count} found; loading all…)",
      filteredTotal: "({count} shown; {total} total)",
      loadDanmakuFailed: "Could not load danmaku"
    }
  };

  const operationalMessages = {
    "等待处理": "Waiting to process",
    "任务已创建": "Job created",
    "正在解析公开回放": "Resolving public replay",
    "正在解析回放弹幕": "Parsing replay danmaku",
    "正在检查HLS回放": "Inspecting HLS replay",
    "正在从HLS提取语音音频": "Extracting speech audio from HLS",
    "正在上传临时音频到私有OSS": "Uploading temporary audio to private OSS",
    "正在提交DashScope识别任务": "Submitting DashScope transcription",
    "正在等待DashScope语音识别": "Waiting for DashScope transcription",
    "正在生成时间戳字幕": "Generating timestamped captions",
    "正在分段总结字幕": "Summarizing caption chunks",
    "正在生成整场结构化总结": "Generating the structured replay summary",
    "正在清理临时音频": "Cleaning up temporary audio",
    "处理完成": "Completed",
    "等待重试": "Waiting to retry",
    "应用重启后等待恢复": "Waiting to recover after restart",
    "应用停止，任务已安全重新排队": "App stopped; job was safely requeued",
    "Worker停止，任务已重新排队": "Worker stopped; job was requeued"
  };

  const statusLabels = {
    zh: {
      queued: "排队中",
      running: "处理中",
      completed: "已完成",
      failed: "失败"
    },
    en: {
      queued: "Queued",
      running: "Processing",
      completed: "Completed",
      failed: "Failed"
    }
  };

  const serverMessages = {
    "请先登录或重新登录": "Please log in or sign in again",
    "用户名或密码错误": "Incorrect username or password",
    "登录失败次数过多，请稍后再试": "Too many failed sign-in attempts. Try again later.",
    "请求安全令牌无效，请刷新页面后重试": "The security token is invalid. Refresh the page and try again.",
    "任务不存在": "Job not found",
    "分享链接格式无效": "The share link format is invalid",
    "仅支持 h5.48.cn 的公开成员直播分享链接": "Only public member livestream links from h5.48.cn are supported",
    "分享链接缺少有效的直播 ID": "The share link does not contain a valid live ID",
    "直播处理完成后才能生成英文字幕": "English captions can be generated after replay processing completes",
    "字幕尚未生成，无法翻译": "Captions are not ready for translation",
    "英文字幕生成失败，请登录后重试": "English caption generation failed. Log in and retry.",
    "视频剪辑服务未启动": "The video clipping service is unavailable",
    "服务正在发布新版本，请稍后再剪辑": "A new release is being deployed. Try clipping again shortly.",
    "视频片段尚未生成完成": "The video clip is not ready yet",
    "字幕尚未生成": "Captions are not ready yet",
    "总结尚未生成": "The summary is not ready yet"
  };

  const storageKey = "p48-language";

  const readLanguage = () => {
    try {
      return window.localStorage.getItem(storageKey) === "en" ? "en" : "zh";
    } catch {
      return "zh";
    }
  };

  let currentLanguage = readLanguage();

  const interpolate = (message, params = {}) => message.replace(
    /\{([a-zA-Z0-9_]+)\}/g,
    (_match, name) => String(params[name] ?? "")
  );

  const t = (key, params = {}) => interpolate(
    dictionaries[currentLanguage][key] || dictionaries.zh[key] || key,
    params
  );

  const elementParams = (element) => {
    const params = {};
    for (const attribute of element.attributes) {
      if (!attribute.name.startsWith("data-i18n-param-")) continue;
      params[attribute.name.slice("data-i18n-param-".length)] = attribute.value;
    }
    return params;
  };

  const translateOperationalMessage = (message) => {
    if (currentLanguage === "zh" || !message) return message;
    const compact = message.replaceAll(" ", "");
    const chunkMatch = compact.match(/^正在总结字幕分段(\d+)\/(\d+)$/);
    if (chunkMatch) {
      return `Summarizing caption chunks ${chunkMatch[1]}/${chunkMatch[2]}`;
    }
    return operationalMessages[compact] || message;
  };

  const translateStatus = (status) => (
    statusLabels[currentLanguage][status] || status
  );

  const translateServerMessage = (message) => {
    if (currentLanguage === "zh" || !message) return message;
    const quotaMatch = message.match(/^今日任务额度已用完（每天 (\d+) 个）$/);
    if (quotaMatch) {
      return `Today's quota is exhausted (${quotaMatch[1]} jobs per day)`;
    }
    return serverMessages[message] || message;
  };

  const setText = (element, key, params = {}) => {
    if (!element) return;
    element.dataset.i18nRuntime = key;
    element.dataset.i18nRuntimeParams = JSON.stringify(params);
    element.textContent = t(key, params);
  };

  const apply = () => {
    document.documentElement.lang = currentLanguage === "en" ? "en" : "zh-CN";
    if (window.location.pathname === "/") {
      document.title = t("siteTitle");
    } else if (window.location.pathname === "/login") {
      document.title = t("loginDocumentTitle");
    } else if (document.querySelector(".error-page")) {
      document.title = t("requestFailedTitle");
    }
    document.querySelectorAll("[data-i18n]").forEach((element) => {
      element.textContent = t(element.dataset.i18n, elementParams(element));
    });
    document.querySelectorAll("[data-i18n-placeholder]").forEach((element) => {
      element.placeholder = t(
        element.dataset.i18nPlaceholder,
        elementParams(element)
      );
    });
    document.querySelectorAll("[data-i18n-title]").forEach((element) => {
      element.title = t(element.dataset.i18nTitle, elementParams(element));
    });
    document.querySelectorAll("[data-i18n-aria-label]").forEach((element) => {
      element.setAttribute(
        "aria-label",
        t(element.dataset.i18nAriaLabel, elementParams(element))
      );
    });
    document.querySelectorAll("[data-job-status]").forEach((element) => {
      element.textContent = translateStatus(element.dataset.jobStatus);
    });
    document.querySelectorAll("[data-operational-message]").forEach((element) => {
      element.textContent = translateOperationalMessage(
        element.dataset.operationalMessage
      );
    });
    document.querySelectorAll("[data-server-message]").forEach((element) => {
      element.textContent = translateServerMessage(
        element.dataset.serverMessage
      );
    });
    document.querySelectorAll("[data-i18n-runtime]").forEach((element) => {
      let params = {};
      try {
        params = JSON.parse(element.dataset.i18nRuntimeParams || "{}");
      } catch {
        params = {};
      }
      element.textContent = t(element.dataset.i18nRuntime, params);
    });
    const toggle = document.querySelector("#language-toggle");
    if (toggle) {
      toggle.textContent = currentLanguage === "zh" ? "EN" : "中文";
      toggle.setAttribute("aria-label", t("switchLanguage"));
      toggle.title = t("switchLanguage");
    }
    document.dispatchEvent(
      new CustomEvent("p48:languagechange", {
        detail: { language: currentLanguage }
      })
    );
  };

  const toggleLanguage = () => {
    currentLanguage = currentLanguage === "zh" ? "en" : "zh";
    try {
      window.localStorage.setItem(storageKey, currentLanguage);
    } catch {
      // The active page still switches even if browser storage is unavailable.
    }
    apply();
  };

  window.P48I18n = {
    apply,
    get language() {
      return currentLanguage;
    },
    setText,
    t,
    translateOperationalMessage,
    translateServerMessage,
    translateStatus
  };

  document.querySelector("#language-toggle")?.addEventListener(
    "click",
    toggleLanguage
  );
  apply();
})();
