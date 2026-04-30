# MVSEP API Reference

The MVSEP API provides programmatic access to create audio separations and return results. An **API key** (`api_token`) is required.

Premium users can request up to **10 concurrent jobs**; non-Premium users are limited to **1 concurrent job**.

---

## Create Separation

`POST https://mvsep.com/api/separation/create`

### Parameters

| Field | Type | Description |
|---|---|---|
| `api_token` | String | Your API key |
| `audiofile` | Binary | Audio file to be processed |
| `sep_type` | Integer | (optional) Separation type ID, defaults to `20`. See table below. |
| `add_opt1` | String | (optional) First additional field (model type, output files, etc.) |
| `add_opt2` | String | (optional) Second additional field (aggressiveness, how to extract, etc.) |
| `output_format` | Integer | (optional) Output format, defaults to `0` (mp3 320kbps). See table below. |
| `is_demo` | Boolean | (optional) Publish to demo page, defaults to `false` |

### Separation Types (`sep_type`)

| Name | Value |
|---|---|
| Ensemble (vocals, instrum) | 26 |
| Ensemble (vocals, instrum, bass, drums, other) | 28 |
| Ensemble All-In (vocals, bass, drums, piano, guitar, lead/back vocals, other) | 30 |
| BS Roformer SW (vocals, bass, drums, guitar, piano, other) | 63 |
| Demucs4 HT (vocals, drums, bass, other) | 20 |
| BS Roformer (vocals, instrumental) | 40 |
| MelBand Roformer (vocals, instrumental) | 48 |
| MDX23C (vocals, instrumental) | 25 |
| SCNet (vocals, instrumental) | 46 |
| MDX B (vocals, instrumental) | 23 |
| Ultimate Vocal Remover VR (vocals, music) | 9 |
| Demucs4 Vocals 2023 (vocals, instrum) | 27 |
| MVSep Karaoke (lead/back vocals) | 49 |
| MDX-B Karaoke (lead/back vocals) | 12 |
| MVSep Crowd removal (crowd, other) | 34 |
| Medley Vox (Multi-singer separation) | 53 |
| MVSep Multichannel BS (vocals, instrumental) | 43 |
| MVSep Male/Female separation | 57 |
| MVSep Choir (choir, other) | 112 |
| MVSep SATB Choir (soprano, alto, tenor, bass) | 111 |
| MVSep Drums (drums, other) | 44 |
| MVSep Bass (bass, other) | 41 |
| MVSep Synth (synth, other) | 88 |
| DrumSep (4-6 stems: kick, snare, cymbals, toms, ride, hh, crash) | 37 |
| MVSep Piano (piano, other) | 29 |
| MVSep Digital Piano (digital-piano, other) | 79 |
| MVSep Keys (keys, other) | 106 |
| MVSep Organ (organ, other) | 58 |
| MVSep Harpsichord (harpsichord, other) | 91 |
| MVSep Accordion (accordion, other) | 99 |
| MVSep Guitar (guitar, other) | 31 |
| MVSep Acoustic Guitar (acoustic-guitar, other) | 66 |
| MVSep Electric Guitar (electric-guitar, other) | 81 |
| MVSep Lead/Rhythm Guitar (lead-guitar, rhythm-guitar) | 101 |
| MVSep Plucked Strings (plucked-strings, other) | 102 |
| MVSep Harp (harp, other) | 72 |
| MVSep Mandolin (mandolin, other) | 74 |
| MVSep Banjo (banjo, other) | 83 |
| MVSep Sitar (sitar, other) | 90 |
| MVSep Ukulele (ukulele, other) | 96 |
| MVSep Dobro (dobro, other) | 97 |
| MVSep Bowed Strings (strings, other) | 52 |
| MVSep Violin (violin, other) | 65 |
| MVSep Viola (viola, other) | 69 |
| MVSep Cello (cello, other) | 70 |
| MVSep Double Bass (double-bass, other) | 73 |
| MVSep Wind (wind, other) | 54 |
| MVSep Brass (brass, other) | 107 |
| MVSep Woodwind (woodwind, other) | 108 |
| MVSep Saxophone (saxophone, other) | 61 |
| MVSep Flute (flute, other) | 67 |
| MVSep Trumpet (trumpet, other) | 71 |
| MVSep Trombone (trombone, other) | 75 |
| MVSep Oboe (oboe, other) | 77 |
| MVSep Clarinet (clarinet, other) | 78 |
| MVSep French Horn (french-horn, other) | 82 |
| MVSep Harmonica (harmonica, other) | 87 |
| MVSep Tuba (tuba, other) | 92 |
| MVSep Bassoon (bassoon, other) | 93 |
| MVSep Bagpipes (bagpipes, other) | 116 |
| MVSep Percussion (percussion, other) | 105 |
| MVSep Tambourine (tambourine, other) | 76 |
| MVSep Marimba (marimba, other) | 84 |
| MVSep Glockenspiel (glockenspiel, other) | 85 |
| MVSep Timpani (timpani, other) | 86 |
| MVSep Triangle (triangle, other) | 89 |
| MVSep Congas (congas, other) | 94 |
| MVSep Bells (bells, other) | 95 |
| MVSep Wind Chimes (wind-chimes, other) | 98 |
| MVSep Xylophone (xylophone, other) | 109 |
| MVSep Celesta (celesta, other) | 110 |
| MVSep Demucs4HT DNR (speech, music, effects) | 24 |
| BandIt Plus (speech, music, effects) | 36 |
| BandIt v2 (speech, music, effects) | 45 |
| MVSep DnR v3 (speech, music, effects) | 56 |
| MVSep Braam (braam, other) | 117 |
| MVSep FX (fx, other) | 122 |
| Apollo Enhancers (by JusperLee, Lew, baicai1145) | 51 |
| Reverb Removal (noreverb) | 22 |
| DeNoise by aufr33 and gabox | 47 |
| AudioSR (Super Resolution) | 59 |
| FlashSR (Super Resolution) | 60 |
| Stable Audio Open Gen | 62 |
| Whisper (extract text from audio) | 39 |
| Parakeet (extract text from audio) | 64 |
| VibeVoice (Voice Cloning) | 103 |
| VibeVoice (TTS) | 104 |
| Qwen3-TTS (Custom Voice) | 118 |
| Qwen3-TTS (Voice Design) | 119 |
| Qwen3-TTS (Voice Cloning) | 120 |
| Bark (Speech Gen) | 115 |
| MVSep MultiSpeaker (MDX23C) | 42 |
| Aspiration (by Sucial) | 50 |
| Phantom Centre extraction | 55 |
| Matchering (by sergree) | 68 |
| SOME (Singing-Oriented MIDI Extractor) | 80 |
| Transkun (piano -> midi) | 113 |
| Basic Pitch (MIDI Extraction) | 114 |
| HeartMuLa (Song Gen) | 121 |
| Demucs3 Model (vocals, drums, bass, other) | 10 |
| MDX A/B (vocals, drums, bass, other) | 7 |
| Vit Large 23 (vocals, instrum) | 33 |
| UVRv5 Demucs (vocals, music) | 17 |
| MVSep DNR (music, sfx, speech) | 18 |
| MVSep Old Vocal Model (vocals, music) | 19 |
| Demucs2 (vocals, drums, bass, other) | 13 |
| Danna Sep (vocals, drums, bass, other) | 15 |
| Byte Dance (vocals, drums, bass, other) | 16 |
| MVSep MelBand Roformer (vocals, instrum) | 35 |
| spleeter | 0 |
| UnMix | 3 |
| Zero Shot (Query Based) (Low quality) | 14 |
| LarsNet (kick, snare, cymbals, toms, hihat) | 38 |

