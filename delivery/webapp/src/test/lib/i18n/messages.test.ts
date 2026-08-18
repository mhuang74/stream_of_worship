import { describe, it, expect } from "vitest";
import { LOCALES, messages } from "@/lib/i18n/messages";

describe("i18n messages", () => {
  it("has exactly the two supported locales", () => {
    expect([...LOCALES].sort()).toEqual(["en", "zh-Hant"]);
  });

  it("defines every key in both locales (no orphans, no blanks)", () => {
    const enKeys = Object.keys(messages.en).sort();
    const zhKeys = Object.keys(messages["zh-Hant"]).sort();
    expect(zhKeys).toEqual(enKeys);
  });

  it("has a non-empty translation for every key in every locale", () => {
    for (const locale of LOCALES) {
      for (const key of Object.keys(messages.en)) {
        const value = messages[locale][key as keyof typeof messages.en];
        expect(value.trim(), `${locale}:${key}`).toBeTruthy();
      }
    }
  });
});
