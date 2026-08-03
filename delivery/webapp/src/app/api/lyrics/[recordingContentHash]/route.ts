import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userLrcOverrides, recordings, songs } from "@/db/schema";
import { eq, and } from "drizzle-orm";
import { createR2ClientFromEnv } from "@/lib/r2/client";

export interface LyricsResponse {
  lrcContent: string | null;
  lines: string[] | null;
}

export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ recordingContentHash: string }> }
) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const { recordingContentHash } = await params;

    // Step 1: Check user LRC override
    const override = await db.query.userLrcOverrides.findFirst({
      where: and(
        eq(userLrcOverrides.userId, Number(session.user.id)),
        eq(userLrcOverrides.recordingContentHash, recordingContentHash)
      ),
    });

    if (override?.lrcContent) {
      return NextResponse.json<LyricsResponse>({
        lrcContent: override.lrcContent,
        lines: null,
      });
    }

    // Step 2: Look up recording by contentHash
    const [recording] = await db
      .select({
        hashPrefix: recordings.hashPrefix,
        lrcStatus: recordings.lrcStatus,
        songId: recordings.songId,
      })
      .from(recordings)
      .where(eq(recordings.contentHash, recordingContentHash))
      .limit(1);

    // Step 3: Attempt R2 fetch unless lrcStatus === "missing"
    if (recording && recording.lrcStatus !== "missing") {
      try {
        const r2Client = createR2ClientFromEnv();
        const signedUrlResult = await r2Client.getLrcSignedUrl(recording.hashPrefix);

        const r2Response = await fetch(signedUrlResult.url);
        if (r2Response.ok) {
          const lrcContent = await r2Response.text();
          if (lrcContent) {
            return NextResponse.json<LyricsResponse>({
              lrcContent,
              lines: null,
            });
          }
        }
        // 404 or any non-ok response: fall through silently
      } catch {
        // Network error, DNS failure, R2 not configured, etc.: fall through silently
      }
    }

    // Step 4: Look up songs.lyricsLines via recordings.songId → songs.id join
    if (recording?.songId) {
      const [song] = await db
        .select({
          lyricsLines: songs.lyricsLines,
          lyricsRaw: songs.lyricsRaw,
        })
        .from(songs)
        .where(eq(songs.id, recording.songId))
        .limit(1);

      if (song) {
        if (song.lyricsLines) {
          try {
            const parsed = JSON.parse(song.lyricsLines);
            if (Array.isArray(parsed) && parsed.length > 0) {
              const lines = parsed.filter((l): l is string => typeof l === "string");
              if (lines.length > 0) {
                return NextResponse.json<LyricsResponse>({
                  lrcContent: null,
                  lines,
                });
              }
            }
          } catch {
            console.warn(
              `[lyrics] Failed to parse lyricsLines JSON for recording ${recordingContentHash}`
            );
          }
        }

        // Step 5: Look up songs.lyricsRaw
        if (song.lyricsRaw) {
          return NextResponse.json<LyricsResponse>({
            lrcContent: song.lyricsRaw,
            lines: null,
          });
        }
      }
    }

    // Step 6: All sources exhausted
    return NextResponse.json<LyricsResponse>({
      lrcContent: null,
      lines: null,
    });
  } catch (error) {
    console.error("Error fetching lyrics:", error);
    return NextResponse.json({ error: "Failed to fetch lyrics" }, { status: 500 });
  }
}