### Additional Fields (`add_opt1`, `add_opt2`)

These fields are algorithm-specific. Below is a reference organized by algorithm.

#### Ultimate Vocal Remover VR (vocals, music) — `sep_type=9`

- **add_opt1** — Model Type:
  - `0` — HP2-4BAND-3090_4band_arch-500m_1
  - `1` — HP2-4BAND-3090_4band_2
  - `2` — HP2-4BAND-3090_4band_1
  - `3` — HP_4BAND_3090
  - `4` — Vocal_HP_4BAND_3090
  - `5` — Vocal_HP_4BAND_3090_AGG
  - `6` — HP2-MAIN-MSB2-3BAND-3090
  - `7` — HP-4BAND-V2
  - `8` — HP-KAROKEE-MSB2-3BAND-3090 (Karaokee model)
  - `9` — WIP-Piano-4band-129605kb (Piano model)
  - `10` — drums-4BAND-3090_4band (Drums model)
  - `11` — bass-4BAND-3090_4band (Bass model)
  - `12` — karokee_4band_v2_sn (Karaokee model v2)
  - `13` — UVR-De-Echo-Aggressive
  - `14` — UVR-De-Echo-Normal
  - `15` — UVR-DeNoise
  - `16` — UVR-DeEcho-DeReverb
  - `17` — UVR-BVE-4B_SN-44100-1 (Back vocals model)
- **add_opt2** — Aggressiveness: `0.1`, `0.2`, `0.3`, `0.4`, `0.5`, `0.6`, `0.7`, `0.8`, `0.9`, `1.0`

#### UVRv5 Demucs (vocals, music) — `sep_type=17`

- **add_opt1** — Model Type:
  - `0` — UVR_Demucs_Model_1
  - `1` — UVR_Demucs_Model_2
  - `2` — UVR_Demucs_Model_Bag

#### MDX A/B (vocals, drums, bass, other) — `sep_type=7`

- **add_opt1** — Vocal model type:
  - `0` — MDX A (Contest Version)
  - `3` — MDX Kimberley Jensen 2023.02.12 SDR: 9.30 (New)
  - `1` — MDX UVR 2022.01.01 SDR 8.62
  - `2` — MDX UVR 2022.07.25 SDR 8.51

#### Zero Shot (Query Based) — `sep_type=14`

- **add_opt1** — Model Type:
  - `0` — Bass (MUSDB18HQ AVG)
  - `1` — Drums (MUSDB18HQ AVG)
  - `2` — Vocals (MUSDB18HQ AVG)
  - `3` — Other (MUSDB18HQ AVG)

#### Demucs4 HT (vocals, drums, bass, other) — `sep_type=20`

- **add_opt1** — Model type:
  - `0` — htdemucs_ft (High Quality, Slow)
  - `1` — htdemucs (Good Quality, Fast)
  - `2` — htdemucs_6s (6 stems, additional piano and guitar)

#### MDX B (vocals, instrumental) — `sep_type=23`

- **add_opt1** — Vocal model type:
  - `7` — MDX Kimberley Jensen v2 2023.05.21 (SDR: 9.60)
  - `0` — MDX UVR 2022.01.01 (SDR: 8.83)
  - `1` — MDX UVR 2022.07.25 (SDR: 8.67)
  - `2` — MDX Kimberley Jensen v1 2023.02.12 (SDR: 9.48)
  - `4` — UVR-MDX-NET-Inst_HQ_2 (SDR: 9.12)
  - `5` — UVR_MDXNET_Main (SDR: 8.79)
  - `6` — MDX Kimberley Jensen Inst (SDR: 9.28)
  - `8` — UVR-MDX-NET-Inst_HQ_3 (SDR: 9.38)
  - `9` — UVR-MDX-NET-Voc_FT (SDR: 9.64)
  - `11` — UVR-MDX-NET-Inst_HQ_4 (SDR: 9.71)
  - `12` — UVR-MDX-NET-Inst_HQ_5 (SDR: 9.45)

#### MVSep Demucs4HT DNR (speech, music, effects) — `sep_type=24`

