import { bundle } from "../messages";

// Core UI chrome: navigation, settings form, and shared/common strings.
// Translations are Traditional Chinese (繁體中文). Brand name "Stream of
// Worship" is a proper noun retained verbatim in both locales.

export const core = bundle({
  en: {
    "brand.name": "Stream of Worship",

    // Navigation
    "nav.main.ariaLabel": "Main navigation",
    "nav.songsets": "Songsets",
    "nav.favorites": "Favorites",
    "nav.settings": "Settings",

    // Home page
    "home.title": "Stream of Worship",
    "home.subtitle":
      "Worship music transition and playback system. Manage songsets, render audio and video, and lead worship seamlessly.",
    "home.viewSongsets": "View Songsets",

    // Settings page
    "settings.title": "Settings",
    "settings.loading": "Loading settings",
    "settings.saved": "Settings saved",
    "settings.failedLoad": "Failed to load settings",
    "settings.failedSave": "Failed to save settings",

    // Settings form
    "settings.section.transitions": "Transitions",
    "settings.transitions.description": "Default transition parameters for new songs",
    "settings.defaultGapBeats": "Default gap beats",
    "settings.unit.beat": "beat",
    "settings.unit.beats": "beats",

    "settings.section.video": "Video",
    "settings.video.description": "Default render settings for lyrics videos",
    "settings.defaultTemplate": "Default template",
    "settings.defaultResolution": "Default resolution",
    "settings.defaultFontSize": "Default font size",
    "settings.defaultFontFamily": "Default font family",

    "settings.section.playback": "Playback",
    "settings.playback.description": "Lyrics display and playback behavior",
    "settings.lyricsLoopWindow": "Lyrics loop window",
    "settings.unit.second": "second",
    "settings.unit.seconds": "seconds",
    "settings.lyricsLoopWindowHint": "How many seconds of upcoming lyrics to display",

    "settings.section.offline": "Offline",
    "settings.offline.description": "Offline caching preferences",
    "settings.autoCacheAfterRender": "Auto-cache after render",
    "settings.autoCacheHint": "Automatically cache rendered files for offline playback",
    "settings.iosNote": "Offline caching requires iOS 17.4 or later",

    "settings.section.advanced": "Advanced",
    "settings.advanced.description": "Desktop-only settings",
    "settings.defaultKeyShift": "Default key shift",
    "settings.noKeyShift": "0 (no shift)",
    "settings.unit.semitones": "semitones",
    "settings.keyShiftHint": "Default semitone shift applied to each transition",
    "settings.timingReviewFont": "Timing review font",
    "settings.timingReviewFontHint": "Font used in the timing editor for LRC review",

    "settings.reset": "Reset",
    "settings.save": "Save",
    "settings.saving": "Saving...",

    // Language picker
    "settings.language": "Language",
    "settings.language.description": "Display language for the app interface",
    "settings.language.en": "English",
    "settings.language.zhHant": "繁體中文",

    // Settings option labels (dropdown values)
    "settings.option.fontPreset.S": "Small (32px)",
    "settings.option.fontPreset.M": "Medium (48px)",
    "settings.option.fontPreset.L": "Large (64px)",
    "settings.option.fontPreset.XL": "Extra Large (80px)",
    "settings.option.timingFont.sans": "Sans-serif",
    "settings.option.timingFont.mono": "Monospace",
    "settings.option.timingFont.serif": "Serif",
    "settings.option.template.dark": "Dark",
    "settings.option.template.gradient_warm": "Gradient Warm",
    "settings.option.template.gradient_blue": "Gradient Blue",
    "settings.option.resolution.720p": "720p (HD)",
    "settings.option.resolution.1080p": "1080p (Full HD)",
    "settings.option.fontFamily.lxgw_wenkai_tc": "Traditional",
    "settings.option.fontFamily.chiron_goround_tc": "Elegant",
    "settings.option.fontFamily.chocolate_classical_sans": "Modern",
    "settings.option.fontFamily.noto_serif_tc": "Classic",
  },
  "zh-Hant": {
    "brand.name": "Stream of Worship",

    // Navigation
    "nav.main.ariaLabel": "主要導覽",
    "nav.songsets": "詩歌集",
    "nav.favorites": "我的最愛",
    "nav.settings": "設定",

    // Home page
    "home.title": "Stream of Worship",
    "home.subtitle":
      "敬拜音樂轉場與播放系統。管理詩歌集、渲染音訊與影片，流暢地帶領敬拜。",
    "home.viewSongsets": "檢視詩歌集",

    // Settings page
    "settings.title": "設定",
    "settings.loading": "載入設定中…",
    "settings.saved": "設定已儲存",
    "settings.failedLoad": "無法載入設定",
    "settings.failedSave": "無法儲存設定",

    // Settings form
    "settings.section.transitions": "轉場",
    "settings.transitions.description": "新詩歌的預設轉場參數",
    "settings.defaultGapBeats": "預設間隔拍數",
    "settings.unit.beat": "拍",
    "settings.unit.beats": "拍",

    "settings.section.video": "影片",
    "settings.video.description": "歌詞影片的預設渲染設定",
    "settings.defaultTemplate": "預設範本",
    "settings.defaultResolution": "預設解析度",
    "settings.defaultFontSize": "預設字型大小",
    "settings.defaultFontFamily": "預設字型",

    "settings.section.playback": "播放",
    "settings.playback.description": "歌詞顯示與播放行為",
    "settings.lyricsLoopWindow": "歌詞循環視窗",
    "settings.unit.second": "秒",
    "settings.unit.seconds": "秒",
    "settings.lyricsLoopWindowHint": "要顯示多少秒即將出現的歌詞",

    "settings.section.offline": "離線",
    "settings.offline.description": "離線快取偏好",
    "settings.autoCacheAfterRender": "渲染後自動快取",
    "settings.autoCacheHint": "自動快取已渲染的檔案以供離線播放",
    "settings.iosNote": "離線快取需要 iOS 17.4 或更新版本",

    "settings.section.advanced": "進階",
    "settings.advanced.description": "僅限桌面版的設定",
    "settings.defaultKeyShift": "預設移調",
    "settings.noKeyShift": "0（不移調）",
    "settings.unit.semitones": "半音",
    "settings.keyShiftHint": "套用到每個轉場的預設半音移調",
    "settings.timingReviewFont": "計時檢閱字型",
    "settings.timingReviewFontHint": "用於 LRC 檢閱計時編輯器的字型",

    "settings.reset": "重設",
    "settings.save": "儲存",
    "settings.saving": "儲存中...",

    // Language picker
    "settings.language": "語言",
    "settings.language.description": "應用程式介面的顯示語言",
    "settings.language.en": "English",
    "settings.language.zhHant": "繁體中文",

    // Settings option labels (dropdown values)
    "settings.option.fontPreset.S": "小（32px）",
    "settings.option.fontPreset.M": "中（48px）",
    "settings.option.fontPreset.L": "大（64px）",
    "settings.option.fontPreset.XL": "特大（80px）",
    "settings.option.timingFont.sans": "無襯線",
    "settings.option.timingFont.mono": "等寬",
    "settings.option.timingFont.serif": "襯線",
    "settings.option.template.dark": "深色",
    "settings.option.template.gradient_warm": "暖色漸層",
    "settings.option.template.gradient_blue": "藍色漸層",
    "settings.option.resolution.720p": "720p（高畫質）",
    "settings.option.resolution.1080p": "1080p（全高畫質）",
    "settings.option.fontFamily.lxgw_wenkai_tc": "繁體",
    "settings.option.fontFamily.chiron_goround_tc": "優雅",
    "settings.option.fontFamily.chocolate_classical_sans": "現代",
    "settings.option.fontFamily.noto_serif_tc": "經典",
  },
});
