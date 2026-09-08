import { bundle } from "../messages";

// Songset UI chrome: list, editor, row, skeletons, client pages.
// Translations are Traditional Chinese (繁體中文). Song metadata (titles,
// composers, lyrics) and user-entered content (songset names, descriptions)
// remain verbatim in both locales.

export const songsetsBundle = bundle({
  en: {
    // Page headings
    "songsets.page.title": "Songsets",
    "songsets.page.subtitle": "Manage your worship song collections",

    // Errors / fallbacks
    "songsets.error.signIn": "Please sign in to view your songsets",
    "songsets.error.loadFailed": "Failed to load songsets",
    "songsets.error.createFailed": "Failed to create songset",
    "songsets.error.renameFailed": "Failed to rename songset",
    "songsets.error.duplicateFailed": "Failed to duplicate songset",
    "songsets.error.deleteFailed": "Failed to delete songset",
    "songsets.error.notFound": "Songset not found",
    "songsets.error.reorderFailed": "Failed to reorder items",
    "songsets.error.removeItemFailed": "Failed to remove item",
    "songsets.error.updateTransitionFailed": "Failed to update transition",
    "songsets.error.updateDescriptionFailed": "Failed to update description",
    "songsets.error.removeSongFailed": "Failed to remove song",
    "songsets.error.addSongFailed": "Failed to add song to songset",
    "songsets.error.downloadAudioFailed": "Failed to download audio",
    "songsets.error.downloadVideoFailed": "Failed to download video",

    // Toasts (success)
    "songsets.toast.created": "Songset created successfully",
    "songsets.toast.createdButEditorFailed": "Songset created but could not open editor",
    "songsets.toast.renamed": "Songset renamed successfully",
    "songsets.toast.duplicated": "Songset duplicated successfully",
    "songsets.toast.deleted": "Songset deleted successfully",
    "songsets.toast.songsetDeleted": "Songset deleted",
    "songsets.toast.songsetDuplicated": "Songset duplicated",
    "songsets.toast.songRemoved": "Song removed",
    "songsets.toast.transitionUpdated": "Transition updated",
    "songsets.toast.descriptionUpdated": "Description updated",

    // Toasts (download)
    "songsets.toast.preparingDownload": "Preparing download...",
    "songsets.toast.downloadStarted": "Download started",

    // Defaults
    "songsets.defaultDuplicateName": "Copy of Songset",
    "songsets.copyOfPrefix": "Copy of ",

    // Navigation
    "songsets.backToSongsets": "Back to songsets",

    // Actions (shared across row/editor/list)
    "songsets.action.render": "Render",
    "songsets.action.play": "Play",
    "songsets.action.rename": "Rename",
    "songsets.action.duplicate": "Duplicate",
    "songsets.action.share": "Share",
    "songsets.action.downloadAudio": "Download Audio",
    "songsets.action.downloadVideo": "Download Video",
    "songsets.action.delete": "Delete",
    "songsets.action.search": "Search",
    "songsets.action.retry": "Retry",
    "songsets.action.cancel": "Cancel",
    "songsets.action.create": "Create",
    "songsets.action.save": "Save",
    "songsets.action.prev": "Prev",
    "songsets.action.next": "Next",
    "songsets.action.reRender": "Re-render",
    "songsets.action.playAnyway": "Play anyway",
    "songsets.action.renderAgain": "Render again",
    "songsets.action.editDescription": "Edit description",
    "songsets.action.createSongset": "Create Songset",

    // Units
    "songsets.unit.song": "song",
    "songsets.unit.songs": "songs",

    // Labels
    "songsets.label.name": "Name",
    "songsets.label.descriptionOptional": "Description (optional)",

    // Placeholders
    "songsets.placeholder.name": "e.g., Sunday Worship",
    "songsets.placeholder.description": "e.g., Easter service songs",
    "songsets.placeholder.songsetName": "Songset name",
    "songsets.searchPlaceholder": "Search by songset name or song title, artist, album...",

    // Empty states
    "songsets.empty.noSongsets": "No songsets yet. Create one to get started.",
    "songsets.empty.searchNoMatch": "No songsets match your search.",

    // Dialogs
    "songsets.dialog.createTitle": "Create New Songset",
    "songsets.dialog.createDescription": "Enter a name for your new songset.",
    "songsets.dialog.deleteTitle": "Delete Songset",
    "songsets.dialog.deleteDescription": "Are you sure you want to delete this songset? This action cannot be undone.",
    "songsets.dialog.deleteNamedDescription": "Are you sure you want to delete \"{name}\"? This action cannot be undone.",
    "songsets.dialog.renameTitle": "Rename Songset",
    "songsets.dialog.renameDescription": "Enter a new name for this songset.",

    // Loading states
    "songsets.loading.songsets": "Loading songsets",
    "songsets.loading.songsetsSr": "Loading songsets…",
    "songsets.loading.songset": "Loading songset",
    "songsets.loading.songsetSr": "Loading songset…",
    "songsets.loading.creating": "Creating...",
    "songsets.loading.deleting": "Deleting...",
    "songsets.loading.saving": "Saving...",
    "songsets.loading.searching": "Searching songsets...",

    // Badges / alerts
    "songsets.badge.offline": "Offline",
    "songsets.alert.artifactsStale": "Artifacts out of date",
    "songsets.alert.staleDescription": "Songs have been modified since the last render.",
    "songsets.alert.renderFailed": "Render failed",
    "songsets.markedLines": "marked lines",
    "songsets.markedLinesHint": "Open on desktop for text edit",
    "songsets.overDurationLimit": "Over 25 min",
    "songsets.maxSongsReached": "songs maximum reached",
    "songsets.unknown": "Unknown",

    // Metadata
    "songsets.updatedPrefix": "Updated ",

    // aria-labels
    "songsets.aria.openMenu": "Open menu",
    "songsets.aria.goBack": "Go back",
    "songsets.aria.moreOptions": "More options",
    "songsets.aria.dismiss": "Dismiss",
    "songsets.aria.addSongs": "Add songs",
    "songsets.aria.searchSongsets": "Search songsets",
    "songsets.aria.clearSearch": "Clear search",
    "songsets.aria.search": "Search",
    "songsets.aria.pagination": "Songset pagination",
    "songsets.aria.previousPage": "Previous page",
    "songsets.aria.nextPage": "Next page",
    "songsets.aria.page": "Page",
    "songsets.aria.createNewSongset": "Create new songset",

    // Edit description dialog
    "songsets.dialog.editDescriptionTitle": "Edit Description",
    "songsets.dialog.editDescriptionDescription": "Update the description for this songset.",
    "songsets.label.description": "Description",
  },
  "zh-Hant": {
    // Page headings
    "songsets.page.title": "敬拜歌單",
    "songsets.page.subtitle": "整理你的敬拜歌單",

    // Errors / fallbacks
    "songsets.error.signIn": "登入後就能看到你的敬拜歌單",
    "songsets.error.loadFailed": "敬拜歌單載入失敗，請再試一次",
    "songsets.error.createFailed": "敬拜歌單建立失敗，請再試一次",
    "songsets.error.renameFailed": "重新命名失敗，請再試一次",
    "songsets.error.duplicateFailed": "敬拜歌單複製失敗",
    "songsets.error.deleteFailed": "刪除失敗，請再試一次",
    "songsets.error.notFound": "找不到這個敬拜歌單",
    "songsets.error.reorderFailed": "排序失敗，請再試一次",
    "songsets.error.removeItemFailed": "項目移除失敗",
    "songsets.error.updateTransitionFailed": "轉場更新失敗",
    "songsets.error.updateDescriptionFailed": "描述更新失敗",
    "songsets.error.removeSongFailed": "詩歌移除失敗",
    "songsets.error.addSongFailed": "加入詩歌失敗，請再試一次",
    "songsets.error.downloadAudioFailed": "音訊下載失敗，請再試一次",
    "songsets.error.downloadVideoFailed": "影片下載失敗，請再試一次",

    // Toasts (success)
    "songsets.toast.created": "已建立敬拜歌單",
    "songsets.toast.createdButEditorFailed": "敬拜歌單已建立，但編輯器開不起來",
    "songsets.toast.renamed": "已重新命名敬拜歌單",
    "songsets.toast.duplicated": "已複製敬拜歌單",
    "songsets.toast.deleted": "已刪除敬拜歌單",
    "songsets.toast.songsetDeleted": "已刪除敬拜歌單",
    "songsets.toast.songsetDuplicated": "已複製敬拜歌單",
    "songsets.toast.songRemoved": "已移除詩歌",
    "songsets.toast.transitionUpdated": "轉場已更新",
    "songsets.toast.descriptionUpdated": "描述已更新",

    // Toasts (download)
    "songsets.toast.preparingDownload": "正在準備下載…",
    "songsets.toast.downloadStarted": "已開始下載",

    // Defaults
    "songsets.defaultDuplicateName": "敬拜歌單副本",
    "songsets.copyOfPrefix": "副本 - ",

    // Navigation
    "songsets.backToSongsets": "回敬拜歌單",

    // Actions (shared across row/editor/list)
    "songsets.action.render": "渲染",
    "songsets.action.play": "播放",
    "songsets.action.rename": "重新命名",
    "songsets.action.duplicate": "複製",
    "songsets.action.share": "分享",
    "songsets.action.downloadAudio": "下載音訊",
    "songsets.action.downloadVideo": "下載影片",
    "songsets.action.delete": "刪除",
    "songsets.action.search": "搜尋",
    "songsets.action.retry": "重試",
    "songsets.action.cancel": "取消",
    "songsets.action.create": "建立",
    "songsets.action.save": "儲存",
    "songsets.action.prev": "上一頁",
    "songsets.action.next": "下一頁",
    "songsets.action.reRender": "重新渲染",
    "songsets.action.playAnyway": "仍要播放",
    "songsets.action.renderAgain": "再次渲染",
    "songsets.action.editDescription": "編輯描述",
    "songsets.action.createSongset": "建立敬拜歌單",

    // Units
    "songsets.unit.song": "首詩歌",
    "songsets.unit.songs": "首詩歌",

    // Labels
    "songsets.label.name": "名稱",
    "songsets.label.descriptionOptional": "描述（選填）",

    // Placeholders
    "songsets.placeholder.name": "例如：主日敬拜",
    "songsets.placeholder.description": "例如：復活節詩歌",
    "songsets.placeholder.songsetName": "敬拜歌單名稱",
    "songsets.searchPlaceholder": "搜尋敬拜歌單名稱，或詩歌名、作者、專輯…",

    // Empty states
    "songsets.empty.noSongsets": "還沒有敬拜歌單，來建立第一個吧！",
    "songsets.empty.searchNoMatch": "沒有符合條件的敬拜歌單，換個關鍵字試試。",

    // Dialogs
    "songsets.dialog.createTitle": "建立新敬拜歌單",
    "songsets.dialog.createDescription": "為新的敬拜歌單取個名字。",
    "songsets.dialog.deleteTitle": "刪除敬拜歌單",
    "songsets.dialog.deleteDescription": "確定要刪除這個敬拜歌單嗎？刪了就救不回來了。",
    "songsets.dialog.deleteNamedDescription": "確定要刪除「{name}」嗎？刪了就救不回來了。",
    "songsets.dialog.renameTitle": "重新命名敬拜歌單",
    "songsets.dialog.renameDescription": "為這個敬拜歌單取個新名字。",

    // Loading states
    "songsets.loading.songsets": "敬拜歌單載入中",
    "songsets.loading.songsetsSr": "敬拜歌單載入中…",
    "songsets.loading.songset": "敬拜歌單載入中",
    "songsets.loading.songsetSr": "敬拜歌單載入中…",
    "songsets.loading.creating": "建立中…",
    "songsets.loading.deleting": "刪除中…",
    "songsets.loading.saving": "儲存中…",
    "songsets.loading.searching": "搜尋敬拜歌單中…",

    // Badges / alerts
    "songsets.badge.offline": "離線",
    "songsets.alert.artifactsStale": "影片可能有更新",
    "songsets.alert.staleDescription": "敬拜歌單在上次渲染後有修改，建議重新渲染。",
    "songsets.alert.renderFailed": "渲染失敗",
    "songsets.markedLines": "行已標記",
    "songsets.markedLinesHint": "在桌面版開啟以編輯文字",
    "songsets.overDurationLimit": "超過 25 分鐘",
    "songsets.maxSongsReached": "首詩歌已達上限",
    "songsets.unknown": "未知",

    // Metadata
    "songsets.updatedPrefix": "更新於 ",

    // aria-labels
    "songsets.aria.openMenu": "開啟選單",
    "songsets.aria.goBack": "返回",
    "songsets.aria.moreOptions": "更多選項",
    "songsets.aria.dismiss": "關閉",
    "songsets.aria.addSongs": "加入詩歌",
    "songsets.aria.searchSongsets": "搜尋敬拜歌單",
    "songsets.aria.clearSearch": "清除搜尋",
    "songsets.aria.search": "搜尋",
    "songsets.aria.pagination": "敬拜歌單分頁",
    "songsets.aria.previousPage": "上一頁",
    "songsets.aria.nextPage": "下一頁",
    "songsets.aria.page": "頁碼",
    "songsets.aria.createNewSongset": "建立新敬拜歌單",

    // Edit description dialog
    "songsets.dialog.editDescriptionTitle": "編輯描述",
    "songsets.dialog.editDescriptionDescription": "更新這個敬拜歌單的描述。",
    "songsets.label.description": "描述",
  },
});
