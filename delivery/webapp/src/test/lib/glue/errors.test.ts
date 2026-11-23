import { describe, expect, it } from "vitest";

import {
  GLUE_ERROR_CODES,
  GlueJobRequestError,
  glueErrorHttpStatus,
  glueErrorMessageKey,
  isGlueErrorCode,
  sanitizeGlueError,
} from "@/lib/glue/errors";
import { messages } from "@/lib/i18n/messages";

// Every stable code must map to a real key in both locales; a missing key would
// render the raw i18n id in the editor.
const EN: Readonly<Record<string, string | undefined>> = messages.en;

describe("glue error vocabulary", () => {
  it("maps every stable code to an existing en key", () => {
    for (const code of GLUE_ERROR_CODES) {
      const key = glueErrorMessageKey(code);
      expect(key.startsWith("glue."), code).toBe(true);
      expect(typeof EN[key], `${code} -> ${key}`).toBe("string");
    }
  });

  it("falls back to the generic unknown key for an unrecognized value", () => {
    expect(glueErrorMessageKey("NOPE")).toBe("glue.error.unknown");
    expect(glueErrorMessageKey("")).toBe("glue.error.unknown");
    expect(typeof EN["glue.error.unknown"]).toBe("string");
  });

  it("assigns the contract HTTP statuses", () => {
    expect(glueErrorHttpStatus("GLUE_NOT_CONFIGURED")).toBe(503);
    expect(glueErrorHttpStatus("GLUE_DISPATCH_FAILED")).toBe(503);
    expect(glueErrorHttpStatus("GLUE_DAILY_LIMIT")).toBe(429);
    expect(glueErrorHttpStatus("GLUE_DURATION_TOO_LONG")).toBe(422);
    expect(glueErrorHttpStatus("GLUE_EXCERPT_TOO_SHORT")).toBe(422);
    expect(glueErrorHttpStatus("GLUE_ACTIVE_JOB")).toBe(409);
    expect(glueErrorHttpStatus("GLUE_PAID_NOT_CANCELLABLE")).toBe(409);
    expect(glueErrorHttpStatus("GLUE_STALE_SNAPSHOT")).toBe(409);
  });

  it("recognizes only allowlisted codes", () => {
    expect(isGlueErrorCode("GLUE_ACTIVE_JOB")).toBe(true);
    expect(isGlueErrorCode("glue.active_job")).toBe(false);
    expect(isGlueErrorCode(42)).toBe(false);
  });

  it("carries the sanitized detail on a typed failure", () => {
    const error = new GlueJobRequestError("GLUE_DISPATCH_FAILED", "x", {
      jobId: "job-1",
      detail: "PUT https://api.elevenlabs.io/v1/x Bearer sk_live_abcdefgh1234",
    });
    expect(error.status).toBe(503);
    expect(error.jobId).toBe("job-1");
    expect(error.detail).not.toContain("http");
    expect(error.detail).not.toContain("sk_live");
  });
});

describe("sanitizeGlueError", () => {
  it("passes an allowlisted code through unchanged", () => {
    expect(sanitizeGlueError("GLUE_WORKER_FAILED")).toBe("GLUE_WORKER_FAILED");
  });

  it("returns undefined for empty input", () => {
    expect(sanitizeGlueError(null)).toBeUndefined();
    expect(sanitizeGlueError("")).toBeUndefined();
    expect(sanitizeGlueError("   ")).toBeUndefined();
    expect(sanitizeGlueError({})).toBeUndefined();
  });

  it("redacts URLs, signed query strings, and storage schemes", () => {
    expect(
      sanitizeGlueError("failed at https://api.elevenlabs.io/v1/text-to-music?key=abc")
    ).not.toContain("elevenlabs.io");
    expect(sanitizeGlueError("see s3://sow-audio/glues/1/jobs/2/response.pcm")).not.toContain(
      "sow-audio"
    );
  });

  it("redacts filesystem paths", () => {
    expect(sanitizeGlueError("ENOENT open /tmp/sow/glues/1/response.pcm")).not.toContain(
      "/tmp/sow"
    );
    expect(sanitizeGlueError("write C:\\work\\glue\\response.pcm failed")).not.toContain(
      "C:\\work"
    );
  });

  it("redacts API keys and bearer tokens", () => {
    for (const secret of [
      "sk_live_abcdefgh12345678",
      "Bearer abc.def.ghi",
      "api_key=supersecretvalue",
      "xi-api-key: verysecret",
      "secret_access_key=abc123",
    ]) {
      const output = sanitizeGlueError(`provider rejected ${secret} (401)`) ?? "";
      expect(output).not.toContain(secret.split(/[ =:]/).pop());
      expect(output).not.toContain("verysecret");
    }
  });

  it("strips stack frames", () => {
    const output = sanitizeGlueError(
      "TypeError: boom\n    at upload (/var/task/app.py:42:9)\n    at main (index.js:1:1)"
    );
    expect(output).not.toContain("at upload");
    expect(output).not.toContain("app.py:42");
    expect(output?.split("\n").length).toBe(1);
  });

  it("collapses provider bodies and caps length", () => {
    const body = `{"detail":{"status":"invalid_request","message":"${"x".repeat(900)}"}}`;
    const output = sanitizeGlueError(body) ?? "";
    expect(output.length).toBeLessThanOrEqual(400);
    expect(output).not.toContain("\n");
  });

  it("keeps a plain user-safe sentence intact", () => {
    expect(sanitizeGlueError("The provider took too long to respond.")).toBe(
      "The provider took too long to respond."
    );
  });

  it("reads an Error's message", () => {
    expect(sanitizeGlueError(new Error("Network request failed"))).toBe(
      "Network request failed"
    );
  });
});
