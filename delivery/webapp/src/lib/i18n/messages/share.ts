import { bundle } from "../messages";

// Share landing page Download affordance (issue #218 PR2, ADR-0009): the
// explicit, never-automatic download of a share's rendered artifacts into
// the token-scoped offline namespace, plus the staleness hint only the
// landing page can compute.

export const shareBundle = bundle({
  en: {
    "share.download": "Download for offline",
    "share.downloading": "Downloading…",
    "share.downloaded": "Downloaded for offline playback",
    "share.redownload": "Re-download (the set has been updated)",
    "share.downloadDone": "Downloaded for offline playback",
    "share.downloadNoArtifacts": "No downloadable files for this share",
    "share.downloadFailed": "Download failed — please try again",
  },
  "zh-Hant": {
    "share.download": "下載以離線播放",
    "share.downloading": "下載中…",
    "share.downloaded": "已下載，可離線播放",
    "share.redownload": "重新下載（歌單已更新）",
    "share.downloadDone": "已下載，可離線播放",
    "share.downloadNoArtifacts": "這個分享沒有可下載的檔案",
    "share.downloadFailed": "下載失敗，請再試一次",
  },
});