- **add_opt1** — Model type:
  - `0` — Single (SDR: 9.62)
  - `1` — Ensemble (SDR: 10.16)

#### MDX23C (vocals, instrumental) — `sep_type=25`

- **add_opt1** — Vocal model type:
  - `3` — 12K FFT, Large Conv, Hop 1024 (SDR vocals: 9.95, SDR instrum: 16.26)
  - `2` — 12K FFT, Large Conv (SDR vocals: 9.71, SDR instrum: 16.02)
  - `0` — 12K FFT (SDR vocals: 9.68, SDR instrum: 15.99)
  - `1` — 12K FFT, 6 Poolings (SDR vocals: 9.49, SDR instrum: 15.79)
  - `4` — 8K FFT (SDR vocals: 10.17, SDR instrum: 16.48)
  - `7` — 8K FFT (SDR vocals: 10.36, SDR instrum: 16.66)

#### Ensemble (vocals, instrum) — `sep_type=26`

- **add_opt1** — Output files:
  - `0` — Standard set
  - `1` — Include intermediate results and max_fft, min_fft
- **add_opt2** — Model Type:
  - `1` — SDR Vocals 10.44 (MDX23C, VitLarge23, Demucs4HT)
  - `2` — SDR Vocals 10.75 (MDX23C, BS Roformer v1, VitLarge23)
  - `3` — SDR Vocals 11.06 (MDX23C, BS Roformer viperx)
  - `4` — SDR Vocals 11.33 (MDX23C, BS Roformer finetuned)
  - `5` — SDR Vocals 11.50 (Mel Roformer and BS Roformer)
  - `6` — SDR Vocals 11.61 (Mel Roformer, BS Roformer and SCNet XL)
  - `7` — SDR Vocals 11.93 (Mel Roformer, BS Roformer x2 and SCNet XL IHF)
  - `8` — High Vocal Fullness (SDR: 11.69, Fullness: 20.46)
  - `9` — High Instrumental Fullness (SDR: 17.69, Fullness: 34.79)

#### Ensemble (vocals, instrum, bass, drums, other) — `sep_type=28`

- **add_opt1** — Output files:
  - `0` — Standard set
  - `1` — Include intermediate results and max_fft, min_fft
- **add_opt2** — Model Type:
  - `1` — SDR avg: 11.21 (v. 2023.09.01)
  - `2` — SDR avg: 11.87 (v. 2024.03.08)
  - `3` — SDR avg: 12.03 (v. 2024.03.28)
  - `4` — SDR avg: 12.17 (v. 2024.04.04)
  - `5` — SDR avg: 12.34 (v. 2024.05.21)
  - `6` — SDR avg: 12.66 (v. 2024.07.14)
  - `7` — SDR avg: 12.76 (v. 2024.08.15)
  - `8` — SDR avg: 12.84 (v. 2024.10.08)
  - `9` — SDR avg: 13.01 (v. 2024.12.20)
  - `10` — SDR avg: 13.07 (v. 2024.12.28)
  - `11` — SDR avg: 13.67 (v. 2025.06.30)

#### Ensemble All-In — `sep_type=30`

- **add_opt1** — Output files:
  - `0` — Standard set
  - `1` — Include intermediate results and max_fft, min_fft
- **add_opt2** — Model Type:
  - `1` — SDR avg: 11.21 (v. 2023.09.01)
  - `2` — SDR avg: 11.87 (v. 2024.03.08)
  - `3` — SDR avg: 12.03 (v. 2024.03.28)
  - `4` — SDR avg: 12.17 (v. 2024.04.04)
  - `5` — SDR avg: 12.32 (v. 2024.05.21)
  - `6` — SDR avg: 12.66 (v. 2024.07.14)
  - `7` — SDR avg: 12.76 (v. 2024.08.15)
  - `8` — SDR avg: 12.84 (v. 2024.10.08)
  - `9` — SDR avg: 13.01 (v. 2024.12.20)
  - `10` — SDR avg: 13.07 (v. 2024.12.28)
  - `11` — SDR avg: 13.67 (v. 2025.06.30)

#### BS Roformer (vocals, instrumental) — `sep_type=40`

- **add_opt1** — Vocal model type:
  - `3` — ver. 2024.02 (SDR vocals: 10.42, SDR instrum: 16.73)
  - `4` — viperx edition (SDR vocals: 10.87, SDR instrum: 17.17)
  - `5` — ver 2024.04 (SDR vocals: 11.24, SDR instrum: 17.55)
  - `29` — ver 2024.08 (SDR vocals: 11.31, SDR instrum: 17.62)
  - `85` — unwa high instrum fullness (SDR instrum: 17.25)
  - `142` — unwa BS Roformer HyperACE v2 instrum (SDR instrum: 17.40)
  - `143` — unwa BS Roformer HyperACE v2 vocals (SDR vocals: 11.39)
  - `81` — ver 2025.07 (SDR vocals: 11.89, SDR instrum: 18.20)

#### MelBand Roformer (vocals, instrumental) — `sep_type=48`

- **add_opt1** — Vocal model type:
  - `0` — Kimberley Jensen edition (SDR vocals: 11.01, SDR instrum: 17.32)
  - `1` — ver 2024.08 (SDR vocals: 11.17, SDR instrum: 17.48)
  - `2` — Bas Curtiz edition (SDR vocals: 11.18, SDR instrum: 17.49)
  - `3` — unwa Instrumental v1 (SDR vocals: 10.24, SDR instrum: 16.54)
  - `5` — unwa Instrumental v1e (SDR vocals: 10.05, SDR instrum: 16.36)
  - `6` — unwa big beta v5e (SDR vocals: 10.59, SDR instrum: 16.89)
  - `4` — ver 2024.10 (SDR vocals: 11.28, SDR instrum: 17.59)
  - `7` — becruily instrum high fullness (SDR instrum: 16.47)
  - `8` — becruily vocals high fullness (SDR vocals: 10.55)
  - `9` — unwa Instrumental v1e plus (SDR vocals: 10.33, SDR instrum: 16.64)
  - `10` — gabox Instrumental v7 (SDR vocals: 10.32, SDR instrum: 16.63)
  - `11` — becruily deux (SDR vocals: 11.35, SDR instrum: 17.66)
  - `12` — gabox v10 flowers (SDR vocals: 10.67, SDR instrum: 16.97)

