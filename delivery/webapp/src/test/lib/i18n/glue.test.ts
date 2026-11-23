import { describe, it, expect } from "vitest";

import { LOCALES, messages } from "@/lib/i18n/messages";

const GLUE_KEYS = Object.keys(messages.en).filter((key) => key.startsWith("glue."));
const JARGON = ["glue", "take", "descriptor", "job", "prompt"];

describe("glue i18n", () => {
  it("defines the AI-transition namespace in both locales with identical keys", () => {
    expect(GLUE_KEYS.length).toBeGreaterThan(20);
    for (const key of GLUE_KEYS) {
      expect(messages["zh-Hant"][key as keyof typeof messages.en]).toBeTruthy();
    }
  });

  it("uses the approved English wording and never leaks internal vocabulary", () => {
    expect(messages.en["glue.title"]).toBe("AI transition");
    for (const locale of LOCALES) {
      for (const key of GLUE_KEYS) {
        const value = messages[locale][key as keyof typeof messages.en].toLowerCase();
        for (const term of JARGON) {
          expect(value, `${locale}:${key}`).not.toContain(term);
        }
      }
    }
  });

  it("uses respectful Traditional Chinese register and full-width punctuation", () => {
    expect(messages["zh-Hant"]["glue.reason.unsavedDraft"]).toContain("你");
    expect(messages["zh-Hant"]["glue.reason.stale"]).toContain("敬拜歌單");
    expect(messages["zh-Hant"]["glue.reason.unsavedDraft"]).toContain("。");
    expect(messages["zh-Hant"]["glue.reason.unsavedDraft"]).not.toContain(",");
  });
});
