import { describe, it, expect, beforeEach, afterEach, vi } from "vitest";
import {
  saveSongsetListState,
  getSongsetListState,
  songsetsListUrl,
} from "@/lib/songset-list-state";

describe("songset-list-state", () => {
  beforeEach(() => {
    sessionStorage.clear();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("round-trips saved page and search", () => {
    saveSongsetListState(3, "");
    expect(getSongsetListState()).toEqual({ page: 3, search: "" });
  });

  it("builds URL with page and search", () => {
    saveSongsetListState(3, "grace");
    expect(songsetsListUrl()).toBe("/songsets?page=3&search=grace");
  });

  it("builds bare URL when page is 1 and search empty", () => {
    saveSongsetListState(1, "");
    expect(songsetsListUrl()).toBe("/songsets");
  });

  it("omits search when empty but keeps page", () => {
    saveSongsetListState(2, "");
    expect(songsetsListUrl()).toBe("/songsets?page=2");
  });

  it("returns null and bare URL with no saved state", () => {
    expect(getSongsetListState()).toBeNull();
    expect(songsetsListUrl()).toBe("/songsets");
  });

  it("returns null and bare URL for invalid saved state", () => {
    sessionStorage.setItem("sow_songset_list_state", "not-json");
    expect(getSongsetListState()).toBeNull();
    expect(songsetsListUrl()).toBe("/songsets");

    sessionStorage.setItem(
      "sow_songset_list_state",
      JSON.stringify({ page: 0, search: "" })
    );
    expect(getSongsetListState()).toBeNull();

    sessionStorage.setItem(
      "sow_songset_list_state",
      JSON.stringify({ page: 1.5, search: "" })
    );
    expect(getSongsetListState()).toBeNull();

    sessionStorage.setItem(
      "sow_songset_list_state",
      JSON.stringify({ page: "3", search: "" })
    );
    expect(getSongsetListState()).toBeNull();
  });

  it("never throws and degrades to null when storage throws (private mode)", () => {
    const quotaStorage: Storage = {
      get length() {
        return 0;
      },
      clear() {},
      getItem(): string | null {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      },
      key(): string | null {
        return null;
      },
      removeItem(): void {},
      setItem(): void {
        throw new DOMException("QuotaExceededError", "QuotaExceededError");
      },
    };
    vi.stubGlobal("sessionStorage", quotaStorage);

    expect(() => saveSongsetListState(3, "grace")).not.toThrow();
    expect(getSongsetListState()).toBeNull();
    expect(songsetsListUrl()).toBe("/songsets");
  });
});
