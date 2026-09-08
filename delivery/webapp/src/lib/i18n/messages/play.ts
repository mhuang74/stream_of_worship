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
    "controller.enterFullscreen": "Enter fullscreen",
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
      "Tap the screen to show controls. Open the lyric list and tap a line to jump to that moment. Fullscreen video uses the iOS system player — lyrics and playback controls are only available outside fullscreen (tap Done to exit).",
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
    "play.notFound": "找不到這個敬拜歌單",
    "play.loadFailed": "敬拜歌單載入失敗，請再試一次",
    "play.backToSongsets": "返回歌單",

    // Projection page
    "projection.loadingAriaLabel": "投影載入中",
    "projection.errorAuthRequired": "需要先登入",
    "projection.errorLoadSongset": "敬拜歌單載入失敗",
    "projection.errorNoArtifacts": "還沒有渲染成品可播放",
    "projection.errorLoadRenderJob": "渲染工作讀取失敗",
    "projection.errorNoVideo": "這個敬拜歌單還沒有影片",
    "projection.errorGetVideoUrl": "影片網址取得失敗，請再試一次",
    "projection.errorLoadFailed": "投影載入失敗，請重新整理",

    // ProjectionPlayer
    "projection.videoAriaLabel": "投影影片",
    "projection.tvFailed": "電視投放失敗，請檢查連線",

    // PrePlayCard
    "preplay.toastRenderFirst": "請先渲染這個敬拜歌單",
    "preplay.stale.title": "成品已過期",
    "preplay.stale.desc": "上次渲染之後，詩歌內容有變更。",
    "preplay.stale.button": "重新渲染",
    "preplay.failed.title": "渲染失敗",
    "preplay.failed.desc": "上一次渲染沒有成功。",
    "preplay.failed.button": "重試渲染",
    "preplay.unrendered.title": "尚未渲染",
    "preplay.unrendered.desc": "這個敬拜歌單要先渲染才能播放。",
    "preplay.unrendered.button": "立即渲染",
    "preplay.songList": "歌曲清單",
    "preplay.total": "總計",
    "preplay.hourShort": "小時",
    "preplay.minShort": "分",
    "preplay.minLong": "分鐘",
    "preplay.unknownSong": "未知歌曲",
    "preplay.unknownArtist": "未知演出者",
    "preplay.startWorship": "開始敬拜",
    "preplay.starting": "開始中…",
    "preplay.share": "分享",
    "preplay.renderToEnable": "渲染這個敬拜歌單後就能播放",
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
    "lyrics.openAriaLabel": "開啟歌詞清單",
    "lyrics.closeAriaLabel": "關閉歌詞清單",
    "lyrics.swipeDownToClose": "向下滑動關閉",
    "lyrics.tapToClose": "輕觸關閉",
    "lyrics.lyrics": "歌詞",

    // ControllerPlayer
    "controller.backAriaLabel": "返回",
    "controller.reenterFullscreen": "重新進入全螢幕",
    "controller.enterFullscreen": "進入全螢幕",
    "controller.connectedTo": "已連線至",
    "controller.tv": "電視",
    "controller.closeTvView": "關閉電視畫面",
    "controller.buffering": "電視載入中…",
    "controller.bufferingActionable": "電視還在載入，請檢查 Wi-Fi 訊號和 MP4 連線，或重新投放。",
    "controller.castUnavailable": "不支援投放",
    "controller.sendToTV": "投放到電視",
    "controller.airplayFallback": "Apple TV 請改用 AirPlay 投放（iOS 版 App 開發中）",
    "controller.screenStaysOn": "保持螢幕恆亮",
    "controller.resumeStale": "電視回報的進度可能已過時，點一下恢復到",
    "controller.tapToResume": "點一下恢復到",
    "controller.iosTitle": "iOS 播放提示",
    "controller.iosDesc": "點一下螢幕就會出現控制列；打開歌詞清單，點一下想唱的那句歌詞，就會跳到那個時間點。小提醒：全螢幕會改用 iOS 系統播放器，歌詞清單和播放控制要離開全螢幕才看得到（按「完成」即可離開）。",
    "controller.dismissInfo": "關閉資訊",
    "controller.keyboardShortcuts": "鍵盤快速鍵",
    "controller.kbSpacePlayPause": "播放/暫停",
    "controller.kbSeek10s": "快轉 10 秒",
    "controller.kbPrevSong": "上一首",
    "controller.kbNextSong": "下一首",
    "controller.diagTitle": "不支援投放",
    "controller.diagDesc": "連不上 Chromecast，請檢查以下幾點：",
    "controller.diag.1": "要在 Android Chrome 上透過 HTTPS 開啟（Cast Web Sender SDK 的限制）。",
    "controller.diag.2": "手機和電視要連同一個 Wi-Fi / VLAN（訪客網路和熱點會擋裝置搜尋）。",
    "controller.diag.3": "電視接收端要開著電源，開發/測試裝置也要先在 Google Cast SDK 開發者控制台加入白名單。",
    "controller.diag.4": "改用筆電瀏覽器在同一個網路開 MP4 網址，確認 R2 連得上、也支援拖曳播放。",
    "controller.toastPlaybackFailed": "播放啟動失敗",
    "controller.mediaAlbum": "敬拜歌單",
  },
});
