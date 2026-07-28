# Database Network Transfer Analysis Report

## Overview
Analysis of the codebase across `delivery/webapp`, `ops`, and `lab` modules to identify causes for high database network transfer (5.5 GB) relative to low storage usage (0.17 GB).

## Primary Bottlenecks

### 1. Over-fetching in Song Lists (`delivery/webapp/src/lib/db/songs.ts`)
The most significant source of bandwidth consumption is the use of "select all" patterns on high-traffic endpoints.
- **Issue:** `listSongs` and `searchSongs` use `db.query.songs.findMany` with `with: { recordings: true }`.
- **Technical Detail:** This retrieves every column from the `songs` and `recordings` tables for every item in the result set (up to 50 songs per page). The `recordings` table contains numerous analytical and confidence columns that are not required for the list view.
- **Impact:** Every search or scroll operation transmits significant redundant data over the network.

### 2. Expensive Aggregations in Songset Summaries (`delivery/webapp/src/lib/db/songsets.ts`)
The `listSongsetSummaries` function performs complex operations to derive state and totals.
- **Issue:** It joins `songsets` $\to$ `songsetItems` $\to$ `recordings` $\to$ `renderJobs` and calculates sums/counts using filter clauses.
- **Technical Detail:** These joins can result in large intermediate datasets being processed and transmitted, especially when calculating `durationSeconds` for multiple songsets per page.
- **Impact:** High overhead for the "My Songsets" view.

### 3. Redundant Round-trips for Pagination
- **Issue:** Both `listSongs` and `searchSongs` execute two distinct queries: one for the data and one for the total count.
- **Impact:** Doubling the number of request/response cycles for every paginated search.

### 4. Vector Search Data Volume (`delivery/webapp/src/lib/db/songs.ts`)
- **Issue:** `semanticSearchSongs` fetches full song and recording rows for vector results.
- **Impact:** While the search itself is efficient, the data returned is un-projected, leading to unnecessary transfer of analytical metadata.

## Recommended Optimizations

### Short-Term (High Impact)
- **Switch to Projections:** Replace `db.query...findMany({ with: ... })` with explicit `db.select({ ... }).from(...)` calls in `listSongs` and `searchSongs` to fetch only the columns displayed in the UI.
- **Projection for Semantic Search:** Apply similar column restrictions to `semanticSearchSongs`.

### Mid-Term (Structural)
- **Denormalize Totals:** Add `item_count` and `total_duration` columns to the `songsets` table to eliminate complex joins during listing.
- **API Optimization:** Implement server-side caching for common search queries or improve client-side debouncing to reduce the total number of requests.

## Conclusion
The network transfer is not driven by the *amount* of data stored, but by the *volume* of data retrieved per request. By moving from "Fetch All" to "Fetch Required," the database bandwidth can likely be reduced by 80-90%.
