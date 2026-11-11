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

    // Preview transport: the rail previews the whole arrangement once (every
    // component plus an always-included 8-beat lead-in from the previous item
    // and 8-beat lead-out into the next); a component row reviews only the
    // 4 beats each side of its junction, twice.
    "arrangement.preview.title": "Preview this song",
    "arrangement.preview.loading": "Loading preview...",
    "arrangement.preview.loadingProgress": "Loading preview... {percent}%",
    "arrangement.preview.play": "Preview",
    "arrangement.preview.pause": "Pause preview",
    "arrangement.preview.stop": "Stop preview",
    "arrangement.preview.timelineAria":
      "Arrangement preview timeline: lead-in, all components, lead-out",
    "arrangement.preview.reviewTransition": "Review transition",
    "arrangement.preview.reviewTransitionAria":
      "Review {fromLabel} to {toLabel}: {beats} beats each side, played twice",
    "arrangement.preview.pass": "Pass {current} of 2",
    "arrangement.preview.completed": "Review complete · 2 passes",
    "arrangement.preview.pairSummary": "{fromLabel} → {toLabel} · 2 passes",
    "arrangement.preview.reviewWindow":
      "Transition window: {beats} beats before → {beats} beats after the join",
    "arrangement.preview.arrangementSummary": "Whole arrangement · {count} components",
    "arrangement.preview.nextOpening": "Next: {song} · Opening — first {seconds} s",
    "arrangement.preview.handover": "{fromSong} · {fromLabel} → {toSong} · {toLabel}",
    "arrangement.preview.opening": "Opening",
    "arrangement.preview.fullSong": "Full song",
    "arrangement.preview.playingAt": "Playing: {song} · {label}",
    "arrangement.preview.pausedAt": "Paused: {song} · {label}",
    "arrangement.preview.crossfade":
      "Crossfade: {fromSong} · {fromLabel} → {toSong} · {toLabel}",

    // Bounds editing (a temporary Apply/Cancel transaction).
    "arrangement.bounds.edited": "Edited",
    "arrangement.bounds.apply": "Apply bounds",
    "arrangement.bounds.cancel": "Cancel",
    "arrangement.bounds.invalid":
      "Use m:ss.s, keep start before end, and stay within the recording.",
    "arrangement.bounds.auditionNote":
      "Play 3 seconds inside the candidate boundary. Apply or Cancel before using transition review.",
    "arrangement.bounds.rangeAria": "Adjust {label} time range",
    "arrangement.bounds.start": "Start",
    "arrangement.bounds.end": "End",
    "arrangement.bounds.startAria": "Start timecode for {label}",
    "arrangement.bounds.endAria": "End timecode for {label}",
    "arrangement.bounds.fixedStart": "Start fixed at 0:00.0",
    "arrangement.bounds.decrease": "Decrease by 0.1 seconds",
    "arrangement.bounds.increase": "Increase by 0.1 seconds",
    "arrangement.bounds.auditionStart": "Audition start",
    "arrangement.bounds.auditionEnd": "Audition end",
    "arrangement.bounds.resetDetected": "Reset to detected",

    // Inferred Intro (from the first sung LRC line).
    "arrangement.intro.inferred": "Intro (inferred from lyrics)",
    "arrangement.intro.inferredShort": "Intro (inferred)",
    "arrangement.intro.firstLyric": "0:00.0–first lyric at {timecode}",
    "arrangement.intro.reset": "Reset to inferred",

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

    // Preview transport: the rail previews the whole arrangement once (every
    // component plus an always-included 8-beat lead-in from the previous item
    // and 8-beat lead-out into the next); a component row reviews only the
    // 4 beats each side of its junction, twice.
    "arrangement.preview.title": "預覽這首歌",
    "arrangement.preview.loading": "正在載入預覽…",
    "arrangement.preview.loadingProgress": "正在載入預覽…{percent}%",
    "arrangement.preview.play": "預覽",
    "arrangement.preview.pause": "暫停預覽",
    "arrangement.preview.stop": "停止預覽",
    "arrangement.preview.timelineAria": "編排預覽時間軸：前一首尾段、所有段落、下一首開頭",
    "arrangement.preview.reviewTransition": "交接預覽",
    "arrangement.preview.reviewTransitionAria": "預覽「{fromLabel}」到「{toLabel}」的交接：前後各 {beats} 拍，播放兩次",
    "arrangement.preview.pass": "第 {current} 次，共 2 次",
    "arrangement.preview.completed": "預覽完成，共 2 次",
    "arrangement.preview.pairSummary": "{fromLabel} → {toLabel}，共 2 次",
    "arrangement.preview.reviewWindow": "交接範圍：交接前 {beats} 拍 → 交接後 {beats} 拍",
    "arrangement.preview.arrangementSummary": "整首編排・{count} 個段落",
    "arrangement.preview.nextOpening": "下一首：{song}・開頭，播放 {seconds} 秒",
    "arrangement.preview.handover": "{fromSong}・{fromLabel} → {toSong}・{toLabel}",
    "arrangement.preview.opening": "開頭",
    "arrangement.preview.fullSong": "整首歌",
    "arrangement.preview.playingAt": "正在播放：{song}・{label}",
    "arrangement.preview.pausedAt": "已暫停：{song}・{label}",
    "arrangement.preview.crossfade": "交叉淡化：{fromSong}・{fromLabel} → {toSong}・{toLabel}",

    // Bounds editing (a temporary Apply/Cancel transaction).
    "arrangement.bounds.edited": "已調整",
    "arrangement.bounds.apply": "套用時間",
    "arrangement.bounds.cancel": "取消",
    "arrangement.bounds.invalid":
      "請使用 分:秒.十分之一秒 格式，開始須早於結束，且不得超出錄音範圍。",
    "arrangement.bounds.auditionNote":
      "試聽候選邊界內的 3 秒。套用或取消後才能使用交接預覽。",
    "arrangement.bounds.rangeAria": "調整「{label}」時間範圍",
    "arrangement.bounds.start": "開始",
    "arrangement.bounds.end": "結束",
    "arrangement.bounds.startAria": "「{label}」開始時間",
    "arrangement.bounds.endAria": "「{label}」結束時間",
    "arrangement.bounds.fixedStart": "開始固定為 0:00.0",
    "arrangement.bounds.decrease": "減少 0.1 秒",
    "arrangement.bounds.increase": "增加 0.1 秒",
    "arrangement.bounds.auditionStart": "試聽開始處",
    "arrangement.bounds.auditionEnd": "試聽結束處",
    "arrangement.bounds.resetDetected": "還原偵測值",

    // Inferred Intro (from the first sung LRC line).
    "arrangement.intro.inferred": "前奏（根據歌詞推斷）",
    "arrangement.intro.inferredShort": "前奏（推斷）",
    "arrangement.intro.firstLyric": "0:00.0 至第一句歌詞 {timecode}",
    "arrangement.intro.reset": "還原推斷值",

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