#### SCNet (vocals, instrumental) — `sep_type=46`

- **add_opt1** — Vocal model type:
  - `0` — SCNet (SDR vocals: 10.25, SDR instrum: 16.56)
  - `1` — SCNet Large (SDR vocals: 10.74, SDR instrum: 17.05)
  - `2` — SCNet XL (SDR vocals: 10.96, SDR instrum: 17.27)
  - `3` — SCNet XL (high fullness)
  - `4` — SCNet XL (very high fullness)
  - `5` — SCNet XL IHF (SDR vocals: 11.11, SDR instrum: 17.41)
  - `6` — SCNet XL IHF (high instrum fullness by becruily)

#### MVSep Piano (piano, other) — `sep_type=29`

- **add_opt1** — Piano model type:
  - `0` — mdx23c (2023.08, SDR: 4.79)
  - `1` — mdx23c (2024.09, SDR: 5.59)
  - `2` — MelRoformer (viperx, SDR: 5.71)
  - `3` — SCNet Large (2024.09, SDR: 5.89)
  - `4` — Ensemble (SCNet + Mel, SDR: 6.20)
  - `5` — BS Roformer SW (SDR: 7.83)

#### MVSep Guitar (guitar, other) — `sep_type=31`

- **add_opt1** — Guitar model type:
  - `0` — mdx23c (2023.08, SDR: 4.78)
  - `2` — mdx23c (2024.06, SDR: 6.34)
  - `3` — MelRoformer (2024.06, SDR: 7.02)
  - `5` — BSRoformer (viperx, SDR: 7.16)
  - `6` — Ensemble (BS + Mel, SDR: 7.51)
  - `7` — BS Roformer SW (SDR: 9.05)

#### MVSep Acoustic Guitar — `sep_type=66`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from guitar part

#### MVSep Electric Guitar — `sep_type=81`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from guitar part

#### MVSep Lead/Rhythm Guitar — `sep_type=101`

- **add_opt1** — Model type:
  - `0` — Two-stage model (SDR: 9.21)
  - `1` — One-stage model (SDR: 9.02)

#### MDX-B Karaoke (lead/back vocals) — `sep_type=12`

- **add_opt1** — Karaoke model type:
  - `0` — Extract directly from mixture (SDR lead vocals: 6.81)
  - `1` — Extract from vocals part (SDR lead vocals: 7.94)

#### MVSep Karaoke (lead/back vocals) — `sep_type=49`

- **add_opt1** — Karaoke model type:
  - `0` — Model by viperx and aufr33 (SDR: 9.45)
  - `1` — Model by becruily (SDR: 9.61)
  - `2` — Model by gabox (SDR: 9.67)
  - `3` — Model fuzed gabox & aufr33/viperx (SDR: 9.85)
  - `4` — SCNet XL IHF by becruily (SDR: 9.53)
  - `5` — BS Roformer by frazer and becruily (SDR: 10.11)
  - `6` — BS Roformer by MVSep Team (SDR: 10.41)
  - `7` — BS Roformer by anvuew (SDR: 10.22)
- **add_opt2** — Extraction type:
  - `0` — Use as is
  - `1` — Extract vocals first

#### MVSep Crowd removal — `sep_type=34`

- **add_opt1** — Model type:
  - `8` — MDX23C v1 (SDR crowd: 5.57)
  - `9` — MDX23C v2 (SDR crowd: 6.06)
  - `0` — Mel Roformer (SDR crowd: 6.07)
  - `1` — Ensemble MDX23C + Mel Roformer (SDR crowd: 6.27)
  - `2` — BS Roformer (SDR crowd: 7.21)

#### MVSep Bass (bass, other) — `sep_type=41`

- **add_opt1** — Bass model type:
  - `0` — BS Roformer (SDR bass: 12.49)
  - `1` — HTDemucs4 (SDR bass: 12.52)
  - `2` — SCNet XL (SDR bass: 13.81)
  - `3` — BS + HTDemucs + SCNet (SDR bass: 14.07)
  - `4` — BS Roformer SW (SDR bass: 14.62)
  - `5` — BS Roformer SW + SCNet XL (SDR bass: 14.87)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from instrumental part
- **add_opt3** — Output files:
  - `0` — Standard set
  - `1` — Include results of independent models

#### MVSep Drums (drums, other) — `sep_type=44`

- **add_opt1** — Drums model type:
  - `0` — HTDemucs (SDR drums: 12.04)
  - `1` — MelBand Roformer (SDR drums: 12.76)
  - `2` — SCNet Large (SDR drums: 13.01)
  - `3` — SCNet XL (SDR drums: 13.42)
  - `4` — Mel + SCNet XL (SDR drums: 13.78)
  - `5` — BS Roformer SW (SDR drums: 14.11)
  - `6` — Mel + SCNet XL + BS Roformer SW (SDR drums: 14.35)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from instrumental part
- **add_opt3** — Output files:
  - `0` — Standard set
  - `1` — Include results of independent models

#### DrumSep — `sep_type=37`

- **add_opt1** — Model Type:
  - `0` — DrumSep model by inagoy (HDemucs, 4 stems)
  - `1` — DrumSep model by aufr33 and jarredou (MDX23C, 6 stems)
  - `2` — DrumSep SCNet XL (5 stems)
  - `3` — DrumSep SCNet XL (6 stems)
  - `4` — DrumSep SCNet XL (4 stems)
  - `5` — DrumSep Ensemble of 4 models (MDX23C + 3 * SCNet XL, 8 stems)
  - `6` — DrumSep MelBand Roformer (4 stems)
  - `7` — DrumSep MelBand Roformer (6 stems)
