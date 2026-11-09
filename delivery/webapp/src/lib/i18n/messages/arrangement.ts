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
    "arrangement.sheet.description":
      "Lyrics first: reorder, repeat, or omit this song's sections",
    "arrangement.section.draft": "Sequence",
    "arrangement.addDialog.title": "Add a component",
    "arrangement.addDialog.description":
      "Adds a copy at the end of the arrangement. Drag it into place afterward.",
    "arrangement.addDialog.rowAria": "Add {label}",
    "arrangement.addDialog.rowCount": "in arrangement ×{count}",
    "arrangement.draft.count": "Steps",
    "arrangement.draft.countLabel": "components",
    "arrangement.draft.empty":
      "No components left. Undo the last change, reset to the original song, or add a section to start again.",
    "arrangement.status.saved": "Saved",
    "arrangement.status.unsaved": "Unsaved changes",
    "arrangement.meta.key": "Key {key}",
    "arrangement.meta.tempo": "{bpm} BPM",

    // Draft actions
    "arrangement.action.reset": "Reset",
    "arrangement.action.save": "Save",
    "arrangement.action.saving": "Saving...",
    "arrangement.action.cancel": "Cancel",
    "arrangement.action.addSection": "Add section",
    "arrangement.action.duplicateAria": "Duplicate step",
    "arrangement.action.removeAria": "Remove step",
    "arrangement.action.dragAria": "Reorder step",
    "arrangement.action.undo": "Undo",
    "arrangement.action.undoAria": "Undo last change",
    "arrangement.action.playSection": "Play section",
    "arrangement.action.pauseSection": "Pause section",
    "arrangement.action.playSectionAria": "Play section",
    "arrangement.action.pauseSectionAria": "Pause section",

    // Lyrics
    "arrangement.lyrics.loading": "Loading lyrics...",
    "arrangement.lyrics.unavailable": "Lyrics unavailable for this song.",
    "arrangement.lyrics.instrumental":
      "Instrumental — no lyrics in this section.",

    // Durations and notes
    "arrangement.duration.originalLabel": "Original",
    "arrangement.duration.arrangedLabel": "Arranged",
    "arrangement.note.saveBehavior":
      "Saving keeps only these sections; Reset restores the full recording.",
    "arrangement.note.snap":
      "Boundaries snap to the nearest detected beat, then repeat or omit any section below.",
    "arrangement.note.reorderHint":
      "Drag a component's handle to reorder. Deleting a component is undoable.",

    // Validation
    "arrangement.validation.empty": "Add at least one step before previewing or saving.",
    "arrangement.validation.tooMany":
      "A sequence can have at most {max} steps. Remove some steps before saving.",

    // Preview transport (song-scoped: this song plus an optional lead-in from
    // the preceding songset item; there is no whole-Songset preview).
    "arrangement.preview.title": "Preview this song",
    "arrangement.preview.description":
      "Hear how “{song}” hands over into your first component.",
    "arrangement.preview.loading": "Loading preview...",
    "arrangement.preview.play": "Play preview",
    "arrangement.preview.pause": "Pause preview",
    "arrangement.preview.stop": "Stop preview",
    "arrangement.preview.fallbackNotice":
      "Crossfades are approximated as hard boundaries on this device.",
    "arrangement.preview.leadIn": "Lead-in",
    "arrangement.preview.leadInAria": "Lead-in seconds",
    "arrangement.preview.seconds": "{seconds}s",
    "arrangement.preview.leadInSummary":
      "Includes {seconds} of “{song}” before the first component.",
    "arrangement.preview.leadInFirst":
      "First song in this set, so there is no previous song to lead in from.",
    "arrangement.preview.leadInUnavailable":
      "There is no room for a lead-in before this song.",
    "arrangement.preview.phaseLeadIn": "Lead-in: {song}",
    "arrangement.preview.phasePlaying": "Playing {label}",
    "arrangement.preview.phasePaused": "Paused",
    "arrangement.preview.timelineAria":
      "Preview timeline: the lead-in, then each component in order",

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
      "This item has no selected recording, so the song preview cannot play.",
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
    "arrangement.sheet.description": "以歌詞為主：重新排序、重複或省略這首歌的段落",
    "arrangement.section.draft": "編排順序",
    "arrangement.addDialog.title": "加入段落",
    "arrangement.addDialog.description": "會在編排結尾加入一個副本，之後再拖曳到指定位置。",
    "arrangement.addDialog.rowAria": "加入 {label}",
    "arrangement.addDialog.rowCount": "編排中 ×{count}",
    "arrangement.draft.count": "步驟",
    "arrangement.draft.countLabel": "個段落",
    "arrangement.draft.empty":
      "已沒有段落。可以復原上一個變更、重設為原始歌曲，或加入段落重新開始。",
    "arrangement.status.saved": "已儲存",
    "arrangement.status.unsaved": "有未儲存的變更",
    "arrangement.meta.key": "調性 {key}",
    "arrangement.meta.tempo": "{bpm} BPM",

    // Draft actions
    "arrangement.action.reset": "重設",
    "arrangement.action.save": "儲存",
    "arrangement.action.saving": "儲存中...",
    "arrangement.action.cancel": "取消",
    "arrangement.action.addSection": "加入段落",
    "arrangement.action.duplicateAria": "複製步驟",
    "arrangement.action.removeAria": "移除步驟",
    "arrangement.action.dragAria": "重新排序步驟",
    "arrangement.action.undo": "復原",
    "arrangement.action.undoAria": "復原上一個變更",
    "arrangement.action.playSection": "播放段落",
    "arrangement.action.pauseSection": "暫停段落",
    "arrangement.action.playSectionAria": "播放段落",
    "arrangement.action.pauseSectionAria": "暫停段落",

    // Lyrics
    "arrangement.lyrics.loading": "正在載入歌詞...",
    "arrangement.lyrics.unavailable": "這首歌沒有可用歌詞。",
    "arrangement.lyrics.instrumental": "純音樂 — 此段落沒有歌詞。",

    // Durations and notes
    "arrangement.duration.originalLabel": "原始長度",
    "arrangement.duration.arrangedLabel": "編排長度",
    "arrangement.note.saveBehavior": "儲存後只保留這些段落；重設會還原完整錄音。",
    "arrangement.note.snap": "段落邊界會對齊到最接近的節拍，之後可在下方重複或省略任何段落。",
    "arrangement.note.reorderHint": "拖曳段落的把手即可調整順序；刪除段落後可以復原。",

    // Validation
    "arrangement.validation.empty": "請至少加入一個步驟才能預覽或儲存。",
    "arrangement.validation.tooMany": "編排最多可有 {max} 個步驟。請在儲存前移除部分步驟。",

    // Preview transport (song-scoped: this song plus an optional lead-in from
    // the preceding songset item; there is no whole-Songset preview).
    "arrangement.preview.title": "預覽這首歌",
    "arrangement.preview.description": "聽聽「{song}」如何交接到第一個段落。",
    "arrangement.preview.loading": "正在載入預覽...",
    "arrangement.preview.play": "播放預覽",
    "arrangement.preview.pause": "暫停預覽",
    "arrangement.preview.stop": "停止預覽",
    "arrangement.preview.fallbackNotice": "此裝置上的交叉淡化會以硬切邊界近似。",
    "arrangement.preview.leadIn": "前導",
    "arrangement.preview.leadInAria": "前導秒數",
    "arrangement.preview.seconds": "{seconds} 秒",
    "arrangement.preview.leadInSummary": "第一個段落前會先播放「{song}」的 {seconds}。",
    "arrangement.preview.leadInFirst": "這是歌單的第一首歌，沒有前一處可作為前導。",
    "arrangement.preview.leadInUnavailable": "這首歌之前沒有可播放的前導空間。",
    "arrangement.preview.phaseLeadIn": "前導：{song}",
    "arrangement.preview.phasePlaying": "正在播放 {label}",
    "arrangement.preview.phasePaused": "已暫停",
    "arrangement.preview.timelineAria": "預覽時間軸：前導，接著是每個段落",

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
    "arrangement.error.noRecording": "此項目尚未選擇錄音，因此無法播放歌曲預覽。",
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
