import { bundle } from "../messages";

// /docs page — AirPlay & casting help (linked from the controller player's
// AirPlay fallback chip, /docs#airplay). Public page, no auth.
// Translations are Traditional Chinese (繁體中文). The brand "Stream of
// Worship" and product names (AirPlay, Apple TV, Chromecast) remain verbatim
// in both locales.

export const docsBundle = bundle({
  en: {
    "docs.heroTitle": "Casting & AirPlay",
    "docs.heroDescription":
      "Why casting isn't available from iPhone and iPad browsers, and how to play on a TV today.",
    "docs.airplay.title": "AirPlay from iPhone / iPad",
    "docs.airplay.p1":
      "Browsers on iOS (Safari, Chrome, Firefox) don't support casting to Chromecast, so the \"Send to TV\" button can't connect to a Chromecast from the web app on an iPhone or iPad.",
    "docs.airplay.p2":
      "Direct AirPlay from the web app isn't supported yet — the native iOS app (in development) will add proper AirPlay support.",
    "docs.airplay.workaroundTitle": "How to watch on a TV today",
    "docs.airplay.workaround.1":
      "Screen Mirroring: on your iPhone or iPad, open Control Center → Screen Mirroring, choose your Apple TV or AirPlay-compatible TV, then play the songset in Stream of Worship. Mirroring sends both video and audio to the TV.",
    "docs.airplay.workaround.2":
      "Cast from another device: open Stream of Worship in Chrome on Android, or Chrome / Edge on a computer, and use Send to TV with your Chromecast or Google TV streamer.",
    "docs.airplay.workaround.3":
      "HDMI cable: connect your device to the TV with an HDMI adapter and play the songset.",
  },
  "zh-Hant": {
    "docs.heroTitle": "投放與 AirPlay",
    "docs.heroDescription":
      "為什麼 iPhone 和 iPad 的網頁版沒辦法直接投放到電視？這頁整理了現在就能在電視上播放的方法。",
    "docs.airplay.title": "從 iPhone / iPad 用 AirPlay",
    "docs.airplay.p1":
      "iPhone 和 iPad 上的瀏覽器（Safari、Chrome、Firefox 都一樣）不支援投放到 Chromecast，所以在網頁版按「投放到電視」是連不上的。",
    "docs.airplay.p2":
      "想在電視上直接用 AirPlay，目前網頁版還做不到——之後的 iOS 版 App（開發中）會加入完整的 AirPlay 功能。",
    "docs.airplay.workaroundTitle": "現在就能在電視上播放的方法",
    "docs.airplay.workaround.1":
      "螢幕鏡像輸出：在 iPhone 或 iPad 打開控制中心 →「螢幕鏡像輸出」，選擇你的 Apple TV 或支援 AirPlay 的電視，再回到 Stream of Worship 播放敬拜歌單。畫面和聲音都會一起送到電視。",
    "docs.airplay.workaround.2":
      "改用其他裝置投放：在 Android 手機的 Chrome，或電腦上的 Chrome / Edge 開啟 Stream of Worship，就能用「投放到電視」連上 Chromecast 或 Google TV。",
    "docs.airplay.workaround.3":
      "HDMI 線：用 HDMI 轉接器把裝置接到電視，就可以直接播放敬拜歌單。",
  },
});