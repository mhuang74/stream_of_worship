# All-In-One Analysis Results Caching System

**Date:** 2026-01-02
**Version:** 1.0
**Status:** Approved
**Author:** System Design

---

## 1. Overview

### Problem Statement

The `allin1.analyze()` function performs deep learning inference for beat detection, downbeat detection, tempo estimation, structure segmentation, and embedding extraction. This analysis takes 2-3 minutes per song on typical hardware. For a POC with 4 songs, total processing time is ~10 minutes. Re-running the analysis script (for visualization tweaks, compatibility matrix updates, or transition generation) requires re-processing all songs from scratch.

### Solution

Implement a content-addressable caching system that stores analysis results on disk and retrieves them on subsequent runs. The cache key is a SHA256 hash of the audio file contents, ensuring cache hits even when files are renamed or moved.

### Goals

1. **Reduce processing time by 95%** for cached runs (10 min → 30 sec)
2. **Preserve cache across file renames** using content hashing
3. **Write results incrementally** to survive crashes and provide progress
4. **Maintain backward compatibility** with existing output formats
5. **No expiration** - cache persists indefinitely

---

## 2. Architecture

### Cache Directory Structure

```
poc_output_allinone/
├── cache/                                      # NEW: Cache directory
│   ├── metadata.json                           # Cache index
│   ├── 9f86d081884c7d659a2feaa0c55ad015.json   # Cached results (hash-named)
│   ├── a3bf4f1b2b0b822cd15d6c15b0f00a08.json
│   └── ...
├── poc_summary.csv                             # Existing outputs (unchanged)
├── poc_full_results.json
├── poc_analysis_visualizations.png
└── ...
```

### Cache File Format

**Individual Cache File** (`cache/<hash>.json`):

```json
{
  "cache_version": "1.0",
  "content_hash": "9f86d081884c7d659a2feaa0c55ad015a3bf4f1b2b0b822cd15d6c15b0f00a08",
  "original_filename": "give_thanks.mp3",
  "cached_at": "2026-01-02T15:45:30.123456",
  "allinone_version": "1.1.0",
  "analysis_duration_seconds": 142.5,

  "result_data": {
    "filename": "give_thanks.mp3",
    "duration": 278.54,
    "tempo": 71.0,
    "tempo_source": "allinone",
    "beats": [2.59, 3.48, 4.31, ...],
    "downbeats": [2.59, 5.19, 7.78, ...],
    "key": "C",
    "mode": "major",
    "sections": [...],
    "embeddings_shape": [4, 1200, 24],
    "embeddings_serialized": "<base64-encoded numpy array>"
  }
}
```

**Cache Metadata Index** (`cache/metadata.json`):

```json
{
  "cache_format_version": "1.0",
  "last_updated": "2026-01-02T15:45:30.123456",
  "total_entries": 4,
  "entries": {
    "9f86d081...": {
      "original_filename": "give_thanks.mp3",
      "cached_at": "2026-01-02T15:45:30.123456",
      "file_size_bytes": 5573110,
      "cache_file": "9f86d081884c7d659a2feaa0c55ad015.json"
    }
  }
}
```

---

## 3. Core Components

### Component 1: File Hashing

**Function:** `compute_file_hash(filepath: Path) -> str`

**Algorithm:** SHA256
- **Reason:** Collision-resistant, fast (~10ms for 5MB files), standard library support
- **Chunk size:** 64KB for memory efficiency with large files
- **Output:** 64-character hex string

**Implementation:**
```python
def compute_file_hash(filepath: Path) -> str:
    """Compute SHA256 hash of audio file contents."""
    import hashlib
    sha256_hash = hashlib.sha256()
    with open(filepath, "rb") as f:
        for byte_block in iter(lambda: f.read(65536), b""):
            sha256_hash.update(byte_block)
    return sha256_hash.hexdigest()
```

### Component 2: Cache Loading

**Function:** `load_from_cache(content_hash: str, cache_dir: Path) -> Optional[dict]`

**Logic:**
1. Check if cache file exists (`cache/<hash[:32]>.json`)
2. Load and validate JSON structure
3. Verify cache version compatibility
4. Deserialize embeddings from base64
5. Return result_data or None on cache miss

