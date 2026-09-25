import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { createHmac } from "node:crypto";
import { signValidationToken, verifyValidationToken } from "@/lib/lead-validation";

const SECRET = "test-secret";

beforeEach(() => {
  process.env.BETTER_AUTH_SECRET = SECRET;
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  delete process.env.BETTER_AUTH_SECRET;
});

describe("signValidationToken / verifyValidationToken", () => {
  it("roundtrips — verify returns the signed email", () => {
    const token = signValidationToken("lead@example.com");
    expect(token).toMatch(/^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$/);
    expect(verifyValidationToken(token)).toEqual({ email: "lead@example.com" });
  });

  it("rejects a token signed with a different secret", () => {
    const token = signValidationToken("lead@example.com");
    process.env.BETTER_AUTH_SECRET = "other-secret";
    expect(verifyValidationToken(token)).toBeNull();
  });

  it("returns null for an expired token (past the 48h TTL)", () => {
    const token = signValidationToken("lead@example.com");
    const now = Date.now();
    vi.spyOn(Date, "now").mockReturnValue(now + 49 * 60 * 60 * 1000);
    expect(verifyValidationToken(token)).toBeNull();
  });

  it("accepts a token still within its 48h TTL", () => {
    const token = signValidationToken("lead@example.com");
    vi.spyOn(Date, "now").mockReturnValue(Date.now() + 47 * 60 * 60 * 1000);
    expect(verifyValidationToken(token)).toEqual({ email: "lead@example.com" });
  });

  it("returns null when the payload is tampered with", () => {
    const token = signValidationToken("lead@example.com");
    const [payload, sig] = token.split(".");
    // Flip a byte of the payload (swap two base64url chars).
    const tampered = payload.slice(0, -2) + payload.slice(-1) + payload.slice(-2, -1);
    expect(verifyValidationToken(`${tampered}.${sig}`)).toBeNull();
  });

  it("returns null when the signature is tampered with", () => {
    const token = signValidationToken("lead@example.com");
    const [payload, sig] = token.split(".");
    const tampered = sig.slice(0, -2) + sig.slice(-1) + sig.slice(-2, -1);
    expect(verifyValidationToken(`${payload}.${tampered}`)).toBeNull();
  });

  it("returns null on garbage inputs (never throws)", () => {
    for (const garbage of ["", ".", "abc", "abc.def.ghi", "a.b", "not-a-token!", "...."]) {
      expect(() => verifyValidationToken(garbage)).not.toThrow();
      expect(verifyValidationToken(garbage)).toBeNull();
    }
  });

  it("returns null when the payload decodes but is not the expected shape", () => {
    const payload = Buffer.from(JSON.stringify({ e: 42 })).toString("base64url");
    const sig = createHmac("sha256", SECRET).update(payload).digest("base64url");
    expect(verifyValidationToken(`${payload}.${sig}`)).toBeNull();
  });

  it("sign throws without BETTER_AUTH_SECRET; verify returns null (never throws)", () => {
    delete process.env.BETTER_AUTH_SECRET;
    expect(() => signValidationToken("lead@example.com")).toThrow(
      "BETTER_AUTH_SECRET not configured"
    );
    expect(verifyValidationToken("anything.atall")).toBeNull();
  });
});
