import { bundle } from "../messages";

// Audio namespace: player bar, lyrics panel, offline indicators/status, and
// semantic search chrome. Song titles, lyrics, and user-entered content remain
// verbatim. Traditional Chinese (繁體中文) in concise worship-app tone.

export const audioBundle = bundle({
  en: {
    // AudioPlayerBar — lyrics error fallback
    "audio.lyrics.unavailable": "Lyrics unavailable",

    // AudioPlayerBar — transport controls (aria-labels)
    "audio.skipBack": "Skip back 10 seconds",
    "audio.skipForward": "Skip forward 10 seconds",
    "audio.pause": "Pause",
    "audio.play": "Play",
    "audio.disableLoop": "Disable loop",
    "audio.enableLoop": "Enable loop",
    "audio.unmute": "Unmute",
    "audio.mute": "Mute",
    "audio.hideLyrics": "Hide lyrics",
    "audio.showLyrics": "Show lyrics",
    "audio.lyricsTitle": "Lyrics (L)",
    "audio.closePlayer": "Close player",

    // AudioPlayerBar — lyrics region aria-label prefix
    "audio.lyricsFor": "Lyrics for",

    // AudioPlayerBar — track type badges
    "audio.trackPreview": "(Preview)",
    "audio.trackLoop": "(Loop)",

    // PlayerLyricsPanel
    "audio.lyrics.loading": "Loading lyrics\u2026",
    "audio.lyrics.noLyrics": "No lyrics available for this recording.",

    // OfflineIndicator
    "audio.offline.message": "You are offline",

    // OfflineStatus — toasts
    "audio.offline.cachingNotAvailable": "Offline caching not available",
    "audio.offline.noArtifacts": "No artifacts available to cache",
    "audio.offline.downloaded": "Downloaded for offline playback",
    "audio.offline.downloadFailed": "Failed to download for offline",

    // OfflineStatus — button / badge
    "audio.offline.downloadForOffline": "Download for offline",
    "audio.offline.ready": "Offline ready",
    "audio.offline.downloading": "Downloading...",

    // OfflineStatus — iOS unsupported
    "audio.offline.updateIos": "Update iOS for offline",
    "audio.offline.iosTooltip": "Offline caching requires iOS 17.4 or later",

    // SemanticSearch — input
    "audio.search.placeholder": "Describe songs by theme or feeling...",
    "audio.search.ariaLabel": "Describe songs to search for",
    "audio.search.helpTip":
      "Tip: describe by theme or feeling \u2014 e.g. \u2018在神寶座前\u2019, \u2018standing before God\u2019s throne\u2019 \u00b7 Press Enter to search",

    // SemanticSearch — button
    "audio.search.searchButton": "Search",
    "audio.search.searching": "Searching...",
    "audio.search.searchSongsByDescription": "Search songs by description",

    // SemanticSearch — loading / empty states
    "audio.search.searchingByMeaning": "Searching by meaning...",
    "audio.search.loadingSongs": "Loading songs...",
    "audio.search.noSongsMatchFilters": "No songs match your filters",
    "audio.search.noMatchingSongs": "No matching songs found",
    "audio.search.tryRemovingFilters": "Try removing some filters to see more results",
    "audio.search.tryDifferentDescription":
      "Try a different description, or songs may not have embeddings yet",

    // SemanticSearch — result count (compose: `${n} ${t("audio.search.songsFoundLabel")}`)
    "audio.search.songsFoundLabel": "songs found",

    // SemanticSearch — similarity badge (compose: `${pct}% ${t("audio.search.matchSuffix")}`)
    "audio.search.matchSuffix": "match",

    // SemanticSearch — why this match
    "audio.search.whyThisMatch": "Why this match?",

    // SemanticSearch — errors / toasts
    "audio.search.semanticUnavailable": "Semantic search unavailable",
    "audio.search.semanticUnavailableSwitch": "Semantic search unavailable, switching to text search",
    "audio.search.searchFailed": "Search failed",
    "audio.search.noAudioForSong": "No audio available for this song",
    "audio.search.unknownArtist": "Unknown Artist",
    "audio.search.failedAudioUrl": "Failed to get audio URL",
    "audio.search.failedLoadPreview": "Failed to load audio preview",

    // SemanticSearch — lyric line label (compose: `${t("audio.search.lyric")} ${i + 1}: ${line}`)
    "audio.search.lyric": "Lyric",
  },
  "zh-Hant": {
    // AudioPlayerBar — lyrics error fallback
    "audio.lyrics.unavailable": "歌詞無法使用",

    // AudioPlayerBar — transport controls (aria-labels)
    "audio.skipBack": "倒轉 10 秒",
    "audio.skipForward": "快轉 10 秒",
    "audio.pause": "暫停",
    "audio.play": "播放",
    "audio.disableLoop": "關閉循環",
    "audio.enableLoop": "開啟循環",
    "audio.unmute": "取消靜音",
    "audio.mute": "靜音",
    "audio.hideLyrics": "隱藏歌詞",
    "audio.showLyrics": "顯示歌詞",
    "audio.lyricsTitle": "歌詞（L）",
    "audio.closePlayer": "關閉播放器",

    // AudioPlayerBar — lyrics region aria-label prefix
    "audio.lyricsFor": "歌詞：",

    // AudioPlayerBar — track type badges
    "audio.trackPreview": "（預覽）",
    "audio.trackLoop": "（循環）",

    // PlayerLyricsPanel
    "audio.lyrics.loading": "載入歌詞中\u2026",
    "audio.lyrics.noLyrics": "此錄音沒有可用的歌詞。",

    // OfflineIndicator
    "audio.offline.message": "您目前離線",

    // OfflineStatus — toasts
    "audio.offline.cachingNotAvailable": "離線快取無法使用",
    "audio.offline.noArtifacts": "沒有可快取的檔案",
    "audio.offline.downloaded": "已下載供離線播放",
    "audio.offline.downloadFailed": "離線下載失敗",

    // OfflineStatus — button / badge
    "audio.offline.downloadForOffline": "下載供離線使用",
    "audio.offline.ready": "離線就緒",
    "audio.offline.downloading": "下載中...",

    // OfflineStatus — iOS unsupported
    "audio.offline.updateIos": "更新 iOS 以使用離線功能",
    "audio.offline.iosTooltip": "離線快取需要 iOS 17.4 或更新版本",

    // SemanticSearch — input
    "audio.search.placeholder": "以主題或感受描述詩歌...",
    "audio.search.ariaLabel": "描述要搜尋的詩歌",
    "audio.search.helpTip":
      "提示：以主題或感受描述 \u2014 例如「在神寶座前」、「standing before God\u2019s throne」· 按 Enter 搜尋",

    // SemanticSearch — button
    "audio.search.searchButton": "搜尋",
    "audio.search.searching": "搜尋中...",
    "audio.search.searchSongsByDescription": "以描述搜尋詩歌",

    // SemanticSearch — loading / empty states
    "audio.search.searchingByMeaning": "按語意搜尋中...",
    "audio.search.loadingSongs": "載入詩歌中...",
    "audio.search.noSongsMatchFilters": "沒有符合篩選條件的詩歌",
    "audio.search.noMatchingSongs": "找不到符合的詩歌",
    "audio.search.tryRemovingFilters": "嘗試移除部分篩選條件以查看更多結果",
    "audio.search.tryDifferentDescription":
      "嘗試使用不同的描述，或詩歌可能尚未產生嵌入向量",

    // SemanticSearch — result count
    "audio.search.songsFoundLabel": "首詩歌",

    // SemanticSearch — similarity badge
    "audio.search.matchSuffix": "相符",

    // SemanticSearch — why this match
    "audio.search.whyThisMatch": "為何相符？",

    // SemanticSearch — errors / toasts
    "audio.search.semanticUnavailable": "語意搜尋無法使用",
    "audio.search.semanticUnavailableSwitch": "語意搜尋無法使用，切換至文字搜尋",
    "audio.search.searchFailed": "搜尋失敗",
    "audio.search.noAudioForSong": "此詩歌沒有可播放的音訊",
    "audio.search.unknownArtist": "未知藝術家",
    "audio.search.failedAudioUrl": "無法取得音訊網址",
    "audio.search.failedLoadPreview": "無法載入音訊預覽",

    // SemanticSearch — lyric line label
    "audio.search.lyric": "歌詞",
  },
});