- **add_opt2** — Preprocess:
  - `0` — Apply Drums model before
  - `1` — Use as is (audio must contain drums only)

#### LarsNet — `sep_type=38`

- **add_opt1** — Model type:
  - `0` — Apply Demucs4HT first to get drums
  - `1` — Use as is (audio must contain drums only)

#### MVSep Wind (wind, other) — `sep_type=54`

- **add_opt1** — Wind model type:
  - `0` — MelBand Roformer (SDR wind: 6.73)
  - `1` — SCNet Large (SDR wind: 6.76)
  - `2` — Mel + SCNet (SDR wind: 7.22)
  - `3` — BS Roformer (SDR wind: 9.82)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from instrumental part
- **add_opt3** — Output files:
  - `0` — Standard set
  - `1` — Include results of independent models

#### MVSep Saxophone — `sep_type=61`

- **add_opt1** — Model type:
  - `0` — SCNet XL (SDR saxophone: 6.15)
  - `1` — MelBand Roformer (SDR saxophone: 6.97)
  - `2` — Mel + SCNet (SDR saxophone: 7.13)
  - `3` — BS Roformer (SDR saxophone: 9.77)

#### MVSep Flute — `sep_type=67`

- **add_opt1** — Flute model type:
  - `0` — SCNet XL (SDR flute: 6.27)
  - `1` — BS Roformer (SDR flute: 9.46)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Organ — `sep_type=58`

- **add_opt1** — Organ model type:
  - `0` — SCNet XL (SDR organ: 2.71)
  - `1` — MelBand Roformer (SDR organ: 2.77)
  - `2` — Mel + SCNet (SDR organ: 3.05)
  - `3` — BS Roformer (SDR organ: 5.08)

#### MVSep Bowed Strings — `sep_type=52`

- **add_opt1** — String model type:
  - `0` — MDX23C (SDR strings: 3.84)
  - `1` — BS Roformer (SDR strings: 5.41)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from instrumental part

#### MVSep Viola — `sep_type=69`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from strings part

#### MVSep Cello — `sep_type=70`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from strings part

#### MVSep Double Bass — `sep_type=73`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from strings part

#### MVSep Violin — `sep_type=65`

(No additional options documented)

#### MVSep Trumpet — `sep_type=71`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Trombone — `sep_type=75`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Oboe — `sep_type=77`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Clarinet — `sep_type=78`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep French Horn — `sep_type=82`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Harmonica — `sep_type=87`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Digital Piano — `sep_type=79`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from piano part

#### MVSep Synth — `sep_type=88`

- **add_opt1** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from instrumental part

#### MVSep Brass — `sep_type=107`

- **add_opt1** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Woodwind — `sep_type=108`

- **add_opt1** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Bagpipes — `sep_type=116`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from wind part

#### MVSep Celesta — `sep_type=110`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from percussion part

#### MVSep Xylophone — `sep_type=109`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract from percussion part

#### MVSep Choir — `sep_type=112`

- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract vocals first

#### MVSep SATB Choir — `sep_type=111`

- **add_opt1** — Model type:
  - `2` — SCNet Masked (SDR: 4.07)
  - `3` — BS Roformer (SDR: 7.39)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract vocals first

#### MVSep Male/Female separation — `sep_type=57`

- **add_opt1** — Model type:
  - `0` — BSRoformer by Sucial (SDR: 6.52)
  - `3` — BSRoformer by aufr33 (SDR: 8.18)
  - `1` — SCNet XL (SDR: 11.83)
  - `2` — MelRoformer (2025.01) (SDR: 13.03)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract vocals first with BS Roformer

#### MVSep Multichannel BS — `sep_type=43`

- **add_opt1** — Model Type:
  - `0` — BS Roformer (SDR: 11.81)
  - `1` — MDX23C (SDR: 10.36)
  - `2` — MelBand Roformer (SDR: 11.17)
  - `3` — MelBand Roformer XL (SDR: 11.28)

#### MVSep MultiSpeaker (MDX23C) — `sep_type=42`

- **add_opt1** — Model Type:
  - `0` — Extract directly from mixture
  - `1` — Extract from vocals part

#### Medley Vox — `sep_type=53`

- **add_opt1** — Model type:
  - `0` — Apply to original file
  - `1` — Extract vocals first

#### Aspiration (by Sucial) — `sep_type=50`

- **add_opt1** — Model type:
  - `0` — Extract directly from mixture
  - `1` — Extract from vocals part

#### MVSep Demucs4HT DNR — `sep_type=24`

- **add_opt1** — Model type:
  - `0` — Single (SDR: 9.62)
  - `1` — Ensemble (SDR: 10.16)

#### BandIt Plus — `sep_type=36`

(No additional options documented)

#### BandIt v2 — `sep_type=45`

- **add_opt1** — Model Type:
  - `0` — Multi language model
  - `1` — English model
  - `2` — German model
  - `3` — French model
  - `4` — Spanish model
  - `5` — Chinese model
  - `6` — Faroese model

#### MVSep DnR v3 — `sep_type=56`

- **add_opt1** — Model type:
  - `0` — SCNet Large (SDR avg: 11.22)
  - `1` — MelBand Roformer (SDR avg: 10.99)
  - `2` — Mel + SCNet (SDR avg: 11.54)
- **add_opt2** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Use vocals model to help
- **add_opt3** — Output files:
  - `0` — Standard set
  - `1` — Include results of independent models

#### Vit Large 23 — `sep_type=33`

- **add_opt1** — Model type:
  - `0` — v1 (SDR vocals: 9.78)
  - `1` — v2 (SDR vocals: 9.90)

