import { bundle } from "../messages";

// /worship list page (issue #211 follow-up descope): the offline redirect
// target and the nav's Worship surface. Boots from the IndexedDB offline
// index (document pre-cached by the service worker, never network-fetched on
// boot); when online it lists every songset with a rendered lyrics video,
// default-filtered to those with an Offline Copy.

export const offlineBundle = bundle({
  en: {
    "worship.page.title": "Worship",
    "worship.filter.ready": "Ready for Offline Worship",
    "worship.filter.all": "All",
    "worship.empty.all": "No songsets with rendered lyrics videos yet.",
    "offline.empty": "Nothing downloaded yet. Open a songset while online and tap Download for offline.",
    "offline.emptyLink": "Browse songsets",
    "offline.needsRedownload": "Cached files are missing — download again while online",
    "offline.updated": "Updated to the latest render",
    "offline.remove": "Remove from offline",
    "offline.play": "Play",
  },
  "zh-Hant": {
    "worship.page.title": "敬拜",
    "worship.filter.ready": "可離線敬拜",
    "worship.filter.all": "全部",
    "worship.empty.all": "還沒有已渲染歌詞影片的敬拜歌單。",
    "offline.empty": "還沒有下載的內容。請在連線時開啟敬拜歌單，然後點選「離線下載」。",
    "offline.emptyLink": "瀏覽敬拜歌單",
    "offline.needsRedownload": "快取檔案遺失——請在連線時重新下載",
    "offline.updated": "已更新至最新版本",
    "offline.remove": "移除離線副本",
    "offline.play": "播放",
  },
});
