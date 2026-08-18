import { bundle } from "../messages";

// Render namespace: render page, render form, submitted/complete status cards,
// and the render status badge. Songset names, lyrics, song metadata, and the
// brand "Stream of Worship" remain verbatim. Dropdown option labels (template,
// resolution, font size, font family) reuse the shared `settings.option.*`
// keys declared in core.ts so there is a single source of truth.

export const renderBundle = bundle({
  en: {
    // Render page (RenderPageClient)
    "render.heading": "Render",
    "render.back.ariaLabel": "Go back",
    "render.songsetNotFound": "Songset not found",
    "render.backToSongsets": "Back to songsets",

    // Render page toasts
    "render.toast.alreadyInProgress": "A render job is already in progress",
    "render.toast.started": "Render started",
    "render.toast.cancelled": "Render cancelled",
    "render.toast.failedToStart": "Failed to start render",
    "render.toast.failedToCancel": "Failed to cancel",
    "render.toast.failedToCreateJob": "Failed to create render job",
    "render.toast.failedToCancelJob": "Failed to cancel render job",
    "render.toast.config.audio": "audio",
    "render.toast.config.video": "video",

    // RenderForm — output options
    "render.output.title": "Output Options",
    "render.output.description": "Choose what to render",
    "render.output.audioLabel": "Audio (MP3)",
    "render.output.audioDescription": "Mixed audio with transitions",
    "render.output.videoLabel": "Video (MP4)",
    "render.output.videoDescription": "Lyrics video with audio",

    // RenderForm — video settings
    "render.video.title": "Video Settings",
    "render.video.description": "Customize the lyrics video",
    "render.video.template": "Template",
    "render.video.resolution": "Resolution",
    "render.video.fontSize": "Font Size",
    "render.video.fontFamily": "Font Family",

    // RenderForm — title card
    "render.titleCard.title": "Title Card",
    "render.titleCard.description": "Add an opening title card",
    "render.titleCard.include": "Include title card",
    "render.titleCard.duration": "Duration",
    "render.titleCard.duration.5": "5 seconds",
    "render.titleCard.duration.10": "10 seconds",
    "render.titleCard.duration.15": "15 seconds",
    "render.titleCard.duration.20": "20 seconds",
    "render.titleCard.duration.25": "25 seconds",
    "render.titleCard.duration.30": "30 seconds",
    "render.titleCard.customText": "Custom title card text",
    "render.titleCard.customTextHint":
      "One line per entry. Leave empty to use songset name and song titles.",
    "render.titleCard.defaultLines": "Default title card lines:",
    "render.titleCard.worshipSet": "Worship Set",
    "render.titleCard.placeholder": "Sunday Morning Worship",

    // RenderForm — offline availability
    "render.offline.title": "Offline Availability",
    "render.offline.description": "Cache for offline playback",
    "render.offline.makeAvailable": "Make available offline",
    "render.offline.requiresIOS": "Requires iOS 17.4 or later",
    "render.offline.cacheHint": "Cache rendered files for offline playback",

    // RenderForm — marked lines warning
    "render.markedLines.plural": "marked lines need attention",
    "render.markedLines.singular": "marked line need attention",
    "render.markedLines.hint":
      "Some lyrics have been marked for review. Please verify before rendering.",
    "render.markedLines.review": "Review",

    // RenderForm — action buttons
    "render.action.cancel": "Cancel",
    "render.action.start": "Start Render",
    "render.action.starting": "Starting...",

    // RenderForm — previous render notice
    "render.previousRender.notice": "Previously rendered at",

    // RenderForm — confirmation dialog
    "render.confirm.title": "Start New Render?",
    "render.confirm.description":
      "A previous render exists for this songset. Compare the parameters below before starting a new render.",
    "render.confirm.parameter": "Parameter",
    "render.confirm.previous": "Previous Render",
    "render.confirm.current": "Current Request",
    "render.confirm.cancel": "Cancel",

    // RenderForm — comparison table row labels
    "render.compare.font": "Font",
    "render.compare.fontSize": "Font Size",
    "render.compare.background": "Background",
    "render.compare.resolution": "Resolution",
    "render.compare.titleCard": "Title Card",
    "render.compare.songs": "Songs",
    "render.compare.songsetDuration": "Songset Duration",
    "render.compare.totalDuration": "Total Duration",
    "render.compare.titleCardOn": "On",
    "render.compare.titleCardOff": "Off",
    "render.compare.estimatedPrefix": "~",

    // RenderSubmitted
    "render.submitted.title": "Render Started",
    "render.submitted.estimatedTime": "Estimated time",
    "render.submitted.estimatedMinutes": "minutes",
    "render.submitted.estimatedPrefix": "~",
    "render.submitted.leavePage":
      "You can leave this page. Check your songset later for the result.",
    "render.submitted.submittedAt": "Submitted at",
    "render.submitted.cancel": "Cancel Render",

    // RenderComplete
    "render.complete.title": "Render Complete!",
    "render.complete.description": "is ready for playback",
    "render.complete.totalTime": "Total time:",
    "render.complete.downloadFiles": "Download Files",
    "render.complete.downloadAudio": "Download Audio (MP3)",
    "render.complete.downloadVideo": "Download Video (MP4)",
    "render.complete.downloadChapters": "Download Chapters (JSON)",
    "render.complete.share": "Share Songset",
    "render.complete.done": "Done",
    "render.complete.shareText": "Check out \"",
    "render.complete.shareTextSuffix": "\" on Stream of Worship",

    // RenderComplete — downloads toasts
    "render.download.preparing": "Preparing download...",
    "render.download.started": "Download started",
    "render.download.failed": "Download failed",

    // RenderStatusBadge
    "render.badge.unrendered": "Not rendered",
    "render.badge.rendering": "Rendering",
    "render.badge.fresh": "Rendered",
    "render.badge.stale": "Needs re-render",
    "render.badge.failed": "Render failed",
  },
  "zh-Hant": {
    // Render page (RenderPageClient)
    "render.heading": "渲染",
    "render.back.ariaLabel": "返回",
    "render.songsetNotFound": "找不到詩歌集",
    "render.backToSongsets": "返回詩歌集",

    // Render page toasts
    "render.toast.alreadyInProgress": "已有渲染作業進行中",
    "render.toast.started": "渲染已開始",
    "render.toast.cancelled": "渲染已取消",
    "render.toast.failedToStart": "無法開始渲染",
    "render.toast.failedToCancel": "無法取消",
    "render.toast.failedToCreateJob": "無法建立渲染作業",
    "render.toast.failedToCancelJob": "無法取消渲染作業",
    "render.toast.config.audio": "音訊",
    "render.toast.config.video": "影片",

    // RenderForm — output options
    "render.output.title": "輸出選項",
    "render.output.description": "選擇要渲染的內容",
    "render.output.audioLabel": "音訊（MP3）",
    "render.output.audioDescription": "含轉場的混音音訊",
    "render.output.videoLabel": "影片（MP4）",
    "render.output.videoDescription": "含音訊的歌詞影片",

    // RenderForm — video settings
    "render.video.title": "影片設定",
    "render.video.description": "自訂歌詞影片",
    "render.video.template": "範本",
    "render.video.resolution": "解析度",
    "render.video.fontSize": "字型大小",
    "render.video.fontFamily": "字型",

    // RenderForm — title card
    "render.titleCard.title": "標題卡",
    "render.titleCard.description": "加入開場標題卡",
    "render.titleCard.include": "加入標題卡",
    "render.titleCard.duration": "持續時間",
    "render.titleCard.duration.5": "5 秒",
    "render.titleCard.duration.10": "10 秒",
    "render.titleCard.duration.15": "15 秒",
    "render.titleCard.duration.20": "20 秒",
    "render.titleCard.duration.25": "25 秒",
    "render.titleCard.duration.30": "30 秒",
    "render.titleCard.customText": "自訂標題卡文字",
    "render.titleCard.customTextHint": "每行一個項目。留空則使用詩歌集名稱與詩歌標題。",
    "render.titleCard.defaultLines": "預設標題卡內容：",
    "render.titleCard.worshipSet": "敬拜詩歌集",
    "render.titleCard.placeholder": "主日敬拜",

    // RenderForm — offline availability
    "render.offline.title": "離線可用性",
    "render.offline.description": "快取以供離線播放",
    "render.offline.makeAvailable": "設為離線可用",
    "render.offline.requiresIOS": "需要 iOS 17.4 或更新版本",
    "render.offline.cacheHint": "快取已渲染的檔案以供離線播放",

    // RenderForm — marked lines warning
    "render.markedLines.plural": "個標記行需要處理",
    "render.markedLines.singular": "個標記行需要處理",
    "render.markedLines.hint": "部分歌詞已標記待檢閱。渲染前請先確認。",
    "render.markedLines.review": "檢閱",

    // RenderForm — action buttons
    "render.action.cancel": "取消",
    "render.action.start": "開始渲染",
    "render.action.starting": "啟動中...",

    // RenderForm — previous render notice
    "render.previousRender.notice": "先前的渲染時間為",

    // RenderForm — confirmation dialog
    "render.confirm.title": "開始新的渲染？",
    "render.confirm.description": "此詩歌集已有先前的渲染。開始新的渲染前，請比較下方的參數。",
    "render.confirm.parameter": "參數",
    "render.confirm.previous": "先前的渲染",
    "render.confirm.current": "目前請求",
    "render.confirm.cancel": "取消",

    // RenderForm — comparison table row labels
    "render.compare.font": "字型",
    "render.compare.fontSize": "字型大小",
    "render.compare.background": "背景",
    "render.compare.resolution": "解析度",
    "render.compare.titleCard": "標題卡",
    "render.compare.songs": "詩歌",
    "render.compare.songsetDuration": "詩歌集長度",
    "render.compare.totalDuration": "總長度",
    "render.compare.titleCardOn": "開啟",
    "render.compare.titleCardOff": "關閉",
    "render.compare.estimatedPrefix": "約 ",

    // RenderSubmitted
    "render.submitted.title": "渲染已開始",
    "render.submitted.estimatedTime": "預估時間",
    "render.submitted.estimatedMinutes": "分鐘",
    "render.submitted.estimatedPrefix": "約 ",
    "render.submitted.leavePage": "您可以離開此頁面。稍後再回來查看詩歌集的結果。",
    "render.submitted.submittedAt": "提交時間",
    "render.submitted.cancel": "取消渲染",

    // RenderComplete
    "render.complete.title": "渲染完成！",
    "render.complete.description": "已可播放",
    "render.complete.totalTime": "總時間：",
    "render.complete.downloadFiles": "下載檔案",
    "render.complete.downloadAudio": "下載音訊（MP3）",
    "render.complete.downloadVideo": "下載影片（MP4）",
    "render.complete.downloadChapters": "下載章節（JSON）",
    "render.complete.share": "分享詩歌集",
    "render.complete.done": "完成",
    "render.complete.shareText": "來看看「",
    "render.complete.shareTextSuffix": "」在 Stream of Worship 上",

    // RenderComplete — downloads toasts
    "render.download.preparing": "正在準備下載...",
    "render.download.started": "下載已開始",
    "render.download.failed": "下載失敗",

    // RenderStatusBadge
    "render.badge.unrendered": "未渲染",
    "render.badge.rendering": "渲染中",
    "render.badge.fresh": "已渲染",
    "render.badge.stale": "需要重新渲染",
    "render.badge.failed": "渲染失敗",
  },
});
