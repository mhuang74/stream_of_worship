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

    // LocateSongsetsPopover
    "audio.locate.findButton": "Find containing songsets",
    "audio.locate.findTitle": "Find in songsets",
    "audio.locate.loadFailed": "Failed to load songsets",
    "audio.locate.empty": "This song is not in any of your songsets.",
    "audio.locate.listAria": "Songsets containing this song",
    "audio.locate.songs": "songs",
    "audio.locate.position": "Position",
    "audio.locate.origin": "Origin",

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
    "audio.lyrics.unavailable": "歌詞載入失敗",

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
    "audio.lyrics.noLyrics": "這首歌目前沒有歌詞",

    // OfflineIndicator
    "audio.offline.message": "你目前離線",

    // OfflineStatus — toasts
    "audio.offline.cachingNotAvailable": "這個瀏覽器不支援離線快取",
    "audio.offline.noArtifacts": "還沒有可以離線保存的檔案",
    "audio.offline.downloaded": "已下載，離線也能聽",
    "audio.offline.downloadFailed": "下載失敗，請再試一次",

    // OfflineStatus — button / badge
    "audio.offline.downloadForOffline": "離線下載",
    "audio.offline.ready": "離線就緒",
    "audio.offline.downloading": "下載中\u2026",

    // OfflineStatus — iOS unsupported
    "audio.offline.updateIos": "更新 iOS 才能離線使用",
    "audio.offline.iosTooltip": "離線快取需要 iOS 17.4 或更新版本",

    // LocateSongsetsPopover
    "audio.locate.findButton": "找出收錄這首歌的敬拜歌單",
    "audio.locate.findTitle": "在敬拜歌單中搜尋",
    "audio.locate.loadFailed": "敬拜歌單載入失敗，請再試一次",
    "audio.locate.empty": "這首歌還沒被加進任何敬拜歌單",
    "audio.locate.listAria": "收錄這首歌的敬拜歌單",
    "audio.locate.songs": "首詩歌",
    "audio.locate.position": "位置",
    "audio.locate.origin": "來源",

    // SemanticSearch — input
    "audio.search.placeholder": "用主題或感受描述想找的詩歌\u2026",
    "audio.search.ariaLabel": "描述想找的詩歌",
    "audio.search.helpTip":
      "小提示：用主題或感受來描述，例如「在神寶座前」、「standing before God\u2019s throne」· 按 Enter 搜尋",

    // SemanticSearch — button
    "audio.search.searchButton": "搜尋",
    "audio.search.searching": "搜尋中\u2026",
    "audio.search.searchSongsByDescription": "用描述搜尋詩歌",

    // SemanticSearch — loading / empty states
    "audio.search.searchingByMeaning": "語意搜尋中\u2026",
    "audio.search.loadingSongs": "載入歌曲中\u2026",
    "audio.search.noSongsMatchFilters": "沒有詩歌符合目前的篩選條件",
    "audio.search.noMatchingSongs": "找不到符合的詩歌",
    "audio.search.tryRemovingFilters": "試著移除部分篩選條件，可能會看到更多結果",
    "audio.search.tryDifferentDescription":
      "換個描述再搜一次，也可能這些詩歌還沒建立語意索引",

    // SemanticSearch — result count
    "audio.search.songsFoundLabel": "首詩歌",

    // SemanticSearch — similarity badge
    "audio.search.matchSuffix": "相符",

    // SemanticSearch — why this match
    "audio.search.whyThisMatch": "為什麼相符？",

    // SemanticSearch — errors / toasts
    "audio.search.semanticUnavailable": "語意搜尋目前不支援",
    "audio.search.semanticUnavailableSwitch": "語意搜尋目前不支援，已切換為文字搜尋",
    "audio.search.searchFailed": "搜尋失敗，請再試一次",
    "audio.search.noAudioForSong": "這首歌沒有音訊檔",
    "audio.search.unknownArtist": "演出者不詳",
    "audio.search.failedAudioUrl": "取得音訊連結失敗，請再試一次",
    "audio.search.failedLoadPreview": "預覽音訊載入失敗",

    // SemanticSearch — lyric line label
    "audio.search.lyric": "歌詞",
  },
});
