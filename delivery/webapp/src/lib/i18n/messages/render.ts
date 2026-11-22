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
    "render.toast.started": "Render started — track or cancel it from the songset menu",
    "render.toast.cancelled": "Render cancelled",
    "render.toast.failedToStart": "Failed to start render",
    "render.toast.failedToCancel": "Failed to cancel",
    "render.toast.failedToCreateJob": "Failed to create render job",
    "render.toast.failedToCancelJob": "Failed to cancel render job",
    "render.toast.completed": "Render completed",
    "render.toast.failed": "Render failed",
    "render.toast.authExpired":
      "Your session expired — sign in again to see the render result.",
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
    "render.titleCard.customText": "Custom title card text",
    "render.titleCard.customTextHint":
      "One line per entry. Leave empty to use songset name and song titles.",
    "render.titleCard.defaultLines": "Default title card lines:",
    "render.titleCard.worshipSet": "Worship Set",
    "render.titleCard.placeholder": "Sunday Morning Worship",

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

    // Render progress line (SongsetEditor metadata row)
    "render.status.estimatedPrefix": "~",
    "render.status.estimatedMinutes": "minutes",

    // RenderForm — AI-transition render preflight (spec §5.5)
    "render.gluePreflight.heading": "AI transitions need attention",
    "render.gluePreflight.body":
      "Finish, retry, or turn off these AI transitions before rendering.",
    "render.gluePreflight.retryFree": "Retry (free)",
    "render.gluePreflight.retryPaid": "Try another (paid)",
    "render.gluePreflight.openEditor": "Open editor",
    "render.gluePreflight.disable": "Disable AI transitions and use ordinary transitions",
    "render.gluePreflight.disableConfirmTitle": "Disable this AI transition?",
    "render.gluePreflight.disableConfirmBody":
      "This Songset will use the ordinary transition instead.",
    "render.gluePreflight.disableConfirmAction":
      "Disable and use ordinary transitions",
    "render.gluePreflight.disableConfirmCancel": "Keep AI transition",
    "render.gluePreflight.phaseLabel": "Status:",
    "render.gluePreflight.jobLabel": "Job:",
    "render.gluePreflight.retryFailed":
      "Could not restart this AI transition. Try again.",
    "render.gluePreflight.disableFailed":
      "Could not disable this AI transition. Try again.",
    "render.gluePreflight.reason.notReady": "This AI transition is not ready yet.",
    "render.gluePreflight.reason.activeJob":
      "An AI transition is still being created. Check back shortly.",
    "render.gluePreflight.reason.staleSnapshot":
      "The songs changed since this AI transition was created.",
    "render.gluePreflight.reason.quotaExhausted":
      "No paid AI transitions are left today.",
    "render.gluePreflight.reason.tempoUnavailable":
      "The next Song has no detected tempo yet.",
    "render.gluePreflight.reason.notAnalyzed":
      "Add section and beat analysis to this Song first.",
    "render.gluePreflight.reason.arrangementInvalid":
      "Fix the arrangement before making this AI transition.",
    "render.gluePreflight.reason.unsavedDraft":
      "Save your arrangement changes first.",
    "render.gluePreflight.reason.workerFailed":
      "The last AI transition attempt did not finish.",
    "render.gluePreflight.reason.retryPaid":
      "The last attempt could not be completed. Try another (paid).",
    "render.gluePreflight.reason.retryFree":
      "The last attempt can be finished for free. Retry.",
    "render.gluePreflight.reason.providerUnknown":
      "The AI transition service did not respond. Try another (paid) or open the editor.",
    "render.gluePreflight.reason.entitlement":
      "This account cannot use AI transitions.",
    "render.gluePreflight.reason.plan": "This plan does not include AI transitions.",
    "render.gluePreflight.reason.copyright":
      "This recording cannot be used for an AI transition.",
    "render.gluePreflight.reason.tooShort":
      "This junction is too short for an AI transition.",
    "render.gluePreflight.reason.audioUnavailable":
      "The AI transition audio is unavailable. Try again.",
    "render.gluePreflight.reason.executionDeadline":
      "The last AI transition attempt ran out of time.",
    "render.gluePreflight.reason.dispatchFailed":
      "The AI transition could not be started. Retry (free).",
    "render.gluePreflight.reason.inputsChanged":
      "The songs changed. Open the editor to refresh this AI transition.",
    "render.gluePreflight.reason.notConfigured":
      "AI transitions are not available right now.",
    "render.gluePreflight.reason.unknown":
      "AI transitions are not available right now.",

    // Render admission — a saved Song arrangement no longer matches the
    // Recording's analysis (render-jobs 409 / failed render tooltip). Mirrors
    // arrangements.ts ARRANGEMENT_UNAVAILABLE_OR_STALE_MESSAGE.
    "render.error.arrangementRepairOrReset":
      "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement",
  },
  "zh-Hant": {
    // Render page (RenderPageClient)
    "render.heading": "渲染",
    "render.back.ariaLabel": "返回",
    "render.songsetNotFound": "找不到這個敬拜歌單",
    "render.backToSongsets": "回到敬拜歌單",

    // Render page toasts
    "render.toast.alreadyInProgress": "已經有渲染在進行了",
    "render.toast.started": "已開始渲染——可在歌曲組選單中追蹤或取消",
    "render.toast.cancelled": "已取消渲染",
    "render.toast.failedToStart": "渲染啟動失敗，請再試一次",
    "render.toast.failedToCancel": "取消失敗",
    "render.toast.failedToCreateJob": "渲染工作建立失敗，請稍後再試",
    "render.toast.failedToCancelJob": "取消渲染工作失敗",
    "render.toast.completed": "已完成渲染",
    "render.toast.failed": "渲染失敗",
    "render.toast.authExpired": "你的登入已經過期——請重新登入查看渲染結果。",
    "render.toast.config.audio": "音訊",
    "render.toast.config.video": "影片",

    // RenderForm — output options
    "render.output.title": "輸出選項",
    "render.output.description": "選擇要渲染的項目",
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
    "render.titleCard.customText": "自訂標題卡文字",
    "render.titleCard.customTextHint": "每行一個項目，留空就用敬拜歌單名稱和詩歌標題。",
    "render.titleCard.defaultLines": "預設的標題卡內容：",
    "render.titleCard.worshipSet": "敬拜歌單",
    "render.titleCard.placeholder": "主日敬拜",

    // RenderForm — marked lines warning
    "render.markedLines.plural": "處標記的歌詞需要確認",
    "render.markedLines.singular": "處標記的歌詞需要確認",
    "render.markedLines.hint": "有部分歌詞被標記待確認，渲染前先檢查一下。",
    "render.markedLines.review": "前往確認",

    // RenderForm — action buttons
    "render.action.cancel": "取消",
    "render.action.start": "開始渲染",
    "render.action.starting": "啟動中…",

    // RenderForm — previous render notice
    "render.previousRender.notice": "上次渲染時間：",
    "render.confirm.title": "要開始新的渲染嗎？",
    "render.confirm.description": "這個敬拜歌單之前已經渲染過了。開始新的渲染之前，先比一下下面的參數。",
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
    "render.compare.songsetDuration": "敬拜歌單長度",
    "render.compare.totalDuration": "總長度",
    "render.compare.titleCardOn": "開啟",
    "render.compare.titleCardOff": "關閉",
    "render.compare.estimatedPrefix": "約 ",

    // RenderComplete
    "render.complete.title": "渲染完成！",
    "render.complete.description": "已經可以播放了",
    "render.complete.totalTime": "總時間：",
    "render.complete.downloadFiles": "下載檔案",
    "render.complete.downloadAudio": "下載音訊（MP3）",
    "render.complete.downloadVideo": "下載影片（MP4）",
    "render.complete.downloadChapters": "下載章節（JSON）",
    "render.complete.share": "分享敬拜歌單",
    "render.complete.done": "完成",
    "render.complete.shareText": "來聽聽看「",
    "render.complete.shareTextSuffix": "」，在 Stream of Worship 上",

    // RenderComplete — downloads toasts
    "render.download.preparing": "準備下載中…",
    "render.download.started": "開始下載了",
    "render.download.failed": "下載失敗，請再試一次",

    // RenderStatusBadge
    "render.badge.unrendered": "還沒渲染",
    "render.badge.rendering": "渲染中",
    "render.badge.fresh": "已渲染",
    "render.badge.stale": "建議重新渲染",
    "render.badge.failed": "渲染失敗",

    // Render progress line (SongsetEditor metadata row)
    "render.status.estimatedPrefix": "約 ",
    "render.status.estimatedMinutes": "分鐘",

    // RenderForm — AI 轉場渲染前檢查（規格 §5.5）
    "render.gluePreflight.heading": "AI 轉場需要處理",
    "render.gluePreflight.body":
      "請先完成、重試或關閉以下 AI 轉場，才能開始渲染。",
    "render.gluePreflight.retryFree": "免費重試",
    "render.gluePreflight.retryPaid": "再試一次（付費）",
    "render.gluePreflight.openEditor": "開啟編輯器",
    "render.gluePreflight.disable": "關閉 AI 轉場，改用一般轉場",
    "render.gluePreflight.disableConfirmTitle": "要關閉這個 AI 轉場嗎？",
    "render.gluePreflight.disableConfirmBody": "這個敬拜歌單會改用一般轉場。",
    "render.gluePreflight.disableConfirmAction": "關閉並改用一般轉場",
    "render.gluePreflight.disableConfirmCancel": "保留 AI 轉場",
    "render.gluePreflight.phaseLabel": "狀態：",
    "render.gluePreflight.jobLabel": "工作：",
    "render.gluePreflight.retryFailed": "無法重新開始這個 AI 轉場，請再試一次。",
    "render.gluePreflight.disableFailed": "無法關閉這個 AI 轉場，請再試一次。",
    "render.gluePreflight.reason.notReady": "這個 AI 轉場尚未完成。",
    "render.gluePreflight.reason.activeJob": "AI 轉場仍在製作中，請稍後再試。",
    "render.gluePreflight.reason.staleSnapshot":
      "詩歌內容已變更，這個 AI 轉場需要重新整理。",
    "render.gluePreflight.reason.quotaExhausted": "今天的付費 AI 轉場額度已用完。",
    "render.gluePreflight.reason.tempoUnavailable": "下一首詩歌尚未偵測到速度。",
    "render.gluePreflight.reason.notAnalyzed": "請先為這首詩歌加入段落與節拍分析。",
    "render.gluePreflight.reason.arrangementInvalid":
      "請先修正編排，再製作 AI 轉場。",
    "render.gluePreflight.reason.unsavedDraft": "請先儲存編排變更。",
    "render.gluePreflight.reason.workerFailed": "上一次 AI 轉場沒有完成。",
    "render.gluePreflight.reason.retryPaid":
      "上一次無法完成，請再試一次（付費）。",
    "render.gluePreflight.reason.retryFree": "上一次可以免費完成，請重試。",
    "render.gluePreflight.reason.providerUnknown":
      "AI 轉場服務沒有回應，請再試一次（付費）或開啟編輯器。",
    "render.gluePreflight.reason.entitlement": "這個帳號無法使用 AI 轉場。",
    "render.gluePreflight.reason.plan": "目前的方案不包含 AI 轉場。",
    "render.gluePreflight.reason.copyright": "這首錄音無法用於 AI 轉場。",
    "render.gluePreflight.reason.tooShort": "這個銜接點太短，無法製作 AI 轉場。",
    "render.gluePreflight.reason.audioUnavailable": "AI 轉場音訊無法取得，請再試一次。",
    "render.gluePreflight.reason.executionDeadline": "上一次 AI 轉場逾時。",
    "render.gluePreflight.reason.dispatchFailed": "無法開始 AI 轉場，請免費重試。",
    "render.gluePreflight.reason.inputsChanged":
      "詩歌內容已變更，請開啟編輯器重新整理這個 AI 轉場。",
    "render.gluePreflight.reason.notConfigured": "目前無法使用 AI 轉場。",
    "render.gluePreflight.reason.unknown": "目前無法使用 AI 轉場。",

    // Render admission — saved arrangement no longer matches analysis
    "render.error.arrangementRepairOrReset":
      "歌曲編排已無法使用或已過期；請修復錄音的歌曲分析，或重設編排",
  },
});
