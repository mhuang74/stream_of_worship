# Project Memory

## 2026-06-15

- Addressed PR #104 review feedback by hardening LRC language/script mismatch warnings against missing or non-string lyric payloads, preserving the legacy `lyrics.lrc` R2 alias for renderers, and adding regression coverage.
- Implemented `catalog-insert-youtube-v2` for the admin CLI.
- Added curated `sow-admin catalog insert`, `catalog edit`, `catalog quarantine`, `catalog restore`, and `catalog list --deleted` flows.
- Added reviewed YouTube metadata/transcript drafting plus shared song ID and lyrics normalization helpers.
- Refactored the YouTube audio import path so `catalog insert --youtube` reuses the `audio download` core behavior.
