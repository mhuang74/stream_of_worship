# Stream of Worship — User Guide

This guide is for worship leaders and their media volunteers. It covers **offline worship playback**: how to prepare a worship set on your device so it plays with no Wi-Fi at the meeting place, and how to start it when you arrive.

For installation, project setup, and architecture, see [README.md](README.md).

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
| A page shows "You are offline. Please reconnect." | That page was never opened online on this device | Navigate only through pages you've opened before (list → play page → controller) |
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
