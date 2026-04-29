# Agent Instructions: Populate Songs with Audio and Lyrics

This document describes how to populate songs in the Stream of Worship catalog with audio files and LRC (lyrics timing) data.

## Prerequisites

- Admin CLI tool `sow_admin` is available via `uv run --extra admin sow-admin`
- Network access to YouTube for audio download
- R2 storage configured for file uploads

## Workflow

### Step 1: Find the Song ID

List songs from the catalog to find the target song's ID:

```bash
uv run --extra admin sow-admin catalog list | head -20
```

The output shows a table with columns: ID, Title, Composer, Album, Key.

Copy the **ID** value for the song you want to populate.

**Example output:**
```
Songs (581 total)
┏━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━━━━━━━━━━━━━━━━━━━┳━━━━━━━━┓
┃ ID                                                                    ┃ Title                                           ┃ Composer               ┃ Album                    ┃  Key   ┃
┡━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━━━━━━━━━━━━━━━━━━━╇━━━━━━━━┩
│ _bao_gui_shi_jia_han_wen_ban__11600e75                                │ 주의 십자가 [寶貴十架韓文版]                    │ 曾祥怡                 │ 寶貴十架                 │   A    │
│ _chai_qian_wo_ri_wen_ban__1f9c16d3                                    │ ここに います [差遣我日文版]                    │ 周巽倩                 │ G.L.O.W. 差遣我          │   G    │
│ he_deng_en_dian_20569b85                                              │ 何等恩典                                        │ 游智婷                 │ 讚美的孩子最喜樂         │   F    │
```

### Step 2: Download Audio and Submit LRC Job

Use the `audio download` command with the `--lrc` flag to:
1. Search YouTube for the song
2. Download the audio
3. Upload to R2 storage
4. Submit an LRC generation job

```bash
uv run --extra admin sow-admin audio download --lrc <SONG_ID> --yes
```

Replace `<SONG_ID>` with the ID from Step 1.

The `--yes` flag skips interactive confirmation.

**Example:**
```bash
uv run --extra admin sow-admin audio download --lrc _bao_gui_shi_jia_han_wen_ban__11600e75 --yes
```

**Expected output:**
```
Song: 주의 십자가 [寶貴十架韓文版]
Composer: 曾祥怡
Album: 寶貴十架
Search query: 주의 십자가 [寶貴十架韓文版] 曾祥怡 寶貴十架 官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美
Previewing video...
╭─────────────────────────────────────── Video Preview ────────────────────────────────────────╮
│ Title: 【寶貴十架 Precious Cross】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (11P) │
│ Duration: 4:15                                                                               │
│ URL: https://www.youtube.com/watch?v=0YJZUyVOQVY                                             │
╰──────────────────────────────────────────────────────────────────────────────────────────────╯
Downloading audio from YouTube...
Downloaded: 【寶貴十架 Precious Cross】官方歌詞版MV (Official Lyrics MV) - 讚美之泉敬拜讚美 (11P).mp3
File size: 5.83 MB
Hash prefix: 87d5300921f4
Uploading to R2...
Uploaded: s3://stream-of-worship/87d5300921f4/audio.mp3
Recording saved (hash_prefix: 87d5300921f4)
Submitting for LRC generation...
LRC job submitted (job: job_b5d2fbec1d28)
```

### Step 3: Check LRC Job Status

After submitting the LRC job, check its status using the `audio status` command with the job ID from Step 2:

```bash
uv run --extra admin sow-admin audio status <JOB_ID>
```

**Example:**
```bash
uv run --extra admin sow-admin audio status job_b5d2fbec1d28
```

**Expected output:**
```
╭───────────── Job: job_b5d2fbec1d28 ──────────────╮
│ Job ID: job_b5d2fbec1d28                         │
│ Type: lrc                                        │
│ Status: processing                               │
│ Stage: awaiting_stem_separation:job_4e67d1dac26e │
│ Progress: 20%                                    │
│ Created: 2026-04-28T17:31:11.276411Z             │
│ Updated: 2026-04-28T17:31:13.589926Z             │
╰──────────────────────────────────────────────────╯
```

**Status values:**
- `processing` - Job is still running
- `completed` - LRC generation finished successfully
- `failed` - Job encountered an error

Re-run the status command periodically until the status changes to `completed`. LRC generation typically takes several minutes depending on the analysis service load.

## Notes

- YouTube download warnings about "n challenge solving failed" are typically non-fatal and the download will still succeed
- The song is considered "populated" once the audio is uploaded and the LRC job is submitted
- LRC generation may take several minutes depending on the analysis service load