#### Reverb Removal — `sep_type=22`

- **add_opt1** — Model Type:
  - `0` — Reverb removal by FoxJoy (MDX23C)
  - `1` — Reverb removal by anvuew (MelRoformer)
  - `2` — Reverb removal by anvuew (BSRoformer)
  - `3` — Reverb removal by anvuew v2 (MelRoformer)
  - `4` — Reverb removal by Sucial (MelRoformer)
  - `5` — Reverb removal by Sucial v2 (MelRoformer)
  - `6` — DeReverb room by anvuew (BSRoformer)
  - `7` — DeReverb stereo by anvuew (BSRoformer)
- **add_opt2** — Preprocess:
  - `0` — Extract vocals (needed for Mel/BS Roformer)
  - `1` — Use as is

#### DeNoise — `sep_type=47`

- **add_opt1** — Model type:
  - `0` — aufr33 (Standard)
  - `1` — aufr33 (Aggressive)
  - `2` — gabox

#### Apollo Enhancers — `sep_type=51`

- **add_opt1** — Model type:
  - `0` — MP3 Enhancer (by JusperLee)
  - `1` — Universal Super Resolution (by Lew)
  - `2` — Vocals Super Resolution (by Lew)
  - `3` — Universal Super Resolution (by MVSep Team)
  - `4` — Universal Super Resolution (by baicai1145)
- **add_opt2** — Cutoff (Hz): `0` (No cutoff), `2000`–`22000` (in 1000 Hz steps)

#### AudioSR (Super Resolution) — `sep_type=59`

- **add_opt1** — Cutoff (Hz): `0` (Automatic), `2000`–`22000` (in 1000 Hz steps)

#### Stable Audio Open Gen — `sep_type=62`

- **add_opt1** — Text prompt (free text)
- **add_opt2** — Length (in seconds): `3`, `5`, `8`, `10`, `12`, `15`, `20`, `25`, `30`, `35`, `40`, `45`, `47`

#### Whisper (extract text from audio) — `sep_type=39`

- **add_opt1** — Model type:
  - `0` — Apply to original file
  - `1` — Extract vocals first
- **add_opt2** — Transcription type:
  - `0` — New timestamps by linto-ai
  - `1` — Old version of timestamps by whisper

#### Parakeet (extract text from audio) — `sep_type=64`

- **add_opt1** — Model type:
  - `0` — Apply to original file
  - `1` — Extract vocals first
- **add_opt2** — Version:
  - `0` — Parakeet v2
  - `1` — Parakeet v3

#### VibeVoice (Voice Cloning) — `sep_type=103`

- **add_opt1** — Model type: `0` (1.5B Small), `1` (7B Large)
- **add_opt2** — Text prompt (free text)
- **add_opt3** — Extract vocals first: `0` (Use original reference file), `1` (Extract vocals first)

#### VibeVoice (TTS) — `sep_type=104`

- **add_opt1** — Model type: `0` (1.5B Small), `1` (7B Large)
- **add_opt2** — Text prompt (free text)

#### Qwen3-TTS (Custom Voice) — `sep_type=118`

- **add_opt1** — Text prompt (free text)
- **add_opt2** — Speaker: `aiden` (English), `ryan` (English), `sohee` (Korean), `ono_anna` (Japanese), `serena` (Chinese), `uncle_fu` (Chinese), `vivian` (Chinese), `dylan` (Chinese Beijing), `eric` (Chinese Sichuan)
- **add_opt3** — Language: `auto`, `english`, `russian`, `chinese`, `french`, `german`, `italian`, `japanese`, `korean`, `portuguese`, `spanish`
- **add_opt4** — Voice description (free text)

#### Qwen3-TTS (Voice Design) — `sep_type=119`

- **add_opt1** — Text prompt (free text)
- **add_opt2** — Voice description (free text)
- **add_opt3** — Language: `auto`, `english`, `russian`, `chinese`, `french`, `german`, `italian`, `japanese`, `korean`, `portuguese`, `spanish`

#### Qwen3-TTS (Voice Cloning) — `sep_type=120`

- **add_opt1** — Text prompt (free text)
- **add_opt2** — Reference text in audio (optional, free text)
- **add_opt3** — Language: `auto`, `english`, `russian`, `chinese`, `french`, `german`, `italian`, `japanese`, `korean`, `portuguese`, `spanish`
- **add_opt4** — Extract vocals first: `0` (Use original reference file), `1` (Extract vocals first)

#### Bark (Speech Gen) — `sep_type=115`

- **add_opt1** — Text prompt (free text)
- **add_opt2** — Speaker: language-prefixed codes like `en_0`–`en_9`, `ru_0`–`ru_9`, `de_0`–`de_9`, `es_0`–`es_9`, `fr_0`–`fr_9`, `hi_0`–`hi_9`, `it_0`–`it_9`, `ja_0`–`ja_9`, `ko_0`–`ko_9`, `pl_0`–`pl_9`, `pt_0`–`pt_9`, `tr_0`–`tr_9`, `zh_0`–`zh_9`

#### SOME (Singing-Oriented MIDI Extractor) — `sep_type=80`

- **add_opt1** — How to use:
  - `0` — Apply to original file
  - `1` — Extract vocals first

#### Transkun (piano -> midi) — `sep_type=113`

- **add_opt1** — How to extract:
  - `0` — Extract directly from mixture
  - `1` — Extract piano first

#### Phantom Centre extraction — `sep_type=55`

- **add_opt1** — Model type:
  - `0` — Phantom Centre by wesleyr36 (MDX23C)
  - `1` — Phantom Centre by gilliaan (BSRoformer)
  - `2` — Phantom Centre by gilliaan (mdx23c)

#### HeartMuLa (Song Gen) — `sep_type=121`