**Cache Miss Scenarios:**
- File doesn't exist
- Corrupted JSON
- Version mismatch
- Hash mismatch (integrity check)

**Performance:** <10ms per lookup

### Component 3: Cache Saving

**Function:** `save_to_cache(content_hash, result_data, cache_dir, analysis_duration)`

**Logic:**
1. Serialize embeddings to base64 (numpy → bytes → base64)
2. Create cache entry with metadata
3. Write to `cache/<hash[:32]>.json`
4. Update central metadata index

**Data Not Cached:**
- Raw audio arrays (`_y`, `_sr`) - too large, easily recomputed
- Visualization intermediates (`_chroma`, `_rms`) - recomputed on demand

**Storage:** ~50KB per song (vs 5MB if caching raw audio)

### Component 4: Metadata Management

**Function:** `update_cache_metadata(content_hash, cache_entry, cache_dir)`

**Purpose:**
- Fast lookup without reading all cache files
- Human-readable hash → filename mapping
- Cache statistics (total entries, last updated)

---

## 4. Integration with poc_analysis_allinone.py

### Modified Function Signature

**Before:**
```python
def analyze_song_allinone(filepath):
```

**After:**
```python
def analyze_song_allinone(filepath, cache_dir=None, use_cache=True):
```

### Analysis Flow with Caching

```
START
  ↓
Compute SHA256 hash of audio file
  ↓
Cache enabled? → NO → [Run full analysis]
  ↓ YES
Check cache/<hash>.json exists?
  ↓ YES (CACHE HIT)          ↓ NO (CACHE MISS)
Load cached results           Run allin1.analyze()
Recompute viz data (30s)      Full analysis (2-3 min)
  ↓                           ↓
  |                     Save to cache
  |                           ↓
  └───────────────────────────┘
                ↓
          Return results
```

---

## 5. Numpy Array Serialization

### Challenge

Embeddings are numpy arrays with shape `(4, timesteps, 24)` - not JSON-serializable.

### Solution: Base64 Encoding

**Serialize (save_to_cache):**
```python
embeddings_bytes = embeddings.astype(np.float32).tobytes()
serialized = base64.b64encode(embeddings_bytes).decode('ascii')
cache_entry['result_data']['embeddings_serialized'] = serialized
```

**Deserialize (load_from_cache):**
```python
embeddings_bytes = base64.b64decode(serialized)
embeddings = np.frombuffer(embeddings_bytes, dtype=np.float32)
embeddings = embeddings.reshape(original_shape)
result_data['_embeddings'] = embeddings
```

**Storage Efficiency:**
- 4 stems × 1200 timesteps × 24 dims × 4 bytes = 460KB uncompressed
- Base64 encoding: 460KB → 613KB (33% overhead)
- Gzip compression (future): Can reduce to ~150KB

---

## 6. Cache Invalidation Strategy

### When Cache is Invalidated

**Automatic (hash-based):**
- File content changes → Different hash → New cache entry
- Old cache entry preserved (manual cleanup)

**Manual:**
- Delete `poc_output_allinone/cache/` directory
- Delete individual cache file
- Pass `use_cache=False` to bypass cache

### When Cache Remains Valid

**Scenarios:**
- File renamed → Same content → Same hash → Cache hit
- File moved → Same content → Same hash → Cache hit
- File copied → Same content → Same hash → Cache hit
- Script re-run → Same file → Same hash → Cache hit

### No Expiration

Cache never expires automatically. Reasons:
1. Audio files are static (rarely change)
2. Disk space is cheap (~50KB per song)
3. User controls when to clear cache
4. Deterministic behavior (no time-based surprises)

---

## 7. Error Handling

### Corrupted Cache File

**Scenario:** JSON parse error, missing fields, invalid data

**Handling:**
```python
try:
    cache_data = json.load(f)
    # Validate structure
except (json.JSONDecodeError, KeyError) as e:
    print(f"⚠️ Cache file corrupted: {e}")
    return None  # Fallback to full analysis
```

### Disk Full (Write Failure)

**Scenario:** No space to write cache file

**Handling:**
```python
try:
    save_to_cache(...)
except IOError as e:
    print(f"⚠️ Failed to write cache: {e}")
    # Continue - analysis still succeeds
```

### Cache Version Mismatch

**Scenario:** Future version changes cache format

