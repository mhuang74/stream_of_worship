import { describe, it, expect } from "vitest";
import { parseAcceptLanguage } from "@/lib/i18n/accept-language";

describe("parseAcceptLanguage", () => {
  it("returns en for a null/empty header", () => {
    expect(parseAcceptLanguage(null)).toBe("en");
    expect(parseAcceptLanguage("")).toBe("en");
  });

  it("maps any zh* tag to zh-Hant (first match wins)", () => {
    expect(parseAcceptLanguage("zh-TW,en-US;q=0.9")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh,en;q=0.9")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh-CN")).toBe("zh-Hant");
    expect(parseAcceptLanguage("zh-Hant")).toBe("zh-Hant");
  });

  it("returns en for en or unknown tags", () => {
    expect(parseAcceptLanguage("en-US")).toBe("en");
    expect(parseAcceptLanguage("fr-FR,de;q=0.8")).toBe("en");
    expect(parseAcceptLanguage("ja,en;q=0.5")).toBe("en");
  });
});
