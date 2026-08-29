(() => {
  const dictionaries = {
    zh: {
      brandSubtitle: "公开回放字幕与总结",
      siteTitle: "口袋48直播总结",
      loginDocumentTitle: "登录 · 口袋48直播总结",
      requestFailedTitle: "请求失败",
      logout: "退出",
      glossaryNav: "词库",
      switchLanguage: "切换为英文",
      historyNavigation: "页面导航",
      goBack: "后退",
      goForward: "前进",
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
      memberFilter: "按成员筛选",
      allMembers: "全部成员",
      applyFilter: "筛选",
      clearFilter: "清除",
      liveTime: "直播时间",
      waitingReplay: "等待解析回放",
      unknownMember: "未知主播",
      noPublicResults: "还没有公开结果",
      resultsAppearHere: "完成后的直播总结会显示在这里。",
      noMemberResults: "该成员暂无可见直播",
      tryAnotherMember: "请选择其他成员或清除筛选。",
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
      retryClipFailed: "重试剪辑失败",
      clipFailed: "视频剪辑失败",
      readClipFailed: "读取剪辑状态失败",
      starting: "正在启动…",
      startClipFailed: "启动视频剪辑失败",
      clipEditorTitle: "调整剪辑",
      closeClipEditor: "关闭剪辑编辑器",
      clipTimeRange: "剪辑时间范围",
      clipTimeline: "精细时间线",
      clipTimelineHint: "移动红线预览，单击会留下标记并跳转播放器；可在标记处分割，拖动边界靠近标记线会优先吸附",
      timelineLyricPreview: "时间轴字幕预览",
      timelineLyricDisabled: "字幕已关闭",
      timelineLyricUnavailable: "这个位置附近没有字幕",
      clipZoomOut: "缩小时间线",
      clipZoomIn: "放大时间线",
      clipKeyboardHint: "聚焦边界后用 ←/→ 微调 0.1 秒，按住 Shift 调整 1 秒；按空格键播放或暂停。",
      manualCutEditing: "手动删减片段",
      manualCutEditingHint: "单击时间轴会标记并跳转播放器；分割后删除不需要的片段，导出时按原顺序自动拼接。",
      splitAtMarker: "在标记处分割",
      deleteSelectedSegment: "删除选中片段",
      restoreSelectedSegment: "恢复选中片段",
      clipSegments: "成片轨道",
      clipOutputTrack: "成片轨道",
      clipOutputTrackHint: "这里只显示最终保留片段；可点上方斜线区域恢复删除片段。",
      clipCutSummary: "成片 {duration} · {kept} 段",
      clipKeptDuration: "成片 {duration} · {count} 段",
      clipExportDurationSummary: "成片 {duration} · {count} 段",
      clipSegmentKept: "保留",
      clipSegmentDeleted: "已删除",
      keptSegmentLabel: "片段 {index}，保留，{start} 到 {end}",
      deletedSegmentLabel: "片段 {index}，已删除，{start} 到 {end}",
      clipSplitUnavailable: "请先在保留片段内部留下标记，并让标记距离片段边界至少 0.1 秒。",
      clipSplitComplete: "已在 {time} 分割；请选择要删除的片段。",
      clipSelectSegmentFirst: "请先选择一个片段。",
      clipMustKeepOneSegment: "至少需要保留一个片段。",
      clipSegmentDeletedNotice: "已删除该片段，预览和导出会自动跳过。",
      clipSegmentRestoredNotice: "已恢复该片段。",
      clipDeletedPreview: "此片段不会出现在导出视频中",
      clipStartRange: "剪辑开始时间",
      clipEndRange: "剪辑结束时间",
      playSelection: "播放所选区间",
      clipOutputLayout: "导出画面",
      clipPortraitLayout: "竖屏 9:16",
      clipPortraitLayoutHint: "保持原视频与字幕样式",
      clipLandscapeLayout: "横屏 16:9",
      clipLandscapeLayoutHint: "米白画布 · 中间视频 · 两侧信息",
      clipLandscapeStyleNote: "横屏固定使用米白画布、红色字幕和玫瑰粉弹幕；字号和字体可调整。",
      landscapeFixedPalette: "固定横屏配色",
      snapToSpeech: "吸附语句与静音边界",
      resetAiRange: "恢复 AI 区间",
      burnedSubtitles: "烧录字幕",
      burnDanmaku: "烧录弹幕",
      subtitleStyle: "字幕样式",
      subtitleStyleHint: "实时预览会与最终导出保持一致",
      vibrantCalmTheme: "活力柔和",
      customSubtitleTheme: "自定义",
      subtitleFontSize: "字幕字号",
      subtitleFontFamily: "横屏字幕字体",
      subtitleFontWenkai: "霞鹜文楷",
      subtitleFontSerif: "思源宋体",
      subtitleFontSans: "思源黑体",
      subtitleTextColor: "文字颜色",
      subtitleBackgroundColor: "字幕底色",
      subtitleContrastGood: "对比度 {ratio}:1 · 适合大字幕",
      subtitleContrastLow: "对比度 {ratio}:1 · 请换一组更清晰的颜色",
      subtitleContrastRequired: "文字与底色的对比度至少需要 3:1。",
      aiCoverTitle: "AI 生成封面",
      aiCoverHint: "用 MARK 截图生成 16:9 与 4:3 横屏封面，标题文字由 Seedream 直接画在图上；提示词可自行修改。",
      aiCoverLoginRequired: "登录后由管理员生成 AI 封面。",
      aiCoverAdminOnly: "当前为管理员内测功能，你可以查看生成效果。",
      aiCoverNotConfigured: "管理员尚未配置 Seedream。",
      aiCoverReadyHint: "先在精细时间线上点击 MARK，再生成封面。",
      aiCoverMarkedFrame: "MARK 截图",
      aiCoverMarkHint: "红色标记所在画面会作为人物与场景参考。",
      aiCoverMainText: "封面文字",
      aiCoverPrompt: "生图提示词",
      aiCoverPromptHint: "提示词里的 {title} 会替换成上面的封面文字，{ratio} 会替换成 16:9 或 4:3。删掉 {title} 也可以，系统会把文字追加到末尾。",
      aiCoverPromptReset: "恢复默认提示词",
      aiCoverGenerate: "一键生成双尺寸",
      aiCoverRegenerate: "用当前文字与提示词重新生成",
      aiCoverRetry: "重试失败任务",
      aiCoverUseForVideo: "设为视频封面",
      aiCoverRemoveFromVideo: "取消使用此封面",
      aiCoverLandscapeVideoOnly: "切换到横屏后可用于成片",
      aiCoverLandscape: "16:9 横屏",
      aiCoverFourThree: "4:3 横屏",
      aiCoverWaiting: "等待生成",
      aiCoverDownloadPng: "下载 PNG",
      aiCoverHistory: "历史版本",
      aiCoverNoHistory: "还没有生成记录。",
      aiCoverHistoryItem: "第 {number} 版 · {style} · {time} · {status}",
      aiCoverFrameNote: "16:9 可用于横屏成片第 0 帧；4:3 仅用于下载。两者都不会增加视频时长或延后声音。",
      aiCoverQueued: "已排队，等待 Seedream 生成。",
      aiCoverRunning: "正在生成两种比例并渲染文字…",
      aiCoverCompleted: "双尺寸封面已生成；切换模板或修改文字不会再次调用 Seedream。",
      aiCoverFailed: "AI 封面生成失败，可以重试或换一版。",
      aiCoverModerationRejected: "图片未通过 Seedream 内容审核，请换一个 MARK 画面。",
      aiCoverLandscapeAlt: "AI 生成的 16:9 横屏封面",
      aiCoverFourThreeAlt: "AI 生成的 4:3 横屏封面",
      aiCoverLoadFailed: "AI 封面记录加载失败。",
      aiCoverRequestFailed: "AI 封面请求失败。",
      aiCoverMarkRequired: "请先在保留片段内点击时间线留下 MARK。",
      aiCoverTextRequired: "请输入封面文字。",
      remove: "删除",
      cancel: "取消",
      createClipExport: "生成剪辑",
      loginToCreateClip: "登录后生成",
      clipPreviewOnly: "仅预览",
      clipGuestPreviewHint: "游客可以体验全部剪辑设置；登录后才能生成视频。",
      clipPreviewMaintenanceHint: "当前可以预览剪辑设置，但剪辑服务正在维护。",
      noSubtitles: "无字幕",
      chineseSubtitles: "中文字幕",
      englishSubtitles: "英文字幕",
      bilingualSubtitles: "中英字幕",
      clipStatus_running: "正在后台生成",
      clipStatus_completed: "剪辑已生成",
      clipStatus_failed: "剪辑生成失败",
      clipOutsideWindow: "所选区间不能超出本条时间线前后 10 分钟的编辑范围。",
      clipInvalidRange: "结束时间必须晚于开始时间。",
      clipNoKeptSegments: "至少需要保留一个视频片段。",
      clipTooManyKeptSegments: "最多可以保留 {count} 个片段。",
      clipDurationTooLong: "剪辑时长不能超过 {minutes} 分钟。",
      clipEnglishUnavailable: "英文字幕尚未完整生成，暂不能选择英文或双语。",
      clipSnapFailed: "静音边界分析失败，已保留语句边界。",
      clipPreviewUnavailable: "当前浏览器无法预览此 HLS 回放。",
      clipPreviewFailed: "所选片段预览加载失败，请稍后重试。",
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
      loadDanmakuFailed: "加载弹幕失败",
      glossaryDocumentTitle: "成员与术语词库 · 口袋48直播总结",
      glossaryAdminTitle: "成员与术语词库",
      glossaryAdminLede: "官方成员资料只读同步；管理员可以补充昵称、CP 名、歌曲、公演和团内术语。新词库只用于之后创建的任务。",
      glossarySaved: "词库更改已保存。",
      catalogLastSyncFailed: "最近一次官方目录同步失败",
      catalogMembers: "目录成员",
      catalogActiveMembers: "当前状态成员",
      catalogVersion: "目录版本",
      catalogLastSuccess: "上次成功同步",
      activeVocabulary: "当前 ASR 热词列表",
      vocabularyUpdatedAt: "热词更新时间",
      vocabularyLastBuildWarning: "最近一次 ASR 热词更新有警告",
      vocabularyAutoRebuild: "成员或术语变化后，单 Worker 会自动为当前兼容 ASR 模型构建并启用预编译热词列表。",
      officialMemberCatalog: "官方成员目录",
      syncNow: "立即同步",
      addMemberAlias: "添加成员昵称",
      canonicalMember: "规范成员",
      selectMember: "选择成员",
      aliasLabel: "昵称或别名",
      addAlias: "添加别名",
      addDomainTerm: "添加团内术语",
      canonicalText: "规范写法",
      termType: "术语类型",
      descriptionZh: "中文解释",
      descriptionEn: "英文解释（可选）",
      addTerm: "添加术语",
      addTermAlias: "添加术语别名",
      canonicalTerm: "规范术语",
      selectTerm: "选择术语",
      managedGlossary: "管理员词库",
      managedAliases: "成员与术语别名",
      aliasTarget: "关联规范词",
      state: "状态",
      actions: "操作",
      activeState: "启用",
      inactiveState: "停用",
      memberType: "成员",
      activate: "启用",
      deactivate: "停用",
      noManagedTerms: "还没有管理员术语。",
      noManagedAliases: "还没有成员或术语别名。",
      browseOfficialMembers: "查看官方成员资料",
      group: "团体",
      team: "队伍",
      catalogNotSynced: "官方成员目录尚未同步。",
      termTypeCpName: "CP 名",
      termTypeTeamAbbreviation: "队伍简称",
      termTypeStage: "公演",
      termTypeSong: "歌曲",
      termTypeUnit: "Unit 曲",
      termTypeEvent: "活动",
      termTypeFandom: "饭圈术语",
      termTypeOther: "其他"
    },
    en: {
      brandSubtitle: "Public replay captions and summaries",
      siteTitle: "Pocket48 Replay Summarizer",
      loginDocumentTitle: "Log in · Pocket48 Replay Summarizer",
      requestFailedTitle: "Request failed",
      logout: "Log out",
      glossaryNav: "Glossary",
      switchLanguage: "Switch to Chinese",
      historyNavigation: "Page navigation",
      goBack: "Back",
      goForward: "Forward",
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
      memberFilter: "Filter by member",
      allMembers: "All members",
      applyFilter: "Filter",
      clearFilter: "Clear",
      liveTime: "Live time",
      waitingReplay: "Waiting to resolve replay",
      unknownMember: "Unknown member",
      noPublicResults: "No public results yet",
      resultsAppearHere: "Completed livestream summaries will appear here.",
      noMemberResults: "No visible replays for this member",
      tryAnotherMember: "Choose another member or clear the filter.",
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
      retryClipFailed: "Could not retry the clip",
      clipFailed: "Video clipping failed",
      readClipFailed: "Could not read clip status",
      starting: "Starting…",
      startClipFailed: "Could not start video clipping",
      clipEditorTitle: "Adjust clip",
      closeClipEditor: "Close clip editor",
      clipTimeRange: "Clip time range",
      clipTimeline: "Precision timeline",
      clipTimelineHint: "Move the red line to preview. Clicking leaves a marker and seeks the player; split there or snap a nearby boundary to it.",
      timelineLyricPreview: "Timeline caption preview",
      timelineLyricDisabled: "Captions are off",
      timelineLyricUnavailable: "No caption near this time",
      clipZoomOut: "Zoom timeline out",
      clipZoomIn: "Zoom timeline in",
      clipKeyboardHint: "Focus a boundary and press ←/→ for 0.1 seconds, hold Shift for 1 second, or press Space to play and pause.",
      manualCutEditing: "Manual cut removal",
      manualCutEditingHint: "Click the timeline to mark and seek the player, then split and remove unwanted segments. Export joins kept segments in order.",
      splitAtMarker: "Split at marker",
      deleteSelectedSegment: "Delete selected segment",
      restoreSelectedSegment: "Restore selected segment",
      clipSegments: "Output track",
      clipOutputTrack: "Output track",
      clipOutputTrackHint: "Only final kept segments appear here. Select a striped region above to restore a removed segment.",
      clipCutSummary: "Output {duration} · {kept} segments",
      clipKeptDuration: "Output {duration} · {count} segments",
      clipExportDurationSummary: "Output {duration} · {count} segments",
      clipSegmentKept: "Kept",
      clipSegmentDeleted: "Deleted",
      keptSegmentLabel: "Segment {index}, kept, {start} to {end}",
      deletedSegmentLabel: "Segment {index}, deleted, {start} to {end}",
      clipSplitUnavailable: "Place the marker inside a kept segment, at least 0.1 seconds from either edge.",
      clipSplitComplete: "Split at {time}. Select the segment you want to remove.",
      clipSelectSegmentFirst: "Select a segment first.",
      clipMustKeepOneSegment: "At least one segment must remain.",
      clipSegmentDeletedNotice: "Segment deleted. Preview and export will skip it.",
      clipSegmentRestoredNotice: "Segment restored.",
      clipDeletedPreview: "This segment will not appear in the exported video",
      clipStartRange: "Clip start time",
      clipEndRange: "Clip end time",
      playSelection: "Play selection",
      clipOutputLayout: "Export canvas",
      clipPortraitLayout: "Portrait 9:16",
      clipPortraitLayoutHint: "Keep the original video and caption style",
      clipLandscapeLayout: "Landscape 16:9",
      clipLandscapeLayoutHint: "Warm canvas · centered video · side panels",
      clipLandscapeStyleNote: "Landscape uses a fixed warm-white canvas, red captions, and muted rose danmaku; caption size and font remain adjustable.",
      landscapeFixedPalette: "Fixed landscape palette",
      snapToSpeech: "Snap to speech and silence boundaries",
      resetAiRange: "Reset to AI range",
      burnedSubtitles: "Burned-in captions",
      burnDanmaku: "Burn in danmaku",
      subtitleStyle: "Caption style",
      subtitleStyleHint: "The live preview matches the final export",
      vibrantCalmTheme: "Vibrant Calm",
      customSubtitleTheme: "Custom",
      subtitleFontSize: "Caption size",
      subtitleFontFamily: "Landscape caption font",
      subtitleFontWenkai: "LXGW WenKai",
      subtitleFontSerif: "Noto Serif CJK SC",
      subtitleFontSans: "Noto Sans CJK SC",
      subtitleTextColor: "Text color",
      subtitleBackgroundColor: "Caption background",
      subtitleContrastGood: "Contrast {ratio}:1 · suitable for large captions",
      subtitleContrastLow: "Contrast {ratio}:1 · choose a clearer color pair",
      subtitleContrastRequired: "Text and background need at least 3:1 contrast.",
      aiCoverTitle: "AI-generated cover",
      aiCoverHint: "Generate 16:9 and 4:3 covers from a marked frame. Seedream draws the title onto the image, and the prompt is editable.",
      aiCoverLoginRequired: "Log in to view covers generated by an administrator.",
      aiCoverAdminOnly: "This feature is in administrator preview. You can still view generated results.",
      aiCoverNotConfigured: "Seedream has not been configured by an administrator.",
      aiCoverReadyHint: "MARK a frame on the detailed timeline before generating.",
      aiCoverMarkedFrame: "MARK frame",
      aiCoverMarkHint: "The red marker supplies the person and scene reference.",
      aiCoverMainText: "Cover text",
      aiCoverPrompt: "Image prompt",
      aiCoverPromptHint: "{title} in the prompt is replaced by the cover text above and {ratio} by 16:9 or 4:3. You may delete {title}; the text is then appended at the end.",
      aiCoverPromptReset: "Restore default prompt",
      aiCoverGenerate: "Generate both sizes",
      aiCoverRegenerate: "Regenerate with current text and prompt",
      aiCoverRetry: "Retry failed generation",
      aiCoverUseForVideo: "Use as video cover",
      aiCoverRemoveFromVideo: "Remove cover from video",
      aiCoverLandscapeVideoOnly: "Switch to landscape to use in video",
      aiCoverLandscape: "16:9 landscape",
      aiCoverFourThree: "4:3 landscape",
      aiCoverWaiting: "Waiting to generate",
      aiCoverDownloadPng: "Download PNG",
      aiCoverHistory: "Version history",
      aiCoverNoHistory: "No generated covers yet.",
      aiCoverHistoryItem: "Version {number} · {style} · {time} · {status}",
      aiCoverFrameNote: "The 16:9 cover can replace frame zero in landscape video; 4:3 is download-only. Neither adds duration or delays audio.",
      aiCoverQueued: "Queued for Seedream generation.",
      aiCoverRunning: "Generating both ratios and rendering exact text…",
      aiCoverCompleted: "Both cover sizes are ready. Template and text edits do not call Seedream again.",
      aiCoverFailed: "AI cover generation failed. Retry or create another version.",
      aiCoverModerationRejected: "Seedream moderation rejected this image. Choose another MARK frame.",
      aiCoverLandscapeAlt: "AI-generated 16:9 landscape cover",
      aiCoverFourThreeAlt: "AI-generated 4:3 landscape cover",
      aiCoverLoadFailed: "AI cover history could not be loaded.",
      aiCoverRequestFailed: "The AI cover request failed.",
      aiCoverMarkRequired: "MARK a frame inside a kept segment first.",
      aiCoverTextRequired: "Enter cover text.",
      remove: "Remove",
      cancel: "Cancel",
      createClipExport: "Create clip",
      loginToCreateClip: "Log in to create",
      clipPreviewOnly: "Preview only",
      clipGuestPreviewHint: "Guests can try every editing control. Log in to generate the video.",
      clipPreviewMaintenanceHint: "You can preview the editing controls, but clip generation is under maintenance.",
      noSubtitles: "No captions",
      chineseSubtitles: "Chinese captions",
      englishSubtitles: "English captions",
      bilingualSubtitles: "Bilingual captions",
      clipStatus_running: "Generating in the background",
      clipStatus_completed: "Clip is ready",
      clipStatus_failed: "Clip generation failed",
      clipOutsideWindow: "The selection must stay within the timeline item's 10-minute context window.",
      clipInvalidRange: "The end time must be later than the start time.",
      clipNoKeptSegments: "At least one video segment must remain.",
      clipTooManyKeptSegments: "You can keep at most {count} segments.",
      clipDurationTooLong: "The clip cannot be longer than {minutes} minutes.",
      clipEnglishUnavailable: "Complete English captions are not ready for English or bilingual export.",
      clipSnapFailed: "Silence analysis failed; the sentence boundary was retained.",
      clipPreviewUnavailable: "This browser cannot preview the HLS replay.",
      clipPreviewFailed: "The selected clip preview failed to load. Try again.",
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
      loadDanmakuFailed: "Could not load danmaku",
      glossaryDocumentTitle: "Member and terminology glossary · Pocket48 Replay Summarizer",
      glossaryAdminTitle: "Member and terminology glossary",
      glossaryAdminLede: "Official member fields are synchronized as read-only data. Administrators can add nicknames, CP names, songs, stages, and fandom terminology. The glossary applies only to newly created jobs.",
      glossarySaved: "Glossary changes saved.",
      catalogLastSyncFailed: "The latest official catalog sync failed",
      catalogMembers: "Catalog members",
      catalogActiveMembers: "Current-status members",
      catalogVersion: "Catalog version",
      catalogLastSuccess: "Last successful sync",
      activeVocabulary: "Active ASR vocabulary",
      vocabularyUpdatedAt: "Vocabulary updated",
      vocabularyLastBuildWarning: "The latest ASR vocabulary update has a warning",
      vocabularyAutoRebuild: "After members or terms change, the single worker automatically builds and activates a precompiled vocabulary for the configured compatible ASR model.",
      officialMemberCatalog: "Official member catalog",
      syncNow: "Sync now",
      addMemberAlias: "Add member nickname",
      canonicalMember: "Canonical member",
      selectMember: "Select a member",
      aliasLabel: "Nickname or alias",
      addAlias: "Add alias",
      addDomainTerm: "Add fandom terminology",
      canonicalText: "Canonical text",
      termType: "Term type",
      descriptionZh: "Chinese explanation",
      descriptionEn: "English explanation (optional)",
      addTerm: "Add term",
      addTermAlias: "Add term alias",
      canonicalTerm: "Canonical term",
      selectTerm: "Select a term",
      managedGlossary: "Administrator glossary",
      managedAliases: "Member and term aliases",
      aliasTarget: "Canonical target",
      state: "State",
      actions: "Actions",
      activeState: "Active",
      inactiveState: "Inactive",
      memberType: "Member",
      activate: "Activate",
      deactivate: "Deactivate",
      noManagedTerms: "No administrator terms yet.",
      noManagedAliases: "No member or term aliases yet.",
      browseOfficialMembers: "Browse official member data",
      group: "Group",
      team: "Team",
      catalogNotSynced: "The official member catalog has not been synchronized.",
      termTypeCpName: "CP name",
      termTypeTeamAbbreviation: "Team abbreviation",
      termTypeStage: "Stage",
      termTypeSong: "Song",
      termTypeUnit: "Unit song",
      termTypeEvent: "Event",
      termTypeFandom: "Fandom term",
      termTypeOther: "Other"
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
    "服务正在发布新版本，请稍后再生成封面": "A new release is being deployed. Try generating the cover again shortly.",
    "AI 封面服务未启动": "The AI cover service is unavailable",
    "管理员尚未配置 Seedream，AI 封面暂不可用": "Seedream has not been configured by an administrator",
    "AI 封面不存在": "AI cover not found",
    "AI 封面素材不存在": "AI cover asset not found",
    "AI 封面素材尚未生成完成": "The AI cover asset is not ready yet",
    "仅管理员可以执行此操作": "Only administrators can perform this action",
    "仅管理员可以把 AI 封面用于视频剪辑": "Only administrators can use an AI cover for video export",
    "视频片段尚未生成完成": "The video clip is not ready yet",
    "视频片段不存在": "Video clip not found",
    "本地视频片段不存在，请重新剪辑": "The local clip is missing. Retry the export.",
    "剪辑范围超出当前时间线条目的可编辑窗口": "The clip range is outside this timeline item's editable window.",
    "剪辑边界超出当前时间线条目的可编辑窗口": "The clip boundary is outside this timeline item's editable window.",
    "剪辑边界超出回放时长": "The clip boundary is outside the replay duration.",
    "回放时长尚未准备好": "The replay duration is not ready yet.",
    "所选范围没有可渲染的字幕": "The selected range has no captions to render.",
    "所选范围的英文字幕尚未完整生成": "English captions are incomplete for the selected range.",
    "所选范围没有可渲染的弹幕": "The selected range has no danmaku to render.",
    "当前 FFmpeg 不支持 ASS 字幕滤镜，无法烧录字幕或弹幕": "This FFmpeg build does not support the ASS filter required for captions or danmaku.",
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
    const clipLengthMatch = message.match(/^单个视频片段最长 ([\d.]+) 分钟$/);
    if (clipLengthMatch) {
      return `A video clip can be at most ${clipLengthMatch[1]} minutes long`;
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
    } else if (window.location.pathname === "/admin/glossary") {
      document.title = t("glossaryDocumentTitle");
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
