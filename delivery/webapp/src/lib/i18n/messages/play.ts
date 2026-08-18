import { bundle } from "../messages";

// Play / projection UI chrome (issue #143).
// Translations are Traditional Chinese (繁體中文). Song metadata (titles,
// composers, lyricists, albums, musical keys), lyrics, user-entered content
// (songset names, descriptions), and the brand "Stream of Worship" remain
// verbatim in both locales.

export const playBundle = bundle({
  en: {
    // Play page (app/songsets/[id]/play/page.tsx)
    "play.title": "Play",
    "play.backAriaLabel": "Go back",
    "play.notFound": "Songset not found",
    "play.loadFailed": "Failed to load songset",
    "play.backToSongsets": "Back to songsets",

    // Projection page (app/songsets/[id]/play/projection/page.tsx)
    "projection.loadingAriaLabel": "Loading projection",
    "projection.errorAuthRequired": "Authentication required",
    "projection.errorLoadSongset": "Failed to load songset",
    "projection.errorNoArtifacts": "No render artifacts available",
    "projection.errorLoadRenderJob": "Failed to load render job",
    "projection.errorNoVideo": "No video available for this songset",
    "projection.errorGetVideoUrl": "Failed to get video URL",
    "projection.errorLoadFailed": "Failed to load projection",

    // ProjectionPlayer
    "projection.videoAriaLabel": "Projection video",
    "projection.tvFailed": "TV projection failed — check connection",

    // PrePlayCard
    "preplay.toastRenderFirst": "Please render this songset first",
    "preplay.stale.title": "Artifacts out of date",
    "preplay.stale.desc": "Songs have been modified since the last render.",
    "preplay.stale.button": "Re-render",
    "preplay.failed.title": "Render failed",
    "preplay.failed.desc": "The last render attempt failed.",
    "preplay.failed.button": "Retry render",
    "preplay.unrendered.title": "Not rendered yet",
    "preplay.unrendered.desc":
      "This songset needs to be rendered before playback.",
    "preplay.unrendered.button": "Render now",
    "preplay.songList": "Song List",
    "preplay.total": "Total",
    "preplay.hourShort": "h",
    "preplay.minShort": "m",
    "preplay.minLong": "min",
    "preplay.unknownSong": "Unknown Song",
    "preplay.unknownArtist": "Unknown Artist",
    "preplay.startWorship": "Start Worship",
    "preplay.starting": "Starting...",
    "preplay.share": "Share",
    "preplay.renderToEnable": "Render this songset to enable playback",
    "preplay.song": "song",
    "preplay.songs": "songs",

    // PlaybackControls
    "controls.seek": "Seek",
    "controls.prevSong": "Previous song",
    "controls.nextSong": "Next song",
    "controls.play": "Play",
    "controls.pause": "Pause",
    "controls.mute": "Mute",
    "controls.unmute": "Unmute",
    "controls.volume": "Volume",
    "controls.connected": "Connected",

    // LyricJumpList
    "lyrics.openAriaLabel": "Open lyric jump list",
    "lyrics.closeAriaLabel": "Close lyric jump list",
    "lyrics.swipeDownToClose": "Swipe down to close",
    "lyrics.tapToClose": "Tap to close",
    "lyrics.lyrics": "Lyrics",

    // ControllerPlayer
    "controller.backAriaLabel": "Back",
    "controller.reenterFullscreen": "Re-enter fullscreen",
    "controller.connectedTo": "Connected to",
    "controller.tv": "TV",
    "controller.closeTvView": "Close TV view",
    "controller.buffering": "TV is loading…",
    "controller.bufferingActionable":
      "TV is still loading — check Wi-Fi / MP4 reachability / retry Cast.",
    "controller.castUnavailable": "Cast unavailable",
    "controller.sendToTV": "Send to TV",
    "controller.airplayFallback":
      "Use AirPlay to an Apple TV — native iOS app pending",
    "controller.screenStaysOn": "Screen stays on",
    "controller.resumeStale":
      "Resume from TV position may be stale — tap to resume at",
    "controller.tapToResume": "Tap to resume at",
    "controller.iosTitle": "iOS Playback Tips",
    "controller.iosDesc":
      "Tap the screen to show controls. Open the lyric list and tap a line to jump to that moment.",
    "controller.dismissInfo": "Dismiss info",
    "controller.keyboardShortcuts": "Keyboard shortcuts",
    "controller.kbSpacePlayPause": "Play/Pause",
    "controller.kbSeek10s": "Seek 10s",
    "controller.kbPrevSong": "Prev song",
    "controller.kbNextSong": "Next song",
    "controller.diagTitle": "Cast unavailable",
    "controller.diagDesc": "Chromecast couldn't be reached. Check the following:",
    "controller.diag.1": "Use Android Chrome over HTTPS (the Cast Web Sender SDK requires it).",
    "controller.diag.2": "Phone and TV must be on the same Wi-Fi / VLAN (guest and captive-portal networks block discovery).",
    "controller.diag.3": "Receiver must be powered on, and dev/staging devices must be whitelisted in the Google Cast SDK Developer Console.",
    "controller.diag.4": "Try opening the MP4 URL from this network in a laptop browser to confirm R2 reachability and range-seek.",
    "controller.toastPlaybackFailed": "Failed to start playback",
    "controller.mediaAlbum": "Worship Set",
  },
  "zh-Hant": {
    // Play page
    "play.title": "播放",
    "play.backAriaLabel": "返回",
    "play.notFound": "找不到詩歌集",
    "play.loadFailed": "載入詩歌集失敗",
    "play.backToSongsets": "返回詩歌集",

    // Projection page
    "projection.loadingAriaLabel": "投影載入中",
    "projection.errorAuthRequired": "需要登入",
    "projection.errorLoadSongset": "載入詩歌集失敗",
    "projection.errorNoArtifacts": "沒有可用的渲染成品",
    "projection.errorLoadRenderJob": "載入渲染工作失敗",
    "projection.errorNoVideo": "此詩歌集沒有可用的影片",
    "projection.errorGetVideoUrl": "取得影片網址失敗",
    "projection.errorLoadFailed": "投影載入失敗",

    // ProjectionPlayer
    "projection.videoAriaLabel": "投影影片",
    "projection.tvFailed": "電視投影失敗 — 請檢查連線",

    // PrePlayCard
    "preplay.toastRenderFirst": "請先渲染此詩歌集",
    "preplay.stale.title": "成品已過期",
    "preplay.stale.desc": "詩歌自上次渲染後已修改。",
    "preplay.stale.button": "重新渲染",
    "preplay.failed.title": "渲染失敗",
    "preplay.failed.desc": "上次渲染嘗試失敗。",
    "preplay.failed.button": "重試渲染",
    "preplay.unrendered.title": "尚未渲染",
    "preplay.unrendered.desc": "此詩歌集需要先渲染才能播放。",
    "preplay.unrendered.button": "立即渲染",
    "preplay.songList": "詩歌列表",
    "preplay.total": "總計",
    "preplay.hourShort": "小時",
    "preplay.minShort": "分",
    "preplay.minLong": "分鐘",
    "preplay.unknownSong": "未知詩歌",
    "preplay.unknownArtist": "未知藝人",
    "preplay.startWorship": "開始敬拜",
    "preplay.starting": "開始中...",
    "preplay.share": "分享",
    "preplay.renderToEnable": "渲染此詩歌集以啟用播放",
    "preplay.song": "首歌",
    "preplay.songs": "首歌",

    // PlaybackControls
    "controls.seek": "拖曳進度",
    "controls.prevSong": "上一首",
    "controls.nextSong": "下一首",
    "controls.play": "播放",
    "controls.pause": "暫停",
    "controls.mute": "靜音",
    "controls.unmute": "取消靜音",
    "controls.volume": "音量",
    "controls.connected": "已連線",

    // LyricJumpList
    "lyrics.openAriaLabel": "開啟歌詞跳轉列表",
    "lyrics.closeAriaLabel": "關閉歌詞跳轉列表",
    "lyrics.swipeDownToClose": "向下滑動關閉",
    "lyrics.tapToClose": "輕觸關閉",
    "lyrics.lyrics": "歌詞",

    // ControllerPlayer
    "controller.backAriaLabel": "返回",
    "controller.reenterFullscreen": "重新進入全螢幕",
    "controller.connectedTo": "已連線至",
    "controller.tv": "電視",
    "controller.closeTvView": "關閉電視畫面",
    "controller.buffering": "電視載入中…",
    "controller.bufferingActionable": "電視仍在載入 — 請檢查 Wi-Fi / MP4 可達性 / 重試投放。",
    "controller.castUnavailable": "投放功能無法使用",
    "controller.sendToTV": "投射到電視",
    "controller.airplayFallback": "請使用 AirPlay 投射至 Apple TV — 原生 iOS 應用程式開發中",
    "controller.screenStaysOn": "螢幕保持開啟",
    "controller.resumeStale": "從電視位置恢復可能已過時 — 輕觸以恢復至",
    "controller.tapToResume": "輕觸以恢復至",
    "controller.iosTitle": "iOS 播放提示",
    "controller.iosDesc": "輕觸螢幕顯示控制列。開啟歌詞列表並輕觸歌詞行以跳轉到該時間點。",
    "controller.dismissInfo": "關閉資訊",
    "controller.keyboardShortcuts": "鍵盤快速鍵",
    "controller.kbSpacePlayPause": "播放/暫停",
    "controller.kbSeek10s": "快轉 10 秒",
    "controller.kbPrevSong": "上一首",
    "controller.kbNextSong": "下一首",
    "controller.diagTitle": "投放功能無法使用",
    "controller.diagDesc": "無法連線至 Chromecast。請檢查以下項目：",
    "controller.diag.1": "請在 Android Chrome 上透過 HTTPS 使用（Cast Web Sender SDK 需要此環境）。",
    "controller.diag.2": "手機與電視必須在同一 Wi-Fi / VLAN（訪客及熱點網路會封鎖裝置探索）。",
    "controller.diag.3": "接收端必須開啟電源，且開發/預備裝置須在 Google Cast SDK 開發者控制台中列入白名單。",
    "controller.diag.4": "嘗試在此網路以筆電瀏覽器開啟 MP4 網址，確認 R2 可達性與範圍定位。",
    "controller.toastPlaybackFailed": "播放啟動失敗",
    "controller.mediaAlbum": "敬拜詩歌集",
  },
});
