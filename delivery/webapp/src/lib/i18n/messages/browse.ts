import { bundle } from "../messages";

// Browse/songset-add chrome: song card, search, list, browse sheet, favorite
// toggle, musical-key multi-select, shared filters. Translations are
// Traditional Chinese (繁體中文). Song metadata (title/composer/album/key/
// tempo) is DB data rendered verbatim; only UI chrome is translated here.

export const browseBundle = bundle({
  en: {
    // FavoriteButton
    "browse.favorite.add": "Add to favorites",
    "browse.favorite.remove": "Remove from favorites",
    "browse.favorite.listenToPrefix": "Listen to ",
    "browse.favorite.listenToSuffix": "% of the song to favorite",

    // MusicalKeyMultiSelect
    "browse.keys.all": "All",
    "browse.keys.label": "Keys:",
    "browse.keys.dropdownLabel": "Musical Key",
    "browse.keys.clearAll": "Clear all",
    "browse.clearAll": "Clear all",

    // ThemeMultiSelect
    "browse.themes.all": "All",
    "browse.themes.label": "Themes:",
    "browse.themes.dropdownLabel": "Theme",
    "browse.themes.clearAll": "Clear all",

    // AlbumMultiSelect
    "browse.albums.label": "Albums:",
    "browse.albums.dropdownLabel": "Albums",
    "browse.albums.all": "All",
    "browse.albums.selected": "Selected",
    "browse.albums.clearAll": "Clear all",

    // BpmRangeMultiSelect
    "browse.bpm.label": "BPM:",
    "browse.bpm.dropdownLabel": "BPM Range",
    "browse.bpm.all": "All",
    "browse.bpm.clearAll": "Clear all",
    "browse.bpm.band.slow": "Slow",
    "browse.bpm.band.moderate": "Moderate",
    "browse.bpm.band.upbeat": "Upbeat",
    "browse.bpm.band.fast": "Fast",

    // SongCard
    "browse.unknownArtist": "Unknown Artist",
    "browse.playPreview": "Play preview",
    "browse.pausePreview": "Pause preview",
    "browse.verified": "Verified",
    "browse.addToSongset": "Add to songset",
    "browse.alreadyAdded": "Already added",
    "browse.songsetFull": "Songset full",
    "browse.bpm": "BPM",

    // SongSearch
    "browse.search.placeholder": "Search songs by title, artist, or album...",
    "browse.search.ariaLabel": "Search songs",
    "browse.search.clear": "Clear search",
    "browse.search.searching": "Searching...",
    "browse.search.searchingSongs": "Searching songs",
    "browse.search.searchingStatus": "Searching songs...",
    "browse.search.runSearch": "Run song search",
    "browse.search.searchButton": "Search",
    "browse.search.searchByDescription": "Search songs by description",
    "browse.search.hint":
      "Tip: search by title, pinyin, or composer — e.g. \u2018歡喜\u2019, \u2018huan xi\u2019, \u2018曾祥怡\u2019 · Press Enter to search",
    "browse.search.failed": "Failed to search songs",

    // SongList
    "browse.dragReorder": "Drag to reorder song ",
    "browse.play": "Play ",
    "browse.pause": "Pause ",
    "browse.song": "song",
    "browse.unknownSong": "Unknown Song",
    "browse.marked": "marked",
    "browse.unit.beats": "beats",
    "browse.transition.editBefore": "Edit transition before ",
    "browse.transition.ariaGap": ": gap ",
    "browse.transition.ariaCrossfade": ", crossfade",
    "browse.transition.gapLabel": "Gap: ",
    "browse.transition.crossfadeSuffix": " + crossfade",
    "browse.remove": "Remove ",
    "browse.confirmDelete": "Confirm delete ",
    "browse.delete": "Delete",
    "browse.songs": "Songs",
    "browse.songsUnit": "songs",
    "browse.empty.noSongs": "No songs in this songset",
    "browse.empty.tapToAdd": "Tap the + button to add songs",

    // BrowseSheet
    "browse.sheet.title": "Search Songs",
    "browse.sheet.description": "Search the catalog and add songs to your songset",
    "browse.sheet.modeAriaLabel": "Search mode",
    "browse.sheet.keywordTab": "Keyword",
    "browse.sheet.describeTab": "Describe",
    "browse.sheet.keywordControls": "Keyword song search controls",
    "browse.sheet.describeControls": "Describe song search controls",
    "browse.sheet.keywordResults": "Keyword song search results",
    "browse.sheet.describeResults": "Describe song search results",
    "browse.retry": "Retry",
    "browse.done": "Done",
    "browse.empty.noSongsFoundPrefix": "No songs found for \u201c",
    "browse.empty.noSongsFoundSuffix": "\u201d",
    "browse.empty.tryAdjustFilters": "Try adjusting your filters or search term",
    "browse.empty.tryDifferentTerm": "Try a different search term",
    "browse.empty.noMatchFilters": "No songs match your filters",
    "browse.empty.tryRemoveFilters": "Try removing some filters to see more results",
    "browse.empty.noSongsAvailable": "No songs available",
    "browse.empty.startTyping": "Start typing to search for songs",
    "browse.favorites": "Favorites",
    "browse.allSongs": "All Songs",

    // Toasts (BrowseSheet + SongList)
    "browse.songNotFound": "Song not found",
    "browse.songAddedToSongset": "Song added to songset",
    "browse.failedToAddSong": "Failed to add song",
    "browse.noAudioAvailable": "No audio available for this song",
    "browse.failedToLoadPreview": "Failed to load audio preview",
  },
  "zh-Hant": {
    // FavoriteButton
    "browse.favorite.add": "加入最愛",
    "browse.favorite.remove": "移除最愛",
    "browse.favorite.listenToPrefix": "聆聽歌曲達 ",
    "browse.favorite.listenToSuffix": "% 即可加入最愛",

    // MusicalKeyMultiSelect
    "browse.keys.all": "全部",
    "browse.keys.label": "調性：",
    "browse.keys.dropdownLabel": "調性",
    "browse.keys.clearAll": "清除全部",
    "browse.clearAll": "清除全部",

    // ThemeMultiSelect
    "browse.themes.all": "全部",
    "browse.themes.label": "主題：",
    "browse.themes.dropdownLabel": "主題",
    "browse.themes.clearAll": "清除全部",

    // AlbumMultiSelect
    "browse.albums.label": "專輯：",
    "browse.albums.dropdownLabel": "專輯",
    "browse.albums.all": "全部",
    "browse.albums.selected": "已選",
    "browse.albums.clearAll": "清除全部",

    // BpmRangeMultiSelect
    "browse.bpm.label": "BPM：",
    "browse.bpm.dropdownLabel": "BPM 範圍",
    "browse.bpm.all": "全部",
    "browse.bpm.clearAll": "清除全部",
    "browse.bpm.band.slow": "慢速",
    "browse.bpm.band.moderate": "中速",
    "browse.bpm.band.upbeat": "快步",
    "browse.bpm.band.fast": "快速",

    // SongCard
    "browse.unknownArtist": "未知演唱者",
    "browse.playPreview": "播放預覽",
    "browse.pausePreview": "暫停預覽",
    "browse.verified": "已驗證",
    "browse.addToSongset": "加入詩歌集",
    "browse.alreadyAdded": "已加入",
    "browse.songsetFull": "詩歌集已滿",
    "browse.bpm": "BPM",

    // SongSearch
    "browse.search.placeholder": "依歌名、演唱者或專輯搜尋詩歌...",
    "browse.search.ariaLabel": "搜尋詩歌",
    "browse.search.clear": "清除搜尋",
    "browse.search.searching": "搜尋中...",
    "browse.search.searchingSongs": "搜尋詩歌中",
    "browse.search.searchingStatus": "搜尋詩歌中...",
    "browse.search.runSearch": "執行詩歌搜尋",
    "browse.search.searchButton": "搜尋",
    "browse.search.searchByDescription": "依描述搜尋詩歌",
    "browse.search.hint":
      "搜尋提示：以歌名、拼音或作曲者搜尋 — 例如「歡喜」「huan xi」「曾祥怡」 · 按 Enter 搜尋",
    "browse.search.failed": "搜尋詩歌失敗",

    // SongList
    "browse.dragReorder": "拖曳以重新排序詩歌 ",
    "browse.play": "播放 ",
    "browse.pause": "暫停 ",
    "browse.song": "詩歌",
    "browse.unknownSong": "未知詩歌",
    "browse.marked": "已標記",
    "browse.unit.beats": "拍",
    "browse.transition.editBefore": "編輯此詩歌前的轉場：",
    "browse.transition.ariaGap": "，間隔 ",
    "browse.transition.ariaCrossfade": "，交叉淡入",
    "browse.transition.gapLabel": "間隔：",
    "browse.transition.crossfadeSuffix": " + 交叉淡入",
    "browse.remove": "移除 ",
    "browse.confirmDelete": "確認刪除 ",
    "browse.delete": "刪除",
    "browse.songs": "詩歌",
    "browse.songsUnit": "首詩歌",
    "browse.empty.noSongs": "此詩歌集中沒有詩歌",
    "browse.empty.tapToAdd": "點擊 + 按鈕加入詩歌",

    // BrowseSheet
    "browse.sheet.title": "搜尋詩歌",
    "browse.sheet.description": "搜尋目錄並將詩歌加入您的詩歌集",
    "browse.sheet.modeAriaLabel": "搜尋模式",
    "browse.sheet.keywordTab": "關鍵字",
    "browse.sheet.describeTab": "描述",
    "browse.sheet.keywordControls": "關鍵字詩歌搜尋控制項",
    "browse.sheet.describeControls": "描述詩歌搜尋控制項",
    "browse.sheet.keywordResults": "關鍵字詩歌搜尋結果",
    "browse.sheet.describeResults": "描述詩歌搜尋結果",
    "browse.retry": "重試",
    "browse.done": "完成",
    "browse.empty.noSongsFoundPrefix": "找不到符合「",
    "browse.empty.noSongsFoundSuffix": "」的詩歌",
    "browse.empty.tryAdjustFilters": "請調整您的篩選條件或搜尋字詞",
    "browse.empty.tryDifferentTerm": "請嘗試其他搜尋字詞",
    "browse.empty.noMatchFilters": "沒有詩歌符合您的篩選條件",
    "browse.empty.tryRemoveFilters": "請移除部分篩選條件以查看更多結果",
    "browse.empty.noSongsAvailable": "目前沒有可用的詩歌",
    "browse.empty.startTyping": "開始輸入以搜尋詩歌",
    "browse.favorites": "我的最愛",
    "browse.allSongs": "全部詩歌",

    // Toasts (BrowseSheet + SongList)
    "browse.songNotFound": "找不到詩歌",
    "browse.songAddedToSongset": "詩歌已加入詩歌集",
    "browse.failedToAddSong": "加入詩歌失敗",
    "browse.noAudioAvailable": "此詩歌沒有可用的音訊",
    "browse.failedToLoadPreview": "無法載入音訊預覽",
  },
});
