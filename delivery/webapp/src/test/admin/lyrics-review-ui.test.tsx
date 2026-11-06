import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LyricsReviewClient } from "@/app/admin/lyrics-review/LyricsReviewClient";
import { messages } from "@/lib/i18n/messages";
vi.mock("@/hooks/useLocale", () => ({ useLocale: () => ({ t: (key: keyof typeof messages.en) => messages.en[key] }) }));
const first = { contentHash: "first", title: "第一首", album: "專輯", reviewed: false };
const second = { contentHash: "second", title: "第二首", album: "專輯", reviewed: false };
let synced = true;
let more = false;
let initialFeedback: { rating: string; reason: string; note: null } | null = null;
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  synced = true; more = false; initialFeedback = null;
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  Element.prototype.scrollTo = vi.fn();
  fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method) return new Response(JSON.stringify({ feedback: init.method === "DELETE" ? null : JSON.parse(init.body as string) }));
    if (url.includes("recording=")) return new Response(JSON.stringify({
      recording: url.includes("second") ? second : first, synced, audioUrl: "https://audio.example/song.mp3",
      lines: synced ? [{ timeSeconds: 1, text: "第一行" }, { timeSeconds: 5, text: "" }, { timeSeconds: 12.5, text: "第二行" }] : [], feedback: initialFeedback,
    }));
    return new Response(JSON.stringify({ recordings: url.includes("offset=2") ? [{ ...first, contentHash: "third", title: "第三首" }] : [first, second], hasMore: more && !url.includes("offset=2") }));
  });
  vi.stubGlobal("fetch", fetcher);
});
async function openFirst() {
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await screen.findByText("第一行");
}
it("seeks by lyric timestamp and progress slider, preserving blank gap rows", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  fireEvent.click(screen.getByText("第二行"));
  expect(audio.currentTime).toBe(12.5);
  fireEvent.change(screen.getByRole("slider", { name: "Playback position" }), { target: { value: "45.25" } });
  expect(audio.currentTime).toBe(45.25);
  expect(screen.getByText("Instrumental / blank Lyrics")).toBeInTheDocument();
  fireEvent.wheel(screen.getByText("第一行").parentElement!.parentElement!);
  expect(screen.getByRole("button", { name: "Follow playback" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Follow playback" }));
  expect(screen.queryByRole("button", { name: "Follow playback" })).not.toBeInTheDocument();
});
it("saves Good without navigating, permits retraction, and advances only on Next", async () => {
  await openFirst();
  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  expect(screen.getByRole("heading", { name: "第一首" })).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("recording=first"), expect.objectContaining({ method: "PUT" }));
  fireEvent.click(screen.getByRole("button", { name: "Retract feedback" }));
  await screen.findByText("Feedback retracted");
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "第二首" });
});
it("saves Poor reason and note", async () => {
  await openFirst();
  fireEvent.click(screen.getByRole("button", { name: "Poor" }));
  fireEvent.change(screen.getByRole("combobox", { name: "Reason" }), { target: { value: "wrong_text" } });
  fireEvent.change(screen.getByRole("textbox", { name: "Optional note" }), { target: { value: "line two" } });
  fireEvent.click(screen.getByRole("button", { name: "Save Poor feedback" }));
  await screen.findByText("Saved");
  expect(fetcher).toHaveBeenCalledWith(expect.any(String), expect.objectContaining({ body: JSON.stringify({ rating: "sad", reason: "wrong_text", note: "line two" }) }));
});
it("requires timed lyrics for Good and offers missing feedback instead", async () => {
  synced = false;
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await screen.findByText("Timed Lyrics missing");
  expect(screen.getByRole("button", { name: "Good" })).toBeDisabled();
  fireEvent.click(screen.getByRole("button", { name: "Poor" }));
  expect(screen.getByRole("combobox", { name: "Reason" })).toHaveValue("missing");
  expect(screen.queryByRole("option", { name: "Timing wrong" })).not.toBeInTheDocument();
});
it("keeps failures distinct from missing lyrics", async () => {
  fetcher.mockImplementation(async (url: string) => url.includes("recording=") ? new Response(null, { status: 503 }) : new Response(JSON.stringify({ recordings: [first], hasMore: false })));
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.queryByText("Timed Lyrics missing")).not.toBeInTheDocument();
});

it("loads another page when Next reaches the end of the loaded queue", async () => {
  more = true;
  await openFirst();
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "第二首" });
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("offset=2"), expect.any(Object)));
  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("recording=third"), expect.any(Object)));
});

it("uses a valid reason when old missing feedback predates regenerated timed lyrics", async () => {
  initialFeedback = { rating: "sad", reason: "missing", note: null };
  await openFirst();
  fireEvent.click(screen.getByRole("button", { name: "Poor" }));
  expect(screen.getByRole("combobox", { name: "Reason" })).toHaveValue("timing");
});
