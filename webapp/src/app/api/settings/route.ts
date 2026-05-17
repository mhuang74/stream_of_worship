import { NextRequest, NextResponse } from "next/server";
import { auth } from "@/lib/auth";
import { db } from "@/db";
import { userSettings } from "@/db/schema";
import { eq } from "drizzle-orm";

const DEFAULTS = {
  offlineAutoCache: true,
  defaultGapBeats: 2.0,
  defaultVideoTemplate: "dark",
  defaultResolution: "720p",
  lyricsLoopWindowSeconds: 3.0,
  defaultFontSizePreset: "M",
  defaultKeyShiftSemitones: 0,
  timingReviewFont: "sans",
} as const;

const VALID_TEMPLATES = ["dark", "gradient_warm", "gradient_blue"] as const;
const VALID_RESOLUTIONS = ["720p", "1080p"] as const;
const VALID_FONT_PRESETS = ["S", "M", "L", "XL"] as const;
const VALID_FONTS = ["sans", "mono", "serif"] as const;

/**
 * GET /api/settings
 * Returns authenticated user's settings (defaults if not yet saved).
 */
export async function GET(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = Number(session.user.id);

    const rows = await db
      .select()
      .from(userSettings)
      .where(eq(userSettings.userId, userId));

    if (rows.length === 0) {
      return NextResponse.json({ settings: { userId, ...DEFAULTS } });
    }

    const row = rows[0];
    return NextResponse.json({
      settings: {
        userId: row.userId,
        offlineAutoCache: row.offlineAutoCache,
        defaultGapBeats: row.defaultGapBeats,
        defaultVideoTemplate: row.defaultVideoTemplate,
        defaultResolution: row.defaultResolution,
        lyricsLoopWindowSeconds: row.lyricsLoopWindowSeconds,
        defaultFontSizePreset: row.defaultFontSizePreset,
        defaultKeyShiftSemitones: row.defaultKeyShiftSemitones,
        timingReviewFont: row.timingReviewFont,
      },
    });
  } catch (error) {
    console.error("Error fetching settings:", error);
    return NextResponse.json({ error: "Failed to fetch settings" }, { status: 500 });
  }
}

/**
 * PUT /api/settings
 * Upserts authenticated user's settings.
 */
export async function PUT(request: NextRequest) {
  try {
    const session = await auth.api.getSession({ headers: request.headers });
    if (!session?.user) {
      return NextResponse.json({ error: "Unauthorized" }, { status: 401 });
    }

    const userId = Number(session.user.id);

    let body: unknown;
    try {
      body = await request.json();
    } catch {
      return NextResponse.json({ error: "Invalid JSON body" }, { status: 400 });
    }

    if (!body || typeof body !== "object") {
      return NextResponse.json({ error: "Invalid request body" }, { status: 400 });
    }

    const b = body as Record<string, unknown>;

    // Validate each field if provided
    if (
      b.defaultVideoTemplate !== undefined &&
      !VALID_TEMPLATES.includes(b.defaultVideoTemplate as (typeof VALID_TEMPLATES)[number])
    ) {
      return NextResponse.json(
        { error: `defaultVideoTemplate must be one of: ${VALID_TEMPLATES.join(", ")}` },
        { status: 400 }
      );
    }

    if (
      b.defaultResolution !== undefined &&
      !VALID_RESOLUTIONS.includes(b.defaultResolution as (typeof VALID_RESOLUTIONS)[number])
    ) {
      return NextResponse.json(
        { error: `defaultResolution must be one of: ${VALID_RESOLUTIONS.join(", ")}` },
        { status: 400 }
      );
    }

    if (
      b.defaultFontSizePreset !== undefined &&
      !VALID_FONT_PRESETS.includes(b.defaultFontSizePreset as (typeof VALID_FONT_PRESETS)[number])
    ) {
      return NextResponse.json(
        { error: `defaultFontSizePreset must be one of: ${VALID_FONT_PRESETS.join(", ")}` },
        { status: 400 }
      );
    }

    if (
      b.timingReviewFont !== undefined &&
      !VALID_FONTS.includes(b.timingReviewFont as (typeof VALID_FONTS)[number])
    ) {
      return NextResponse.json(
        { error: `timingReviewFont must be one of: ${VALID_FONTS.join(", ")}` },
        { status: 400 }
      );
    }

    if (
      b.defaultGapBeats !== undefined &&
      (typeof b.defaultGapBeats !== "number" || b.defaultGapBeats < 0 || b.defaultGapBeats > 16)
    ) {
      return NextResponse.json(
        { error: "defaultGapBeats must be a number between 0 and 16" },
        { status: 400 }
      );
    }

    if (
      b.lyricsLoopWindowSeconds !== undefined &&
      (typeof b.lyricsLoopWindowSeconds !== "number" ||
        b.lyricsLoopWindowSeconds < 1 ||
        b.lyricsLoopWindowSeconds > 30)
    ) {
      return NextResponse.json(
        { error: "lyricsLoopWindowSeconds must be a number between 1 and 30" },
        { status: 400 }
      );
    }

    if (
      b.defaultKeyShiftSemitones !== undefined &&
      (typeof b.defaultKeyShiftSemitones !== "number" ||
        !Number.isInteger(b.defaultKeyShiftSemitones) ||
        b.defaultKeyShiftSemitones < -6 ||
        b.defaultKeyShiftSemitones > 6)
    ) {
      return NextResponse.json(
        { error: "defaultKeyShiftSemitones must be an integer between -6 and 6" },
        { status: 400 }
      );
    }

    const now = new Date();

    const values = {
      userId,
      offlineAutoCache:
        typeof b.offlineAutoCache === "boolean" ? b.offlineAutoCache : DEFAULTS.offlineAutoCache,
      defaultGapBeats:
        typeof b.defaultGapBeats === "number" ? b.defaultGapBeats : DEFAULTS.defaultGapBeats,
      defaultVideoTemplate:
        typeof b.defaultVideoTemplate === "string"
          ? b.defaultVideoTemplate
          : DEFAULTS.defaultVideoTemplate,
      defaultResolution:
        typeof b.defaultResolution === "string" ? b.defaultResolution : DEFAULTS.defaultResolution,
      lyricsLoopWindowSeconds:
        typeof b.lyricsLoopWindowSeconds === "number"
          ? b.lyricsLoopWindowSeconds
          : DEFAULTS.lyricsLoopWindowSeconds,
      defaultFontSizePreset:
        typeof b.defaultFontSizePreset === "string"
          ? b.defaultFontSizePreset
          : DEFAULTS.defaultFontSizePreset,
      defaultKeyShiftSemitones:
        typeof b.defaultKeyShiftSemitones === "number"
          ? b.defaultKeyShiftSemitones
          : DEFAULTS.defaultKeyShiftSemitones,
      timingReviewFont:
        typeof b.timingReviewFont === "string" ? b.timingReviewFont : DEFAULTS.timingReviewFont,
      updatedAt: now,
    };

    await db
      .insert(userSettings)
      .values({ ...values, createdAt: now })
      .onConflictDoUpdate({
        target: userSettings.userId,
        set: { ...values },
      });

    return NextResponse.json({ settings: values });
  } catch (error) {
    console.error("Error saving settings:", error);
    return NextResponse.json({ error: "Failed to save settings" }, { status: 500 });
  }
}
