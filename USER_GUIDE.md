# Stream of Worship — User Guide

This guide is for worship leaders and their media volunteers. It covers two topics: **editing a song's arrangement** — choosing which parts of each song a worship set plays — and **offline worship playback**, where a set prepared on your device plays with no Wi-Fi at the meeting place.

For installation, project setup, and architecture, see [README.md](README.md).

---

## Song Arrangement Editor

### What an arrangement is

- A **Song Arrangement** is the ordered list of parts of *one* song that the worship set plays. Each entry in that list is a **Step**: one use of a section the app detected in the recording — intro, verse, chorus, bridge, and so on.
- The list may **repeat, reorder, and omit** sections freely. A song you never arrange plays in full, straight through.
- Repeating a section adds Steps, not songs: the per-set cap of 5 songs is unaffected. Once a set is at the cap, the add-song button is replaced by a pill reading **5 songs maximum reached**（5 首詩歌已達上限）.
- An arrangement belongs to one song inside one songset. It is carried over by **Duplicate**（複製）, it survives sharing (a share link plays the songset's latest render), and it is **not editable in the Android app** — arrangement editing is web-only.
- Inside one song, Steps play back-to-back with **no gap and no fade**. Gaps and crossfades exist only **between** songs; see the **Transitions, key, and tempo** subsection below.

### Before you start

- The editor needs **section and beat analysis** on the recording. The song catalog only lists songs that have a published (or in-review) recording; if that recording's analysis is missing, the row's **Arrange**（編排）button is disabled and its tooltip reads **Recording analysis is unavailable, so a new arrangement cannot be created.**（錄音分析資料不可用，無法建立新編排。）
- Editing needs a signed-in session and a network connection: the preview streams the source audio, so it does not work offline.
- Recommended order: build the songset (up to 5 songs) → arrange the songs → render → download or play.

### Open the editor

1. Open **Songsets**（敬拜歌單）and pick a songset.
2. On any song row, tap **Arrange**（編排）. On phones the button renders as the list icon alone; from the `md` breakpoint up it shows the icon plus the **Arrange** label. It is the only row action always visible on mobile — the neighbouring **Gap：**（間隔：）transition control is desktop-only.

A row that already has an arrangement shows the **Arranged**（已編排）badge and the arranged duration next to the original duration. When the stored arrangement no longer matches the recording's analysis, the row shows **Duration unavailable**（無法計算長度）instead, and the editor opens only to explain the problem and offer **Reset**（重設）— see the **Save, cancel, and reset** subsection below.

The sheet is titled **Song arrangement**（歌曲編排）, and under the title it shows the song's name followed by the subtitle **Reorder, repeat, or omit this song's sections**（重新排序、重複或省略這首歌的段落）.

### Build the sequence

**The editor does not open empty.** A song you have already arranged opens with your saved Steps; a song you have never arranged opens with **every detected section already in the Sequence**, in recording order. Both lists are already snapped to beats, so the usual work is removing Steps and dragging them into order, not building the list from scratch. An empty Sequence with **No steps yet. Add sections from above.**（尚無步驟，請從上方加入段落。）means the stored arrangement could not be read or the recording's analysis is missing — or you removed every Step yourself; it never means the editor failed to prefill.

The sheet has four regions, in this order from the top:

1. **Snap notice** — **Boundaries snap to the nearest detected beat, then repeat or omit any section below.**（段落邊界會對齊到最接近的節拍，之後可在下方重複或省略任何段落。）
2. **Score bar** — the component count on the left (for example **8 components**（個段落）) with **n/64**（步驟）and a single **Add section**（加入段落）button on the right. The button is disabled while a save is in flight, when the recording has no analysable sections, and at 64 Steps. Opening it shows a picker listing every detected section: repeated labels are numbered in source order (`Chorus 1`, `Chorus 2`, …), each row shows that section's length (`0:25`) and how many copies are already in the arrangement (**in arrangement ×2**（編排中 ×2）). Choosing a row **appends** that section to the end of the sequence and closes the picker — you cannot insert in the middle. A section whose stored bounds fall outside the recording (or that collapses when snapped) still appears in the picker but its row is greyed out.
3. **Sequence**（編排順序）— the draft list, with the counter **Steps n/64**（步驟）. Each row has a drag handle, its position number, the section label, that Step's length (`32s`, `1m 5s`), a duplicate button, and a remove button. Drag rows to reorder — with mouse, touch, or keyboard.
4. **Duration box** — **Original**（原始長度）and **Arranged**（編排長度）, plus the note **Saving keeps only these sections; Reset restores the full recording.**（儲存後只保留這些段落；重設會還原完整錄音。）

Two validation messages appear in the sheet:

- Empty draft: **Add at least one step before previewing or saving.**（請至少加入一個步驟才能預覽或儲存。）
- More than 64 Steps: **A sequence can have at most 64 steps. Remove some steps before saving.**（編排最多可有 64 個步驟。請在儲存前移除部分步驟。）

Step boundaries are **snapped automatically** to the nearest detected beat. There is no field for typing a custom start or end time, and a Step cannot be trimmed shorter than the detected section.

### Preview the whole set

The **Whole-Songset preview**（整份敬拜歌單預覽）region, below the sequence, **Plays this draft together with the other songs' saved transitions.**（以這份草稿搭配其他詩歌已儲存的轉場進行播放。）Your unsaved draft is used for this song; every other song keeps its saved arrangement and transitions. The badge alongside shows **Total**（總長度）.

- Controls: **Play preview**（播放預覽）/ **Pause preview**（暫停預覽）, a stop button, a scrub slider, and one **Start here**（從這裡開始）button per song in the set, to jump straight to that song's start.
- Editing any Step stops the preview, because the mix no longer matches the draft.
- The preview is a **near-render approximation**: it applies the selected sections, the inter-song gaps, and the crossfades, but not the final loudness normalization or encoding — expect small level differences from the rendered file.
- If the device silently drops crossfades, the notice **Crossfades are approximated as hard boundaries on this device.**（此裝置上的交叉淡化會以硬切邊界近似。）appears.
- Starting a preview stops any song that was playing in the app's player.

Preview errors you may see:

- **This item has no selected recording, so the whole-Songset preview cannot play.**（此項目尚未選擇錄音，因此整份敬拜歌單的預覽無法播放。）
- **This item's saved arrangement is invalid or stale. Repair the recording analysis or reset the arrangement.**（此項目已儲存的編排無效或已過期。請修復錄音分析資料，或重設編排。）
- **Preview playback failed. Try again.**（預覽播放失敗，請再試一次。）

### Save, cancel, and reset

- **Save**（儲存）commits the sequence — the button shows **Saving...**（儲存中...）while it works — and shows the toast **Arrangement saved**（編排已儲存）. Saves are explicit: there is no autosave, and saving does not start a render.
- **Cancel**（取消）discards the draft. If you changed anything, you first get **Discard arrangement changes?**（要捨棄編排變更嗎？）with **Your edits to this arrangement have not been saved.**（這份編排的變更尚未儲存。）and the choices **Keep editing**（繼續編輯）/ **Discard**（捨棄）. Closing the sheet asks the same way.
- **Reset**（重設）deletes the custom sequence after the prompt **Reset arrangement?**（重設編排？）→ **This removes the custom sequence and restores the full original recording.**（這會移除自訂順序並還原完整的原始錄音。）with the confirm button **Reset arrangement**（重設編排）. Reset also works when the analysis is unavailable — use it to recover from a stale arrangement. A reset shows the same **Arrangement saved**（編排已儲存）toast, so read the row afterwards to confirm the **Arranged**（已編排）badge is gone.
- Saving or resetting an arrangement marks the songset **Needs re-render**（建議重新渲染）— see the **Render and play the arranged set** subsection below.

Save and reset errors you may see:

- **This sequence would exceed the Songset duration limit.**（此順序會超出敬拜歌單的長度限制。）
- **Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement.**（歌曲編排不可用或已過期；請修復錄音分析資料，或重設編排。）
- **Your session expired. Sign in again to save the arrangement.**（工作階段已過期，請重新登入以儲存編排。）
- **Failed to save the arrangement.**（無法儲存編排。）

Limits: **1 to 64 Steps per song**; the set stays capped at **5 songs** and at **25 minutes (1500 s) of arranged audio**. Sets over the duration cap show the header badge **Over 25 min**（超過 25 分鐘）and cannot be rendered until they are trimmed.

### Transitions, key, and tempo

Per-song transition settings live in the row's **Gap：**（間隔：）control, available for every song after the first: the gap in beats（拍）, optionally replaced by **Crossfade**（交叉淡入淡出）, plus key shift（移調）and tempo adjustment（速度）.

- Gaps and crossfades are applied **between songs only**; inside a song, Steps always join hard.
- Current limitation: **key shift and tempo adjustments are saved per transition but not applied by the renderer** — a rendered file plays each recording at its original key and tempo.

### Render and play the arranged set

- Render from the songset menu with **Render**（渲染）. The arrangement is baked into the output: the MP3/MP4, the chapters manifest, and the lyric video all follow the arranged order and timings.
- Saving or resetting an arrangement makes existing renders stale: the songset shows the badge **Needs re-render**（建議重新渲染）and the banner **Artifacts out of date**（影片可能有更新）/ **Songs have been modified since the last render.**（敬拜歌單在上次渲染後有修改，建議重新渲染。）with **Re-render**（重新渲染）and **Play anyway**（仍要播放）. The old render stays playable, and share links keep serving the latest render until a new one completes.
- When the render finishes (**Render completed**（已完成渲染）), play the set from the songset with **Play**（播放）→ **Start Worship**（開始敬拜）.
- A set already downloaded for offline use is stale after a re-render — download it again; see the **Offline Worship Playback** section below.

### Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| **Arrange**（編排）greyed out, tooltip "Recording analysis is unavailable, so a new arrangement cannot be created." | The recording has no usable section or beat analysis | Pick another recording or song; if a stale arrangement is blocking you, reset it |
| **Add section**（加入段落）greyed out with no sections in the picker | An arrangement is saved but the analysis behind it is gone, or every section collapses when snapped | **Reset**（重設）, or ask an admin to repair the analysis |
| **Play preview**（播放預覽）disabled next to "Add at least one step before previewing or saving." | The draft is empty | Add at least one section with **Add section**（加入段落） |
| "A sequence can have at most 64 steps. Remove some steps before saving." | All 64 Steps are used | Remove Steps before saving |
| Save fails with "Song arrangement is unavailable or stale; repair the Recording analysis or reset the arrangement." | The recording's analysis changed after the arrangement was saved | **Reset**（重設）and rebuild the sequence; report it to an admin if the analysis changed unexpectedly |
| Header badge **Over 25 min**（超過 25 分鐘） | Arranged audio totals more than 25 minutes (1500 s) | Shorten arrangements or remove a song |
| Row shows **Duration unavailable**（無法計算長度） | The stored arrangement no longer validates against the analysis | Open **Arrange**（編排）and reset |
| **Needs re-render**（建議重新渲染）after saving | Expected — the saved arrangement has no rendered audio yet | Render the set again |
| Preview sounds different from the rendered file | Expected near-render approximation (no loudness normalization) | Trust the rendered file |
| Crossfades are hard cuts during preview | Device audio fallback | Confirm the result in the rendered file |
| Render fails immediately with the repair-or-reset message | An arrangement no longer matches its recording's analysis | Reset that song's arrangement, then render again |

### Requirements and limits

- **Web app only** — the Android app does not edit arrangements. Editing and preview need a network connection.
- **1 to 64 Steps per song**, **up to 5 songs per set**, and **up to 25 minutes (1500 s) of arranged audio**.
- An arrangement applies to **renders created after saving**; existing renders are left untouched.
- Arrangements are carried over by **Duplicate**（複製）; changing a song's recording inside the set drops that song's arrangement.
- Step boundaries always come from detected sections snapped to detected beats — there is no manual time entry.

---

## Offline Worship Playback

### What offline playback gives you

Once a worship set is downloaded for offline use, the downloaded video (or audio-only file, when the set was rendered without video) plays right on your device — with lyrics, chapter markers, and seeking between songs — even with the network completely off. Everything else in the app keeps working online exactly as before.

### Works offline vs. needs a network

| Works offline | Needs a network |
|---|---|
| Playing the downloaded video or audio, with lyrics and chapter seeking | The first download of a set |
| The play card and the **Offline**（離線）badge on the songset list | Rendering or re-rendering a set |
| **Remove from offline**（移除離線副本） | Cast to TV and second-screen projection (the downloaded copy is local-only) |
| Pages you have already opened on this device (songset list, play page, controller) | Editing songsets, sharing links, search |
| | Pages this device has never opened online |

### Before the meeting — checklist

- [ ] Use the **same device and same browser** you will use at the meeting. The downloaded copy lives in this device's browser storage — it is not synced anywhere, and clearing the site's data deletes it.
- [ ] Sign in while you still have Wi-Fi.
- [ ] The set is **rendered** (audio MP3 and/or video MP4). The download button only appears once a render has completed.
- [ ] **Download for offline**（離線下載）finished: the button becomes an **Offline ready**（離線就緒）badge and you saw the "Downloaded for offline playback"（已下載，離線也能聽）toast.
- [ ] The songset list shows the **Offline**（離線）badge for the set — solid, not amber-tinted.
- [ ] Optional: do the airplane-mode soundcheck below before the day of the service.

### Step-by-step: prepare and start offline worship playback

1. **Render the set first, if you haven't.** Open the songset, choose **Render**（渲染）, and generate audio or video. Wait until you see the "Render completed"（已完成渲染）toast.
2. **Open the set's play page.** From the songset list, tap the set to reach the Start Worship screen. Below the song list, tap **Download for offline**（離線下載）. The button shows a percentage while downloading — keep the app open until it finishes (a worship video is hundreds of MB).
3. **Confirm the download.** The button becomes an **Offline ready**（離線就緒）badge, and the songset list row shows the **Offline**（離線）badge with a crossed-out Wi-Fi icon.
4. *(Optional)* **Turn on auto-download for future renders.** In Settings, **Auto-cache after render**（渲染後自動快取）is on by default. It downloads a set automatically when a render finishes — but only if the render page stays open until the render completes.
5. *(Optional)* **Soundcheck in airplane mode.** Turn on airplane mode on the device, open the app — a red **You are offline**（你目前離線）banner appears at the top — open the set, and tap **Start Worship**（開始敬拜）. If playback starts from the downloaded copy, you are ready.
6. **At the meeting:** open the app on the same device. The set's play page shows **Ready for offline playback**（離線播放已就緒）. Tap **Start Worship**（開始敬拜）.
7. **In the player**, the **Offline playback**（離線播放中）badge shows. Seek between songs with the chapter list or scrubber as usual.
8. **If playback fails or stalls** (an overlay appears after about 15 seconds), tap **Retry**（重試）. If it keeps failing, reconnect to Wi-Fi and download the set again.
9. **After the service,** free up storage: songset list → the set's **⋯** menu → **Remove from offline**（移除離線副本）.

### Troubleshooting

| What you see | What it means | What to do |
|---|---|---|
| No **Download for offline**（離線下載）button | The set has no completed render yet | Render audio or video first |
| **Update iOS for offline**（更新 iOS 才能離線使用） | The device runs iOS older than 17.4 | Update iOS, or use another device |
| **Offline caching not available**（這個瀏覽器不支援離線快取） | The browser doesn't support offline caching | Use a current Safari or Chrome over HTTPS (or localhost) |
| **Failed to download for offline**（下載失敗，請再試一次） | The download was interrupted or storage is full | Retry on a stable connection; free up device space |
| Download refuses at a size limit | The app caps offline storage at 1 GB (warns at 500 MB) | Remove other offline sets via their **⋯** menu |
| The **Offline**（離線）badge is amber | The set was re-rendered after you downloaded it | Download again — the old copy is removed automatically |
| **Start Worship** spins or errors while offline | The set was never downloaded **on this device** | Download it while online, or play online |
| A page shows "Unable to load this page" | That page was never opened online on this device — or the server couldn't be reached (on a dev host, possibly a TLS/certificate problem) | Navigate only through pages you've opened before (list → play page → controller); while online, open the pages you'll need offline |
| **Playback stopped**（播放已停止）or **Playback stalled**（播放卡住了）overlay | The media failed, or stalled over 15 seconds | Tap **Retry**（重試）; if it persists, reconnect and download the set again |
| Mid-service Wi-Fi drop | Nothing — playback continues from the downloaded copy ("The live version could not be loaded — playing the downloaded copy."／無法載入線上版本，改為播放已下載的副本。) | Nothing to do |

### Device requirements and limits

- **iOS 17.4 or later** is required for offline caching on iOS; all other platforms are supported.
- The app must be served over **HTTPS** (or localhost) — browsers only enable offline caching in secure contexts.
- A downloaded copy is **per device and per browser**; it is never synced. Clearing the site's data deletes it.
- Offline storage has a **500 MB warning** threshold and a **1 GB hard limit** across all downloaded sets.
- The copy is tied to **one render**: re-rendering the set makes the downloaded copy stale (amber badge), and it is evicted automatically when superseded.
- Auto-cache after render only fires when the **render page stays open** until the render completes — there is no server-side completion hook.
- **Cast to TV and second-screen projection are unavailable** while playing offline — the downloaded copy is local-only.
