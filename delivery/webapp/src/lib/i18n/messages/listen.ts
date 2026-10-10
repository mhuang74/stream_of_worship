import { bundle } from "../messages";

// Listen page UI chrome (issue #253): the full-page catalog listening
// experience with search, favorites, songs-in-my-songsets, and discovery.
// Translations are Traditional Chinese (繁體中文). Song metadata remains
// verbatim. Search-mode chrome reuses the shared `browse.*` keys rendered
// inside CatalogSearch; only page-level (section) strings live here.

export const listenBundle = bundle({
  en: {
    "listen.title": "Listen",
    "listen.description": "The full catalog at your fingertips — search, play, and collect.",

    // Search section
    "listen.section.search": "Search",

    // Songs in My Songsets section
    "listen.section.mySongsets": "Songs in My Songsets",
    "listen.viewSongsetAriaLabel": "View songset",

    // Discovery section
    "listen.section.discovery": "Discovery",
    "listen.discovery.loadMore": "Load more",
    "listen.discovery.loading": "Loading...",
    "listen.discovery.loadFailed": "Failed to load discovery songs",
    "listen.discovery.empty.title": "Nothing new from the community yet",
    "listen.discovery.empty.description":
      "Songs other listeners are favoriting or adding to their songsets will show up here.",

    // My Songsets empty state
    "listen.mySongsets.empty.title": "No songset songs yet",
    "listen.mySongsets.empty.description":
      "Songs you add to your songsets will show up here.",
  },
  "zh-Hant": {
    "listen.title": "收聽",
    "listen.description": "整個詩歌目錄，隨手可及——搜尋、播放、收藏。",

    // Search section
    "listen.section.search": "搜尋",

    // Songs in My Songsets section
    "listen.section.mySongsets": "我的敬拜歌單裡的詩歌",
    "listen.viewSongsetAriaLabel": "查看敬拜歌單",

    // Discovery section
    "listen.section.discovery": "探索",
    "listen.discovery.loadMore": "載入更多",
    "listen.discovery.loading": "載入中…",
    "listen.discovery.loadFailed": "探索歌曲載入失敗，請再試一次",
    "listen.discovery.empty.title": "社群還沒有新的詩歌",
    "listen.discovery.empty.description":
      "其他聽眾的最愛或加入敬拜歌單的詩歌，會顯示在這裡。",

    // My Songsets empty state
    "listen.mySongsets.empty.title": "還沒有敬拜歌單詩歌",
    "listen.mySongsets.empty.description":
      "加入敬拜歌單的詩歌會顯示在這裡。",
  },
});
