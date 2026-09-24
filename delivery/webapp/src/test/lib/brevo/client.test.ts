import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  upsertContact,
  sendConfirmationEmail,
} from "@/lib/brevo/client";

// Stub global fetch so the wrapper's REST request shapes can be asserted
// without any network traffic.
const fetchMock = vi.fn(async () =>
  new Response(JSON.stringify({ id: 1 }), { status: 201 })
);

const SIGNUP_URL = "https://streamofworship.com/register?email=visitor%40example.com";

function lastCall(): { url: string; init: RequestInit } {
  const [url, init] = fetchMock.mock.calls[fetchMock.mock.calls.length - 1];
  return { url, init };
}

describe("upsertContact", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", fetchMock);
    process.env.BREVO_API_KEY = "test-key";
    delete process.env.BREVO_LIST_ID;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.BREVO_API_KEY;
    delete process.env.BREVO_LIST_ID;
  });

  it("POSTs the contact with api-key header and updateEnabled", async () => {
    await upsertContact({ email: "visitor@example.com", source: "landing-page" });

    expect(fetchMock).toHaveBeenCalledOnce();
    const { url, init } = lastCall();
    expect(url).toBe("https://api.brevo.com/v3/contacts");
    expect(init.method).toBe("POST");
    const headers = init.headers as Record<string, string>;
    expect(headers["api-key"]).toBe("test-key");
    expect(headers["content-type"]).toBe("application/json");

    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      email: "visitor@example.com",
      updateEnabled: true,
      attributes: { SOURCE: "landing-page" },
    });
  });

  it("omits LOCALE/VARIANT attributes when empty or undefined", async () => {
    await upsertContact({ email: "v@example.com", source: "landing-page", locale: "", variant: "" });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.attributes).toEqual({ SOURCE: "landing-page" });
    expect(body.attributes.LOCALE).toBeUndefined();
    expect(body.attributes.VARIANT).toBeUndefined();
  });

  it("includes LOCALE/VARIANT attributes when provided", async () => {
    await upsertContact({
      email: "v@example.com",
      source: "landing-page",
      locale: "zh-Hant",
      variant: "a",
    });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.attributes).toEqual({
      SOURCE: "landing-page",
      LOCALE: "zh-Hant",
      VARIANT: "a",
    });
  });

  it("includes listIds only when BREVO_LIST_ID is set", async () => {
    await upsertContact({ email: "v@example.com", source: "landing-page" });
    expect(JSON.parse(lastCall().init.body as string).listIds).toBeUndefined();

    process.env.BREVO_LIST_ID = "42";
    await upsertContact({ email: "v@example.com", source: "landing-page" });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.listIds).toEqual([42]);
  });

  it("never throws on a non-OK response (logged, swallowed)", async () => {
    fetchMock.mockResolvedValueOnce(
      new Response("boom", { status: 500 })
    );
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(
      upsertContact({ email: "v@example.com", source: "landing-page" })
    ).resolves.toBeUndefined();
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("never throws when fetch itself rejects", async () => {
    fetchMock.mockRejectedValueOnce(new Error("network down"));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(
      upsertContact({ email: "v@example.com", source: "landing-page" })
    ).resolves.toBeUndefined();
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("is no-op-safe when BREVO_API_KEY is unset (no fetch, no throw)", async () => {
    delete process.env.BREVO_API_KEY;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    await expect(
      upsertContact({ email: "v@example.com", source: "landing-page" })
    ).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("skipping contact upsert for v@example.com")
    );
    warnSpy.mockRestore();
  });
});

describe("sendConfirmationEmail", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.stubGlobal("fetch", fetchMock);
    process.env.BREVO_API_KEY = "test-key";
    delete process.env.BREVO_TEMPLATE_ID;
    delete process.env.BREVO_FROM_ADDRESS;
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    delete process.env.BREVO_API_KEY;
    delete process.env.BREVO_TEMPLATE_ID;
    delete process.env.BREVO_FROM_ADDRESS;
  });

  it("POSTs the template send with templateId, to and params", async () => {
    process.env.BREVO_TEMPLATE_ID = "7";
    await sendConfirmationEmail({
      to: "visitor@example.com",
      locale: "en",
      signupUrl: SIGNUP_URL,
    });

    expect(fetchMock).toHaveBeenCalledOnce();
    const { url, init } = lastCall();
    expect(url).toBe("https://api.brevo.com/v3/smtp/email");
    expect(init.method).toBe("POST");
    expect((init.headers as Record<string, string>)["api-key"]).toBe("test-key");

    const body = JSON.parse(init.body as string);
    expect(body).toEqual({
      templateId: 7,
      to: [{ email: "visitor@example.com" }],
      params: { email: "visitor@example.com", signupUrl: SIGNUP_URL },
    });
  });

  it("falls back to inline HTML when no template is configured, embedding the signup URL as a link", async () => {
    await sendConfirmationEmail({
      to: "visitor@example.com",
      locale: "en",
      signupUrl: SIGNUP_URL,
    });

    const body = JSON.parse(lastCall().init.body as string);
    expect(body.templateId).toBeUndefined();
    expect(body.sender).toEqual({
      email: "noreply@streamofworship.com",
      name: "Stream of Worship",
    });
    expect(body.to).toEqual([{ email: "visitor@example.com" }]);
    expect(body.subject).toContain("Stream of Worship");
    expect(body.htmlContent).toContain(`href="${SIGNUP_URL}"`);
  });

  it("renders Traditional Chinese fallback copy for zh-Hant", async () => {
    await sendConfirmationEmail({
      to: "visitor@example.com",
      locale: "zh-Hant",
      signupUrl: SIGNUP_URL,
    });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.subject).toMatch(/[\u4e00-\u9fff]/);
    expect(body.htmlContent).toContain(`href="${SIGNUP_URL}"`);
  });

  it("renders English (non-Chinese) fallback copy for other locales", async () => {
    await sendConfirmationEmail({
      to: "visitor@example.com",
      locale: "en",
      signupUrl: SIGNUP_URL,
    });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.subject).not.toMatch(/[\u4e00-\u9fff]/);
  });

  it("parses BREVO_FROM_ADDRESS into the fallback sender", async () => {
    process.env.BREVO_FROM_ADDRESS = "Custom <hello@custom.example>";
    await sendConfirmationEmail({
      to: "v@example.com",
      locale: "en",
      signupUrl: SIGNUP_URL,
    });
    const body = JSON.parse(lastCall().init.body as string);
    expect(body.sender).toEqual({ email: "hello@custom.example", name: "Custom" });
  });

  it("never throws on a non-OK response (logged, swallowed)", async () => {
    fetchMock.mockResolvedValueOnce(new Response("boom", { status: 502 }));
    const errorSpy = vi.spyOn(console, "error").mockImplementation(() => {});
    await expect(
      sendConfirmationEmail({ to: "v@example.com", locale: "en", signupUrl: SIGNUP_URL })
    ).resolves.toBeUndefined();
    expect(errorSpy).toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("is no-op-safe when BREVO_API_KEY is unset (no fetch, no throw)", async () => {
    delete process.env.BREVO_API_KEY;
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    await expect(
      sendConfirmationEmail({ to: "v@example.com", locale: "en", signupUrl: SIGNUP_URL })
    ).resolves.toBeUndefined();
    expect(fetchMock).not.toHaveBeenCalled();
    expect(warnSpy).toHaveBeenCalledWith(
      expect.stringContaining("skipping confirmation email to v@example.com")
    );
    warnSpy.mockRestore();
  });

  it.each(["-1", "0"])(
    "treats a non-positive BREVO_TEMPLATE_ID (%s) as unset so the send still falls back",
    async (value) => {
      process.env.BREVO_TEMPLATE_ID = value;
      await sendConfirmationEmail({
        to: "v@example.com",
        locale: "en",
        signupUrl: SIGNUP_URL,
      });
      const body = JSON.parse(lastCall().init.body as string);
      // A template send with a bogus ID would be rejected by Brevo and the
      // lead would get nothing; the inline fallback keeps the confirmation.
      expect(body.templateId).toBeUndefined();
      expect(body.htmlContent).toContain(`href="${SIGNUP_URL}"`);
    }
  );
});
