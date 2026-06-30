# Research Prompt: Best Practices for Assembling a Chinese Worship Song Set

## Objective
Research and compile actionable best practices for assembling a set of Chinese worship songs (中文敬拜诗歌) into a cohesive song set. The output should guide a software system that programmatically selects and orders songs with smooth musical transitions and a worship-leading arc. Focus on contemporary Chinese worship songs (现代中文敬拜诗歌) used in Chinese-speaking congregations (华人教会).

## Key Research Questions

### 1. Worship Flow & Thematic Arc
- What is the recommended emotional/theological progression of a worship set? (e.g., starting with praise/thanksgiving 赞美/感恩 → entering deeper worship 敬拜/亲近 → closing with commitment/response 奉献/回应/差遣)
- How many songs are typically in a Sunday worship set? Prayer meeting? Retreat?
- What thematic tags are commonly used in Chinese worship music (赞美, 感恩, 敬拜, 奉献, 认罪, 差遣, etc.) and how do they map to set positions?
- Are there established worship-arc frameworks (e.g., "worship architecture", Temple model: Outer Court → Inner Court → Holy of Holies) adapted for Chinese contexts?
- Differences between hymnal-based (传统圣诗) and contemporary-worship (现代敬拜) selection approaches.

### 2. Tempo (BPM) Progression
- Confirm/refine the heuristic: start with higher BPM (e.g., 110–130) and gradually slow toward the end (e.g., 60–80).
- What BPM ranges suit each phase (opening praise, mid-worship, intimate/closing)?
- How gradual should the BPM step-down be between consecutive songs? Any rule of thumb (e.g., ≤15–20 BPM drop per transition)?
- Are Chinese worship songs typically slower/faster than English counterparts? Any cultural tempo tendencies?

### 3. Key & Harmonic Compatibility for Transitions
- Which key relationships count as "compatible" for smooth transitions? (same key, relative major/minor, dominant/subdominant, Circle-of-Fifths neighbors)
- Best-practice modulation/transition techniques between two songs in different keys (pivot chords, shared tones, direct modulation, vamp transitions).
- Common keys used in Chinese worship (C, D, G, A, F, etc.) and preferred key-neighbor pairs for transitions.
- How to maintain harmonic coherence while still moving tempo and mood forward.
- Quantifiable compatibility scoring (e.g., Circle-of-Fifths distance) that a program could compute.

### 4. Chinese Song Sources & Cataloging
- Major Chinese hymnals/songbooks and how they index songs (by theme/tune/author): e.g., 赞美之泉, 新歌颂扬, 谈琴诗歌, 生命圣诗.
- Contemporary Chinese worship collectives commonly used: Stream of Praise 赞美之泉, Joshua Band 约书亚乐团, Wings of Worship 馨香祭, Bridge 音乐, etc.
- Common thematic categorization systems used by these sources.
- How to classify a given song's "phase/role" in a set based on lyrics, tempo, and mood (for programmatic tagging).

### 5. Decision Rules for Programmatic Assembly
Synthesize concrete, computable rules:
- **Hard constraints** (e.g., first song must be praise/thanksgiving with BPM ≥ 110; last song BPM ≤ 90; consecutive keys within Circle-of-Fifths distance ≤ 2 unless modulating).
- **Soft constraints** (e.g., prefer tempo step-down ≤ 15 BPM between consecutive songs; prefer thematic progression following the praise→worship→response arc).
- A **scoring/fitness function** for ordering a candidate song set that balances tempo decay, key compatibility, and thematic progression.
- **Sequence templates** for 4-, 5-, and 6-song sets showing the ideal phase / BPM / key profile.
- How to handle "dead-end" songs (poor transition candidates) and limited song pools.

## Output Format
Structured report with sections matching the five areas above. For each, give a concise prose summary (1–3 paragraphs), then a bulleted list of concrete recommendations. End with a **References** section listing Chinese- and English-language sources (books, articles, worship-leading manuals, songbook indexes).

## Scope Notes
- Prioritize resources from/for Chinese-language congregations; English worship-leading literature is acceptable as supplementary where Chinese-specific sources are thin.
- Prefer quantitative or clearly classifiable criteria (BPM numbers, key relationships, theme tags) over purely qualitative guidance — the downstream consumer is a software song-set constructor.
- In scope: contemporary Chinese worship songs and Chinese versions of widely-used Western worship songs.
- Out of scope: lyrics/LRC generation, audio mixing techniques beyond transition-relevant key/tempo considerations, full service liturgy planning.
