import { describe, it, expect } from "vitest";
import {
  sanitizeRenderErrorMessage,
  formatRenderFailedAt,
  getRenderFailureText,
} from "@/lib/render/error-message";

describe("sanitizeRenderErrorMessage", () => {
  it("returns null for non-string input", () => {
    expect(sanitizeRenderErrorMessage(null)).toBeNull();
    expect(sanitizeRenderErrorMessage(undefined)).toBeNull();
    expect(sanitizeRenderErrorMessage(123)).toBeNull();
  });

  it("returns null for empty string", () => {
    expect(sanitizeRenderErrorMessage("")).toBeNull();
  });

  it("returns null for whitespace-only string", () => {
    expect(sanitizeRenderErrorMessage("   \n\t  ")).toBeNull();
  });

  it("strips ANSI escape codes", () => {
    const input = "\x1b[31mError: something failed\x1b[0m";
    expect(sanitizeRenderErrorMessage(input)).toBe("Error: something failed");
  });

  it("strips control characters", () => {
    const input = "Error\x00\x07something failed";
    expect(sanitizeRenderErrorMessage(input)).toBe("Errorsomething failed");
  });

  it("uses the first useful non-empty line", () => {
    const input = "\n\n  \nActual error here\n  at SomeTrace\n  at AnotherTrace";
    expect(sanitizeRenderErrorMessage(input)).toBe("Actual error here");
  });

  it("skips traceback framing lines", () => {
    const input =
      "Traceback (most recent call last):\n  File \"app.py\", line 10, in run\nRuntimeError: boom";
    expect(sanitizeRenderErrorMessage(input)).toBe("RuntimeError: boom");
  });

  it("redacts URLs", () => {
    const input = "Failed to fetch https://example.com/api?key=secret";
    const result = sanitizeRenderErrorMessage(input);
    expect(result).not.toContain("https://example.com");
    expect(result).toContain("[url]");
  });

  it("redacts Unix absolute paths", () => {
    const input = "File not found: /usr/local/bin/ffmpeg";
    const result = sanitizeRenderErrorMessage(input);
    expect(result).not.toContain("/usr/local/bin/ffmpeg");
    expect(result).toContain("[path]");
  });

  it("redacts Windows absolute paths", () => {
    const input = "Error in C:\\Users\\admin\\config\\secrets.txt";
    const result = sanitizeRenderErrorMessage(input);
    expect(result).not.toContain("C:\\Users\\admin\\config\\secrets.txt");
    expect(result).toContain("[path]");
  });

  it("redacts secret-like key/value fragments", () => {
    const input = "TOKEN=abc123 API_KEY=xyz PASSWORD=hunter2";
    const result = sanitizeRenderErrorMessage(input);
    expect(result).not.toContain("abc123");
    expect(result).not.toContain("xyz");
    expect(result).not.toContain("hunter2");
    expect(result).toContain("[redacted]");
  });

  it("redacts SOW_*_KEY fragments", () => {
    const input = "SOW_R2_KEY=mysecret";
    const result = sanitizeRenderErrorMessage(input);
    expect(result).not.toContain("mysecret");
    expect(result).toContain("[redacted]");
  });

  it("collapses repeated whitespace", () => {
    const input = "Error:   something   failed";
    expect(sanitizeRenderErrorMessage(input)).toBe("Error: something failed");
  });

  it("truncates long messages to 250 characters with ellipsis", () => {
    const input = "A".repeat(300);
    const result = sanitizeRenderErrorMessage(input);
    expect(result).toHaveLength(250);
    expect(result).toMatch(/…$/);
  });

  it("returns null for fully redacted message", () => {
    const input = "https://example.com";
    expect(sanitizeRenderErrorMessage(input)).toBeNull();
  });
});

describe("formatRenderFailedAt", () => {
  it("formats a date in a readable format", () => {
    const date = new Date("2024-06-15T10:30:00Z");
    const result = formatRenderFailedAt(date);
    expect(result).toContain("Jun");
    expect(result).toContain("15");
    expect(result).toContain("2024");
  });
});

describe("getRenderFailureText", () => {
  it("prefers sanitized error text", () => {
    expect(getRenderFailureText("FFmpeg error: bad codec", null)).toBe(
      "FFmpeg error: bad codec"
    );
  });

  it("falls back to date-based text when error is null", () => {
    const date = new Date("2024-06-15T10:30:00Z");
    const result = getRenderFailureText(null, date);
    expect(result).toContain("Render failed around");
    expect(result).toContain("Please render again.");
  });

  it("falls back to generic text when error and date are null", () => {
    expect(getRenderFailureText(null, null)).toBe(
      "Render failed. Please render again."
    );
  });

  it("falls back to generic text when error is empty string", () => {
    const date = new Date("2024-06-15T10:30:00Z");
    const result = getRenderFailureText("", date);
    expect(result).toContain("Render failed around");
  });
});
