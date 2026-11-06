import { beforeEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { LyricsReviewClient } from "@/app/admin/lyrics-review/LyricsReviewClient";
import { messages } from "@/lib/i18n/messages";
vi.mock("@/hooks/useLocale", () => ({ useLocale: () => ({ t: (key: keyof typeof messages.en) => messages.en[key] }) }));
const first = { contentHash: "first", title: "第一首", album: "專輯", reviewed: false };
const second = { contentHash: "second", title: "第二首", album: "專輯", reviewed: false };
let synced = true;
let more = false;
type Feedback = { rating: "happy" | "sad"; reason: "timing" | "missing" | null; note: null };
let initialFeedback: Feedback | null = null;
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
function expectRating(rating: Feedback["rating"] | null) {
  for (const [name, value] of [["Good", "happy"], ["Poor", "sad"]] as const) {
    const button = screen.getByRole("button", { name });
    const selected = rating === value;
    expect(button).toHaveClass(...(selected ? ["bg-primary", "text-primary-foreground"] : ["border-border", "bg-background"]));
    if (!selected) expect(button).not.toHaveClass("bg-primary");
    expect(button).toHaveAttribute("aria-pressed", String(selected));
  }
}
it.each<Feedback | null>([
  null,
  { rating: "happy", reason: null, note: null },
  { rating: "sad", reason: "timing", note: null },
])("highlights only the saved rating: %j", async (feedback) => {
  initialFeedback = feedback;
  await openFirst();
  expectRating(feedback?.rating ?? null);
  if (feedback) expect(screen.getByText(`Your feedback: ${feedback.rating === "happy" ? "Good" : "Poor"}`)).toBeInTheDocument();
});
it("keeps Good highlighted while editing and saving Poor, then highlights the saved Poor rating", async () => {
  initialFeedback = { rating: "happy", reason: null, note: null };
  await openFirst();
  fireEvent.click(screen.getByRole("button", { name: "Poor" }));
  expectRating("happy");
  expect(screen.getByRole("combobox", { name: "Reason" })).toHaveValue("timing");
  let finishSave!: (response: Response) => void;
  fetcher.mockImplementationOnce(() => new Promise<Response>(resolve => { finishSave = resolve; }));
  fireEvent.click(screen.getByRole("button", { name: "Save Poor feedback" }));
  expectRating("happy");
  expect(screen.getByRole("button", { name: "Good" })).toBeDisabled();
  finishSave(new Response(JSON.stringify({ feedback: { rating: "sad", reason: "timing", note: null } })));
  await screen.findByText("Saved");
  expectRating("sad");
  expect(screen.getByText("Your feedback: Poor")).toBeInTheDocument();
  expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("recording=first"), expect.objectContaining({
    method: "PUT", body: JSON.stringify({ rating: "sad", reason: "timing", note: "" }),
  }));
});
it("replaces Poor with Good and clears both highlights after retraction", async () => {
  initialFeedback = { rating: "sad", reason: "timing", note: null };
  await openFirst();
  expectRating("sad");
  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  expectRating("happy");
  expect(screen.getByText("Your feedback: Good")).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Retract feedback" }));
  await screen.findByText("Feedback retracted");
  expectRating(null);
  expect(screen.queryByText(/Your feedback:/)).not.toBeInTheDocument();
});
it.each([
  { saved: "happy", action: "Save Poor feedback", method: "PUT" },
  { saved: "sad", action: "Good", method: "PUT" },
  { saved: "sad", action: "Retract feedback", method: "DELETE" },
] as const)("preserves $saved when $action fails", async ({ saved, action, method }) => {
  initialFeedback = { rating: saved, reason: saved === "sad" ? "timing" : null, note: null };
  await openFirst();
  if (action === "Save Poor feedback") fireEvent.click(screen.getByRole("button", { name: "Poor" }));
  fetcher.mockResolvedValueOnce(new Response(null, { status: 503 }));
  fireEvent.click(screen.getByRole("button", { name: action }));
  await screen.findByRole("alert");
  expect(fetcher).toHaveBeenLastCalledWith(expect.stringContaining("recording=first"), expect.objectContaining({ method }));
  expectRating(saved);
  expect(screen.getByText(`Your feedback: ${saved === "happy" ? "Good" : "Poor"}`)).toBeInTheDocument();
  expect(screen.queryByText("Saved")).not.toBeInTheDocument();
});
it("loads the saved selection for each recording when navigating", async () => {
  initialFeedback = { rating: "sad", reason: "timing", note: null };
  await openFirst();
  expectRating("sad");
  initialFeedback = { rating: "happy", reason: null, note: null };
  fireEvent.click(screen.getByRole("button", { name: "Next" }));
  await screen.findByRole("heading", { name: "第二首" });
  expectRating("happy");
  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));
  initialFeedback = null;
  fireEvent.click(await screen.findByText("第一首"));
  await screen.findByRole("heading", { name: "第一首" });
  expectRating(null);
});
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