**Handling:**
```python
if cache_data.get('cache_version') != '1.0':
    print(f"⚠️ Cache version mismatch")
    return None  # Re-analyze with new version
```

---

## 8. Performance Benchmarks

### Current System (No Cache)

| Scenario | Time |
|----------|------|
| 4 songs, first run | 10 min |
| 4 songs, second run | 10 min |
| Modify 1 song, re-run | 10 min |

### With Caching

| Scenario | Time | Speedup |
|----------|------|---------|
| 4 songs, first run | 10 min | 1x (cache population) |
| 4 songs, second run | 30 sec | **20x faster** |
| Modify 1 song, re-run | 3 min | 3.3x faster |
| Rename file, re-run | 30 sec | **20x faster** |

### Storage Requirements

- Per song: ~50KB JSON (with base64 embeddings)
- 4 songs: ~200KB total
- 100 songs: ~5MB total
- **Negligible disk space**

---

## 9. Backward Compatibility

### No Breaking Changes

All existing output files unchanged:
- `poc_summary.csv` - Same format
- `poc_full_results.json` - Same format
- `poc_analysis_visualizations.png` - Same format
- `poc_compatibility_scores.csv` - Same format

Cache is opt-in:
- Default: `use_cache=True` (enabled)
- Disable: Pass `use_cache=False`
- Delete cache: Remove `cache/` directory

Existing installations:
- No migration needed
- Cache auto-creates on first run
- Old output files remain valid

---

## 10. Design Decisions & Rationale

### Why SHA256 vs MD5?

**Choice:** SHA256

**Rationale:**
- Collision-resistant (MD5 has known collisions)
- Fast enough (~10ms for 5MB files)
- Industry standard for content addressing
- Future-proof (won't need migration from MD5)

### Why Truncate Hash to 32 Chars?

**Choice:** Use first 32 chars for filename, store full 64 in JSON

**Rationale:**
- 32 chars = 128 bits = still collision-resistant (2^128 space)
- Shorter filenames easier to debug
- Full hash in JSON for validation

### Why Not Cache Raw Audio?

**Choice:** Don't cache `_y`, `_sr`, `_chroma`, `_rms`

**Rationale:**
- Raw audio: 5MB per song → 20MB for 4 songs
- Compressed embeddings: 50KB per song → 200KB for 4 songs
- **100x storage savings**
- Recomputing from audio takes only 2-3 seconds (acceptable)

**Trade-off:** Cached runs take 30s instead of 5s (still 95% faster than 10 min)

### Why No TTL/Expiration?

**Choice:** Cache never expires

**Rationale:**
- Audio files are static (rarely change)
- Content hash auto-invalidates on changes
- Explicit deletion is clearer than time-based expiration
- Avoid "works on my machine" issues due to cache age

---

## 11. Future Enhancements

### Phase 2 (Optional)

1. **Cache Statistics**
   - CLI command: `python poc_analysis_allinone.py --cache-stats`
   - Show: hit rate, total entries, disk usage, oldest entry

2. **Cache Management**
   - `--clear-cache` flag to delete all cache
   - `--rebuild-cache` to force re-analysis
   - `--cache-dir` to specify custom cache location

3. **Compression**
   - Gzip cache files to reduce storage by 70%
   - Trade: Smaller files vs slight CPU overhead

4. **Parallel Cache Loading**
   - Load multiple cache files concurrently
   - Reduce 30s → 10s for large song libraries

5. **Shared Cache**
   - Team-shared cache via S3/cloud storage
   - Avoid duplicate analysis across developers

---

## 12. References

**Related Files:**
- `/home/mhuang/Development/stream_of_worship/poc/poc_analysis_allinone.py` (main implementation)
- `/home/mhuang/Development/stream_of_worship/specs/allinone-integration-plan.md` (original allin1 design)
- `/home/mhuang/Development/stream_of_worship/poc/poc_analysis.py` (librosa baseline for comparison)

**External References:**
- SHA256 algorithm: [FIPS 180-4](https://csrc.nist.gov/publications/detail/fips/180/4/final)
- Base64 encoding: [RFC 4648](https://tools.ietf.org/html/rfc4648)
- Content-addressable storage: [Git internals](https://git-scm.com/book/en/v2/Git-Internals-Git-Objects)
