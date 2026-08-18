import { describe, it, expect } from "vitest";
import { formatTotalDuration } from "@/lib/i18n/format";
import { t } from "@/lib/i18n/messages";
import type { TranslationKey } from "@/lib/i18n/messages";

const en = (key: TranslationKey): string => t("en", key);
const zhHant = (key: TranslationKey): string => t("zh-Hant", key);

describe("formatTotalDuration", () => {
  it("returns N/A for null or zero", () => {
    expect(formatTotalDuration(en, null)).toBe("N/A");
    expect(formatTotalDuration(en, 0)).toBe("N/A");
  });

  it("formats under an hour as whole minutes (en)", () => {
    expect(formatTotalDuration(en, 45 * 60)).toBe("45 min");
  });

  it("formats an hour or more as H + zero-padded minutes (en)", () => {
    expect(formatTotalDuration(en, 90 * 60)).toBe("1h 30m");
    expect(formatTotalDuration(en, 2 * 3600 + 5 * 60)).toBe("2h 05m");
  });

  it("swaps the unit labels in Traditional Chinese but stays structurally consistent", () => {
    expect(formatTotalDuration(zhHant, 45 * 60)).toBe("45 分鐘");
    expect(formatTotalDuration(zhHant, 90 * 60)).toBe("1小時 30分");
  });
});
