# Stream of Worship

A seamless Chinese worship music transition system: songs are analyzed (tempo, key, structure) and strung into smooth transitions. Users build songsets, render audio/video, and manage a song library.

## Language

### Locale
The display language of the webapp's UI — one of `English` or `繁體中文` (Traditional Chinese) — chosen per user account. The Locale affects **UI text only**: the song catalog, lyrics, and rendered audio/video output are always Traditional Chinese regardless of the Locale, because that is how the catalog and lyrics are stored.
_Avoid_: language, display language, translation

### Catalog

**Song**:
A catalog entry scraped from sop.org containing the metadata of a hymn — title, composer, lyricist, album, musical key, and lyrics. A Song has no audio of its own.
_Avoid_: track, piece, hymn entry

**Recording**:
A hash-addressed audio file bound to a Song, carrying its analysis results (tempo, key, beats, sections), lyrics status, and R2 asset URLs. One Song may have many Recordings.
_Avoid_: track, audio, file

**SongComponent**:
A detected structural segment of a Recording — such as a chorus, verse, or bridge — with per-segment musical features (BPM, key, energy) and an entry/exit role for transition planning. Admin-side only; users never interact with it directly.
_Avoid_: section, part, segment

**VocalPosture**:
A 3-value classification of a song component's lyrical addressee: "To God", "About God", "To Congregation". Admin-side only; persisted at the component and recording level but not surfaced in the webapp.
_Avoid_: voice, perspective, address

> Three "section" concepts exist in the codebase and are easily confused:
> - `Song.sections` — scraped lyric sections from sop.org (lyric structure).
> - `Recording.sections` — allin1 analysis section labels (audio structure boundaries).
> - `song_components` — fully-featured detected components with features and roles (the SongComponent above).

### Songset & Transitions

**Songset**:
An ordered list of songs a user curates and renders into a seamless audio/video deliverable.
_Avoid_: playlist

**Transition**:
The changeover between adjacent songs in a Songset, controlled by five parameters: gapBeats, crossfadeEnabled, crossfadeDurationSeconds, keyShiftSemitones, and tempoRatio.
_Avoid_: crossfade, segue, bridge

**Theme**:
A fixed 12-value vocabulary classifying a song's worship theme: 讚美, 感恩, 敬拜, 奉獻, 認罪, 差遣, 信心, 祈禱, 復興, 聖靈, 十字架, 跟隨. Each theme maps to a Worship Arc phase; a ThemeAnchor is the reference embedding used to classify a song's themes by cosine similarity. At the recording level, the theme is aggregated from component-level classifications (most frequent, with chorus-preference tie-breaking) and persisted as `recordings.theme`.
_Avoid_: tag, category, label

**Worship Arc**:
The fixed five-phase liturgy every Songset is constructed against: 讚美 (Call) → 感恩 (Thanksgiving) → 敬拜 (Worship) → 奉獻 (Response) → 差遣 (Commission). A Songset's songs are chosen to follow this arc. The arc is invariant; per-song-count templates select which phases appear (2 songs: Call→Response; 3: Call→Worship→Commission; 4: Call→Worship→Response→Commission; 5: full arc). Currently defined for 2-5 song sets.
_Avoid_: set list, service order, liturgy

### Render

**Render**:
The process of producing an audio (MP3) and/or video (MP4) deliverable with a chapters manifest from a Songset, stored in Cloudflare R2.
_Avoid_: export, encode, output

### Projection

**Projection**:
The webapp's two-screen worship playback model: an operator controls playback on a controller screen while a synchronized lyrics video plays chrome-free on a second projection screen (a TV or projector) via the W3C Presentation API. Casting the lyrics video to the big screen is what lets worship flow without interruption.
_Avoid_: casting (generic), screen mirroring, chromecast, second screen

### Lyrics

**Lyrics**:
Time-synced lyrics for a Recording, with timestamps in [mm:ss.xx] format. The canonical version is curated by admins; users may create personal overrides.
_Avoid_: LRC, official LRC, synced lyrics

### User State

**Favorite**:
A song a user has marked as personally preferred via a toggle on the song, feeding the pool they build songsets from. Favoriting a song requires the song to be Completed; unfavoriting is free.
_Avoid_: like, save, starred, bookmark

**Completion**:
The state of a song whose user has heard at least 90% of its duration at any point. Permanent once earned, and the only prerequisite for Favoriting a song.
_Avoid_: listened, played, heard, watched
