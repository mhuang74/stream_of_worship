import { bundle } from "../messages";

// Song Arrangement Editor namespace: the editor sheet, the whole-Songset
// preview transport, the item-row Arrange action/"Arranged" badge, and the
// analysis-unavailable/render-repair messages (issue #286, spec
// specs/song_arrangement_editor.md §5). Recording section labels come from
// analysis data and stay verbatim. zh-Hant completeness is compile-enforced.

export const arrangementBundle = bundle({
  en: {
    // Row action and badge
    "arrangement.action.edit": "Arrange",
    "arrangement.badge.arranged": "Arranged",
    "arrangement.disabled.noAnalysis":
      "Recording analysis is unavailable, so a new arrangement cannot be created.",
    "arrangement.duration.unavailable": "Duration unavailable",

    // Sheet chrome
    "arrangement.sheet.title": "Song arrangement",
    "arrangement.sheet.description": "Reorder, repeat, or omit this song's sections",
    "arrangement.section.source": "Source sections",
    "arrangement.section.draft": "Sequence",
    "arrangement.section.sourceHint":
      "Add any section as many times as you like. Numbers distinguish repeated labels.",
    "arrangement.draft.count": "Steps",
    "arrangement.draft.empty": "No steps yet. Add sections from above.",
    "arrangement.palette.empty":
      "Recording analysis is unavailable, so there are no sections to arrange.",

    // Draft actions
    "arrangement.action.reset": "Reset",
    "arrangement.action.save": "Save",
    "arrangement.action.saving": "Saving...",
    "arrangement.action.cancel": "Cancel",
    "arrangement.action.addAria": "Add section",
    "arrangement.action.duplicateAria": "Duplicate step",
    "arrangement.action.removeAria": "Remove step",
    "arrangement.action.dragAria": "Reorder step",

    // Durations and notes
    "arrangement.duration.originalLabel": "Original",
    "arrangement.duration.arrangedLabel": "Arranged",
    "arrangement.note.saveBehavior":
      "Saving keeps only these sections; Reset restores the full recording.",
    "arrangement.note.snap":
      "Boundaries snap to the nearest detected beat, then repeat or omit any section below.",

    // Validation
    "arrangement.validation.empty": "Add at least one step before previewing or saving.",
    "arrangement.validation.tooMany":
      "A sequence can have at most {max} steps. Remove some steps before saving.",

    // Preview transport
    "arrangement.preview.title": "Whole-Songset preview",
    "arrangement.preview.description":
      "Plays this draft together with the other songs' saved transitions.",
    "arrangement.preview.loading": "Loading preview...",
    "arrangement.preview.play": "Play preview",
    "arrangement.preview.pause": "Pause preview",
    "arrangement.preview.stop": "Stop preview",
    "arrangement.preview.seekAria": "Preview position",
    "arrangement.preview.total": "Total",
    "arrangement.preview.startHere": "Start here",
    "arrangement.preview.startHereAria": "Start preview here",
    "arrangement.preview.fallbackNotice":
      "Crossfades are approximated as hard boundaries on this device.",

    // Dialogs
    "arrangement.dialog.resetTitle": "Reset arrangement?",
    "arrangement.dialog.resetDescription":
      "This removes the custom sequence and restores the full original recording.",
    "arrangement.dialog.resetConfirm": "Reset arrangement",
    "arrangement.dialog.dirtyTitle": "Discard arrangement changes?",
    "arrangement.dialog.dirtyDescription":
      "Your edits to this arrangement have not been saved.",
    "arrangement.dialog.keepEditing": "Keep editing",
    "arrangement.dialog.discard": "Discard",

    // Errors (save + preview); the stale/unavailable copy is the render-jobs
    // 409 repair-or-reset message.
    "arrangement.error.unavailableTitle": "Arrangement unavailable",
    "arrangement.error.unavailableOrStale":
      "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement.",
    "arrangement.error.noRecording":
      "This item has no selected recording, so the whole-Songset preview cannot play.",
    "arrangement.error.invalidArrangement":
      "This item's saved arrangement is invalid or stale. Repair the recording analysis or reset the arrangement.",
    "arrangement.error.playbackFailed": "Preview playback failed. Try again.",
    "arrangement.error.saveFailed": "Failed to save the arrangement.",
    "arrangement.error.tooLong":
      "This sequence would exceed the Songset duration limit.",
    "arrangement.error.unauthenticated":
      "Your session expired. Sign in again to save the arrangement.",

    // Toasts
    "arrangement.toast.saved": "Arrangement saved",
    "arrangement.toast.reset": "Arrangement reset",
  },
  "zh-Hant": {
    // Row action and badge
    "arrangement.action.edit": "編排",
    "arrangement.badge.arranged": "已編排",
    "arrangement.disabled.noAnalysis": "錄音分析資料不可用，無法建立新編排。",
    "arrangement.duration.unavailable": "無法計算長度",

    // Sheet chrome
    "arrangement.sheet.title": "歌曲編排",
    "arrangement.sheet.description": "重新排序、重複或省略這首歌的段落",
    "arrangement.section.source": "來源段落",
    "arrangement.section.draft": "編排順序",
    "arrangement.section.sourceHint": "任何段落都可以重複加入。數字用於區分重複的段落名稱。",
    "arrangement.draft.count": "步驟",
    "arrangement.draft.empty": "尚無步驟，請從上方加入段落。",
    "arrangement.palette.empty": "錄音分析資料不可用，沒有可編排的段落。",

    // Draft actions
    "arrangement.action.reset": "重設",
    "arrangement.action.save": "儲存",
    "arrangement.action.saving": "儲存中...",
    "arrangement.action.cancel": "取消",
    "arrangement.action.addAria": "加入段落",
    "arrangement.action.duplicateAria": "複製步驟",
    "arrangement.action.removeAria": "移除步驟",
    "arrangement.action.dragAria": "重新排序步驟",

    // Durations and notes
    "arrangement.duration.originalLabel": "原始長度",
    "arrangement.duration.arrangedLabel": "編排長度",
    "arrangement.note.saveBehavior": "儲存後只保留這些段落；重設會還原完整錄音。",
    "arrangement.note.snap": "段落邊界會對齊到最接近的節拍，之後可在下方重複或省略任何段落。",

    // Validation
    "arrangement.validation.empty": "請至少加入一個步驟才能預覽或儲存。",
    "arrangement.validation.tooMany": "編排最多可有 {max} 個步驟。請在儲存前移除部分步驟。",

    // Preview transport
    "arrangement.preview.title": "整份敬拜歌單預覽",
    "arrangement.preview.description": "以這份草稿搭配其他詩歌已儲存的轉場進行播放。",
    "arrangement.preview.loading": "正在載入預覽...",
    "arrangement.preview.play": "播放預覽",
    "arrangement.preview.pause": "暫停預覽",
    "arrangement.preview.stop": "停止預覽",
    "arrangement.preview.seekAria": "預覽播放位置",
    "arrangement.preview.total": "總長度",
    "arrangement.preview.startHere": "從這裡開始",
    "arrangement.preview.startHereAria": "從這裡開始預覽",
    "arrangement.preview.fallbackNotice": "此裝置上的交叉淡化會以硬切邊界近似。",

    // Dialogs
    "arrangement.dialog.resetTitle": "重設編排？",
    "arrangement.dialog.resetDescription": "這會移除自訂順序並還原完整的原始錄音。",
    "arrangement.dialog.resetConfirm": "重設編排",
    "arrangement.dialog.dirtyTitle": "要捨棄編排變更嗎？",
    "arrangement.dialog.dirtyDescription": "這份編排的變更尚未儲存。",
    "arrangement.dialog.keepEditing": "繼續編輯",
    "arrangement.dialog.discard": "捨棄",

    // Errors (save + preview); the stale/unavailable copy is the render-jobs
    // 409 repair-or-reset message.
    "arrangement.error.unavailableTitle": "編排不可用",
    "arrangement.error.unavailableOrStale":
      "歌曲編排不可用或已過期；請修復錄音分析資料，或重設編排。",
    "arrangement.error.noRecording":
      "此項目尚未選擇錄音，因此整份敬拜歌單的預覽無法播放。",
    "arrangement.error.invalidArrangement":
      "此項目已儲存的編排無效或已過期。請修復錄音分析資料，或重設編排。",
    "arrangement.error.playbackFailed": "預覽播放失敗，請再試一次。",
    "arrangement.error.saveFailed": "無法儲存編排。",
    "arrangement.error.tooLong": "此順序會超出敬拜歌單的長度限制。",
    "arrangement.error.unauthenticated": "工作階段已過期，請重新登入以儲存編排。",

    // Toasts
    "arrangement.toast.saved": "編排已儲存",
    "arrangement.toast.reset": "已重設編排",
  },
});