- **add_opt1** — Lyrics (free text)
- **add_opt2** — Tags (optional, free text)
- **add_opt3** — Genre: `pop`, `hip-hop`, `rock`, `electronic`, `latin`, `r&b`, `classical`, `jazz`, `metal`, `country`, `rap`, `edm`, `reggaeton`, `k-pop`, `house`, `techno`, `alternative rock`, `indie`, `soul`, `blues`, `reggae`, `afrobeats`, `folk`, `ambient`, `lo-fi`, `trap`, `dance pop`, `indie pop`, `dubstep`, `drum and bass`, `trance`, `synthwave`, `punk`, `hard rock`, `heavy metal`, `nu metal`, `grunge`, `funk`, `disco`, `soundtrack`, `cinematic`, `orchestral`, `acoustic`, `gospel`, `drill`, `boom bap`, `uk garage`, `grime`, `electro`, `breakbeat`, `trip-hop`, `future bass`, `hardstyle`, `industrial`, `idm`, `hyperpop`, `vaporwave`, `pop punk`, `metalcore`, `death metal`, `black metal`, `symphonic metal`, `post-punk`, `psychedelic rock`, `progressive rock`, `emo`, `shoegaze`, `post-rock`, `garage rock`, `math rock`, `bossa nova`, `samba`, `dancehall`, `ska`, `amapiano`, `j-pop`, `americana`, `bluegrass`, `neo soul`, `smooth jazz`, `swing`, `bebop`, `fusion`, `arabic`, `indian`, `celtic`, `balkan`, `avant-garde`, `experimental`, `new age`, `baroque`, `romantic`, `minimalism`, `a cappella`, `choral`, `mathcore`, `screamo`, `big band`, `motown`, `chillout`, `world music` (use `---` for none)
- **add_opt4** — Timbre: `clean`, `distorted`, `acoustic`, `synthetic`, `bright`, `dark`, `warm`, `cold`, `soft`, `hard`, `heavy`, `light`, `dry`, `wet`, `smooth`, `rough`, `thick`, `thin`, `wide`, `narrow`, `deep`, `full`, `punchy`, `muffled`, `boomy`, `airy`, `lo-fi`, `saturated`, `harsh`, `mellow`, `rich`, `dull`, `hollow`, `tight`, `loose`, `spacious`, `echoing`, `resonant`, `organic`, `metallic`, `wooden`, `breathy`, `raspy`, `husky`, `whispery`, `gravelly`, `throaty`, `nasal`, `guttural`, `wailing`, `brassy`, `crunchy`, `fuzzy`, `gritty`, `grainy`, `crispy`, `buzzing`, `droning`, `ringing`, `piercing`, `shrill`, `tinny`, `biting`, `bass-heavy`, `midrangey`, `trebly`, `harmonic`, `inharmonic`, `pure`, `complex`, `modulated`, `detuned`, `phasey`, `boxy`, `dead`, `muted`, `squelchy`, `fizzy`, `hazy`, `diffuse`, `papery`, `plastic`, `rubbery`, `glassy`, `creamy`, `silky`, `velvety`, `brilliant`, `aggressive`, `gentle`, `soothing`, `sparse`, `shallow` (use `---` for none)
- **add_opt5** — Gender: `---` (none), `male`, `female`
- **add_opt6** — Mood: `happy`, `sad`, `energetic`, `relaxing`, `dark`, `upbeat`, `chill`, `calm`, `joyful`, `melancholic`, `uplifting`, `romantic`, `tense`, `epic`, `aggressive`, `dramatic`, `playful`, `peaceful`, `dreamy`, `eerie`, `mysterious`, `hopeful`, `nostalgic`, `angry`, `soothing`, `cheerful`, `emotional`, `intense`, `suspenseful`, `gloomy`, `somber`, `gentle`, `mellow`, `serene`, `exciting`, `fun`, `groovy`, `lively`, `driving`, `bouncy`, `triumphant`, `motivational`, `euphoric`, `ethereal`, `meditative`, `laid-back`, `tranquil`, `depressing`, `sorrowful`, `mournful`, `heartbreaking`, `bittersweet`, `touching`, `scary`, `creepy`, `ominous`, `fierce`, `frantic`, `anxious`, `restless`, `nervous`, `haunting`, `sexy`, `sensual`, `quirky`, `weird`, `hypnotic`, `majestic`, `grand`, `reflective`, `pensive`, `introspective`, `soulful`, `funky` (use `---` for none)
- **add_opt7** — Instrument: `piano`, `synthesizer`, `electric guitar`, `acoustic guitar`, `bass`, `bass guitar`, `drums`, `drum machine`, `percussion`, `strings`, `violin`, `keyboard`, `saxophone`, `trumpet`, `cello`, `flute`, `organ`, `electric piano`, `double bass`, `brass`, `woodwinds`, `trombone`, `clarinet`, `viola`, `french horn`, `tuba`, `oboe`, `bassoon`, `piccolo`, `accordion`, `ukulele`, `banjo`, `mandolin`, `harp`, `harpsichord`, `celesta`, `kick drum`, `snare drum`, `cymbals`, `hi-hat`, `toms`, `tambourine`, `shaker`, `congas`, `bongos`, `cowbell`, `marimba`, `xylophone`, `vibraphone`, `glockenspiel`, `timpani`, `tabla`, `taiko`, `sitar`, `lute`, `fiddle`, `erhu`, `duduk`, `shakuhachi`, `recorder`, `sampler`, `turntables`, `theremin` (use `---` for none)
- **add_opt8** — Scene: `party`, `dance`, `workout`, `relax`, `study`, `sleep`, `focus`, `background`, `driving`, `cinematic`, `gaming`, `meditation`, `club`, `lounge`, `commuting`, `working`, `coding`, `reading`, `yoga`, `gym`, `running`, `cooking`, `cleaning`, `dating`, `romantic dinner`, `late night`, `road trip`, `cafe`, `bar`, `nature`, `morning`, `evening`, `soundtrack`, `trailer`, `vlog`, `commercial`, `podcast`, `video game`, `film`, `anime`, `corporate`, `presentation`, `wedding`, `festival`, `holiday`, `summer`, `winter`, `christmas`, `halloween` (use `---` for none)
- **add_opt9** — Region: `western`, `latin`, `african`, `asian`, `middle eastern`, `european`, `caribbean`, `k-pop`, `j-pop`, `c-pop`, `bollywood`, `celtic`, `nordic`, `balkan`, `slavic`, `mediterranean`, `arabic`, `indian`, `native american`, `indigenous`, `romani`, `klezmer`, `american`, `british`, `french`, `spanish`, `italian`, `german`, `irish`, `scottish`, `jamaican`, `cuban`, `brazilian`, `mexican`, `andean`, `hawaiian`, `polynesian`, `australian`, `persian`, `turkish`, `greek`, `west african`, `south african`, `east asian`, `south asian`, `southeast asian`, `eastern european`, `scandinavian`, `appalachian`, `cajun`, `afro-cuban`, `afro-brazilian` (use `---` for none)
- **add_opt10** — Topic: `love`, `heartbreak`, `romance`, `breakup`, `desire`, `infatuation`, `betrayal`, `life`, `death`, `friendship`, `family`, `growing up`, `youth`, `aging`, `nostalgia`, `loneliness`, `grief`, `mental health`, `depression`, `anxiety`, `hope`, `motivation`, `empowerment`, `self-love`, `overcoming`, `success`, `failure`, `party`, `money`, `wealth`, `hustle`, `drinking`, `drugs`, `addiction`, `crime`, `violence`, `revenge`, `cars`, `fashion`, `society`, `politics`, `protest`, `rebellion`, `war`, `peace`, `injustice`, `freedom`, `religion`, `spirituality`, `faith`, `nature`, `space`, `ocean`, `summer`, `winter`, `spring`, `autumn`, `travel`, `home`, `storytelling`, `fantasy`, `sci-fi`, `horror`, `mythology`, `comedy`, `parody`, `instrumental` (use `---` for none)

