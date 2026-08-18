import { describe, it, expect } from "vitest";
import { LOCALES, messages, mergeMessages, bundle } from "@/lib/i18n/messages";

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

  it("throws when a key is duplicated across bundles (no silent overwrite)", () => {
    const a = bundle({ en: { dup: "a" }, "zh-Hant": { dup: "a" } });
    const b = bundle({ en: { dup: "b" }, "zh-Hant": { dup: "b" } });
    expect(() => mergeMessages(a, b)).toThrow(/duplicate message key "dup"/);
  });
});
