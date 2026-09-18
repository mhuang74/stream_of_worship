import { bundle } from "../messages";

// /offline list page (issue #211 follow-up): the offline redirect target and
// playback entry. Rendered purely from the IndexedDB offline index — the
// document itself is pre-cached by the service worker, never network-fetched
// on boot. Translations are Traditional Chinese (繁體中文).

export const offlineBundle = bundle({
  en: {
    "offline.title": "Offline ready",
    "offline.empty": "Nothing downloaded yet. Open a songset while online and tap Download for offline.",
    "offline.emptyLink": "Browse songsets",
    "offline.ready": "Offline ready",
    "offline.needsRedownload": "Cached files are missing — download again while online",
    "offline.updateAvailable": "Update available",
    "offline.update": "Update",
    "offline.updated": "Updated to the latest render",
    "offline.remove": "Remove from offline",
    "offline.play": "Play",
    "offline.cachedPrefix": "Cached",
  },
  "zh-Hant": {
    "offline.title": "離線就緒",
    "offline.empty": "還沒有下載的內容。請在連線時開啟敬拜歌單，然後點選「離線下載」。",
    "offline.emptyLink": "瀏覽敬拜歌單",
    "offline.ready": "離線就緒",
    "offline.needsRedownload": "快取檔案遺失——請在連線時重新下載",
    "offline.updateAvailable": "有可用更新",
    "offline.update": "更新",
    "offline.updated": "已更新至最新版本",
    "offline.remove": "移除離線副本",
    "offline.play": "播放",
    "offline.cachedPrefix": "已快取",
  },
});