#### spleeter — `sep_type=0`

- **add_opt1** — Model type:
  - `0` — 2 stems (vocals, music)
  - `1` — 4 stems (vocals, drums, bass, other)
  - `2` — 5 stems (vocals, drums, bass, piano, other)

#### UnMix — `sep_type=3`

- **add_opt1** — Model type:
  - `0` — unmix XL (vocals, drums, bass, other)
  - `1` — unmix HQ (vocals, drums, bass, other)
  - `2` — unmix SD (vocals, drums, bass, other)
  - `3` — unmix SE (vocals, music) — low quality

#### Demucs3 Model — `sep_type=10`

- **add_opt1** — Model type:
  - `0` — Demucs3 Model A (Contest Version)
  - `1` — Demucs3 Model B (High Quality)

### Output Format (`output_format`) Values

| Name | Value |
|---|---|
| mp3 (320 kbps) | 0 |
| wav (uncompressed, 16 bit) | 1 |
| flac (lossless, 16 bit) | 2 |
| m4a (lossy) | 3 |
| wav (uncompressed, 32 bit) | 4 |
| flac (lossless, 24 bit) | 5 |

### Example

```bash
curl --location --request POST 'https://mvsep.com/api/separation/create' \
  --form 'audiofile=@"/path/to/file.mp3"' \
  --form 'api_token="YOUR_API_TOKEN"' \
  --form 'sep_type="9"' \
  --form 'add_opt1="0"' \
  --form 'add_opt2="1"' \
  --form 'output_format="1"' \
  --form 'is_demo="1"'
```

### Response

| Key | Value |
|---|---|
| `success` | `false` when job creation failed; `true` when created successfully |
| `data` | Holds extra information depending on `success` |
| `data.link` | URL to get result (when `success` is `true`) |
| `data.hash` | Job hash (when `success` is `true`) |
| `data.message` | Error description (when `success` is `false`) |

---

## Get Result

`GET https://mvsep.com/api/separation/get`

### Parameters

| Field | Type | Description |
|---|---|---|
| `hash` | String | Separation hash (from create response) |

### Example

```bash
curl --location --request GET 'https://mvsep.com/api/separation/get?hash=20230327071601-0e3e5c6c85-13-dimensions.mp3'
```

### Response

| Key | Value |
|---|---|
| `success` | `false` when hash not found/removed/expired; `true` when valid |
| `status` | Job status (see below) |
| `data` | Holds extra information depending on `status` |
| `data.queue_count` | Unprocessed jobs count in user's priority (when `waiting` or `distributing`) |
| `data.current_order` | Order of user's job (when `waiting` or `distributing`) |
| `data.message` | Description of status; error reason when `failed` |
| `data.algorithm` | Used algorithm (when `done`) |
| `data.algorithm_description` | Algorithm details (when `done`) |
| `data.output_format` | Output format (when `done`) |
| `data.tags` | Audio meta tags (when `done`) |
| `data.input_file` | Input audio download details (when `done`) |
| `data.files` | Output audios download details (when `done`) |
| `data.date` | Job processing date (when `done`) |
| `data.finished_chunks` | Finished parts count (when `distributing`) |
| `data.all_chunks` | Total parts count (when `distributing`) |

#### Status Values

| Status | Meaning |
|---|---|
| `not_found` | Job is invalid |
| `waiting` | Job is in queue, not yet processed |
| `processing` | Job is being processed |
| `done` | Job has been successfully processed |
| `failed` | Job processing failed |
| `distributing` | Large audio is being distributed to multiple GPU instances |
| `merging` | Distributed parts have finished processing and are being merged |

---

## Errors

| Error Code | Meaning |
|---|---|
| 400 | Some parameters are missing or invalid |
| 401 | Unknown or invalid `api_token` |
