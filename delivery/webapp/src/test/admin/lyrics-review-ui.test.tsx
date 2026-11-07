import { beforeEach, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { LyricsReviewClient } from "@/app/admin/lyrics-review/LyricsReviewClient";
import { messages } from "@/lib/i18n/messages";
import type { ReviewCandidate, ReviewFeedback } from "@/lib/lyrics/review";
let locale: "en" | "zh-Hant" = "en";
vi.mock("@/hooks/useLocale", () => ({ useLocale: () => ({ t: (key: keyof typeof messages.en) => messages[locale][key] }) }));
const first: ReviewCandidate = { contentHash: "first", title: "第一首", album: "專輯", reviewed: false, feedback: [] };
const second: ReviewCandidate = { contentHash: "second", title: "第二首", album: "專輯", reviewed: false, feedback: [] };
let queueRows: ReviewCandidate[];
let synced = true;
let more = false;
type Feedback = { rating: "happy" | "sad"; reason: "timing" | "missing" | null; note: null };
let initialFeedback: Feedback | null = null;
let setRows: { id: string; name: string; total: number; done: number }[] = [];
let fetcher: ReturnType<typeof vi.fn>;
beforeEach(() => {
  synced = true; more = false; initialFeedback = null; setRows = [];
  locale = "en"; queueRows = [first, second];
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "play").mockResolvedValue(undefined);
  Element.prototype.scrollTo = vi.fn();
  fetcher = vi.fn(async (url: string, init?: RequestInit) => {
    if (init?.method) return new Response(JSON.stringify({ feedback: init.method === "DELETE" ? null : JSON.parse(init.body as string) }));
    if (url.endsWith("/sets")) return new Response(JSON.stringify({ sets: setRows }));
    if (url.includes("recording=")) return new Response(JSON.stringify({
      recording: url.includes("second") ? second : first, synced, audioUrl: "https://audio.example/song.mp3",
      lines: synced ? [{ timeSeconds: 1, text: "第一行" }, { timeSeconds: 5, text: "" }, { timeSeconds: 12.5, text: "第二行" }] : [], feedback: initialFeedback,
    }));
    return new Response(JSON.stringify({ recordings: url.includes("offset=2") ? [{ ...first, contentHash: "third", title: "第三首" }] : queueRows, hasMore: more && !url.includes("offset=2") }));
  });
  vi.stubGlobal("fetch", fetcher);
});
async function openFirst() {
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await screen.findByText("第一行");
}
/** Queue requests only: the set-picker refresh shares the same fetcher. */
const queueUrls = () => fetcher.mock.calls
  .map(([url]) => url as string)
  .filter(url => url.startsWith("/api/admin/lyrics-review?"));
function expectRating(rating: Feedback["rating"] | null) {
  for (const [name, value] of [["Good", "happy"], ["Poor", "sad"]] as const) {
    const button = screen.getByRole("button", { name });
    const selected = rating === value;
    expect(button).toHaveClass(...(selected ? ["bg-primary", "text-primary-foreground"] : ["border-border", "bg-background"]));
    if (!selected) expect(button).not.toHaveClass("bg-primary");
    expect(button).toHaveAttribute("aria-pressed", String(selected));
  }
}

it("shows every saved rating only while Show reviewed is checked, including during search", async () => {
  queueRows = [{ ...first, reviewed: true, feedback: [
    { id: "good", rating: "happy", reason: null, note: null },
    { id: "poor", rating: "sad", reason: "timing", note: "Second line is late" },
    { id: "poor-no-note", rating: "sad", reason: "other", note: null },
  ] }, second];
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  fireEvent.change(screen.getByRole("textbox", { name: "Search title or album" }), { target: { value: "第一" } });
  await waitFor(() => expect(queueUrls().at(-1)).toContain("q=%E7%AC%AC%E4%B8%80&reviewed=0"));
  expect(screen.queryByRole("list", { name: "Lyrics Feedback" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("checkbox", { name: "Show reviewed" }));
  const list = await screen.findByRole("list", { name: "Lyrics Feedback" });
  expect(within(list).getAllByRole("listitem").map(item => item.textContent)).toEqual([
    "Good", "Poor · Timing wrongSecond line is late", "Poor · Other",
  ]);
  expect(screen.getAllByRole("list", { name: "Lyrics Feedback" })).toHaveLength(1);
  expect(within(screen.getByRole("button", { name: /第二首/ })).queryByRole("list")).not.toBeInTheDocument();
  await waitFor(() => expect(queueUrls().at(-1)).toContain("reviewed=1"));
  fireEvent.click(screen.getByRole("checkbox", { name: "Show reviewed" }));
  expect(screen.queryByText("Good")).not.toBeInTheDocument();
  expect(screen.queryByText("Second line is late")).not.toBeInTheDocument();
});

it.each((["en", "zh-Hant"] as const).flatMap(language =>
  (["missing", "timing", "wrong_text", "other"] as const).map(reason => ({ language, reason })),
))("translates Poor and $reason in $language and displays a wrapping note", async ({ language, reason }) => {
  locale = language;
  const note = `First line\n${"長".repeat(300)}`;
  queueRows = [{ ...first, reviewed: true, feedback: [
    { id: "poor", rating: "sad", reason, note },
    { id: "good", rating: "happy", reason: null, note: null },
  ] }];
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  fireEvent.click(screen.getByRole("checkbox", { name: messages[language]["review.reviewed"] }));
  const list = await screen.findByRole("list", { name: messages[language]["review.feedback"] });
  const [poor, good] = within(list).getAllByRole("listitem");
  expect(poor).toHaveTextContent(messages[language]["review.poor"]);
  expect(poor).toHaveTextContent(messages[language][`review.${reason}Reason`]);
  expect(poor.textContent).toContain(note);
  expect(poor).toHaveClass("whitespace-pre-wrap", "break-words", "[overflow-wrap:anywhere]");
  expect(good).toHaveTextContent(messages[language]["review.good"]);
});

it("refreshes the displayed feedback after saving and retracting with Show reviewed checked", async () => {
  const defaultFetch = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method) {
      initialFeedback = init.method === "DELETE" ? null : JSON.parse(init.body as string);
      const feedback: ReviewFeedback[] = initialFeedback ? [{ id: "saved", ...initialFeedback }] : [];
      queueRows = [{ ...first, reviewed: feedback.length > 0, feedback }, second];
    }
    return defaultFetch(url, init);
  });
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  fireEvent.click(screen.getByRole("checkbox", { name: "Show reviewed" }));
  await waitFor(() => expect(queueUrls().at(-1)).toContain("reviewed=1"));
  fireEvent.click(screen.getByText("第一首"));
  await screen.findByText("第一行");
  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));
  expect(within(screen.getByRole("list", { name: "Lyrics Feedback" })).getByText("Good")).toBeInTheDocument();
  fireEvent.click(screen.getByText("第一首"));
  fireEvent.click(await screen.findByRole("button", { name: "Retract feedback" }));
  await screen.findByText("Feedback retracted");
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));
  expect(screen.queryByRole("list", { name: "Lyrics Feedback" })).not.toBeInTheDocument();
});
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
  expect(screen.getByText("00:12.50")).toBeInTheDocument();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  fireEvent.click(screen.getByText("第二行"));
  expect(audio.currentTime).toBe(12.5);
  // The custom slider only shows in Full Screen; the normal view uses native chrome.
  fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
  fireEvent.change(screen.getByRole("slider", { name: "Playback position" }), { target: { value: "45.25" } });
  expect(audio.currentTime).toBe(45.25);
  // A timestamped line with no text stays empty: no placeholder text.
  expect(screen.queryByText("Instrumental / blank Lyrics")).not.toBeInTheDocument();
  expect(screen.getByText("00:05.00").closest("button")).toHaveTextContent(/^00:05\.00$/);
  fireEvent.wheel(screen.getByText("第一行").parentElement!.parentElement!);
  expect(screen.queryByRole("button", { name: "Follow playback" })).not.toBeInTheDocument();
});
it("resumes auto-scrolling on its own after a manual scroll", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  const container = screen.getByText("第一行").parentElement!.parentElement!;
  const scrollTo = vi.mocked(Element.prototype.scrollTo);
  vi.useFakeTimers();
  try {
    scrollTo.mockClear();
    fireEvent.wheel(container);
    // Playback moves on while follow is suspended: the view must not chase it.
    audio.currentTime = 12.5;
    fireEvent.timeUpdate(audio);
    expect(scrollTo).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(5000));
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));
  } finally { vi.useRealTimers(); }
});
it("resumes auto-scrolling immediately when a lyric line is tapped", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  const scrollTo = vi.mocked(Element.prototype.scrollTo);
  vi.useFakeTimers();
  try {
    fireEvent.wheel(screen.getByText("第一行").parentElement!.parentElement!);
    scrollTo.mockClear();
    fireEvent.click(screen.getByText("第二行"));
    expect(audio.currentTime).toBe(12.5);
    expect(scrollTo).toHaveBeenCalledWith(expect.objectContaining({ behavior: "smooth" }));
  } finally { vi.useRealTimers(); }
});
it("enters Full Screen with one playing audio element and leaves it on the second tap", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
  const slider = screen.getByRole("slider", { name: "Playback position" });
  expect(screen.queryByRole("button", { name: "Good" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Full screen" })).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "Next" })).not.toBeInTheDocument();
  // Same nodes, so playback never restarts when the layout swaps.
  expect(document.querySelector("audio")).toBe(audio);
  expect(screen.getByRole("slider", { name: "Playback position" })).toBe(slider);
  // One scrubber and one timecode: the native audio chrome is gone in Full Screen.
  expect(screen.getAllByRole("slider")).toHaveLength(1);
  expect(audio).not.toHaveAttribute("controls");
  expect(screen.getByText("00:00 / 02:00")).toBeInTheDocument();
  // Integer seconds in the playback bar and the slider's human-readable value;
  // only the lyric rows carry hundredths.
  audio.currentTime = 19.56;
  fireEvent.timeUpdate(audio);
  expect(screen.getByText("00:19 / 02:00")).toBeInTheDocument();
  expect(screen.getByRole("slider", { name: "Playback position" })).toHaveAttribute("aria-valuetext", "00:19");
  // The bar's play button drives the one audio element and follows its events.
  fireEvent.click(screen.getByRole("button", { name: "Play" }));
  expect(HTMLMediaElement.prototype.play).toHaveBeenCalled();
  fireEvent.play(audio);
  expect(screen.getByRole("button", { name: "Pause" })).toBeInTheDocument();
  fireEvent.pause(audio);
  expect(screen.getByRole("button", { name: "Play" })).toBeInTheDocument();
  // First tap only arms the exit.
  fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
  expect(screen.getByRole("button", { name: "Confirm exit" })).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Confirm exit" }));
  expect(screen.queryByRole("button", { name: "Confirm exit" })).not.toBeInTheDocument();
  expect(screen.getByRole("button", { name: "Good" })).toBeInTheDocument();
  expect(document.querySelector("audio")).toBe(audio);
  expect(audio).toHaveAttribute("controls");
});
it("shows only native audio controls in the normal view", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  expect(audio).toHaveAttribute("controls");
  expect(screen.queryByText("00:00 / 02:00")).not.toBeInTheDocument();
  // The custom slider stays in the DOM (hidden) so its node survives the
  // Full Screen toggle; role queries skip display:none, so query by id.
  expect(screen.queryByRole("slider")).not.toBeInTheDocument();
  expect(document.querySelector("#review-position")).not.toBeVisible();
});
it("seeks from Full Screen and disarms an exit that is never confirmed", async () => {
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  fireEvent.loadedMetadata(audio);
  fireEvent.click(screen.getByRole("button", { name: "Full screen" }));
  fireEvent.click(screen.getByText("第二行"));
  expect(audio.currentTime).toBe(12.5);
  vi.useFakeTimers();
  try {
    fireEvent.click(screen.getByRole("button", { name: "Exit full screen" }));
    expect(screen.getByRole("button", { name: "Confirm exit" })).toBeInTheDocument();
    act(() => vi.advanceTimersByTime(3000));
    expect(screen.getByRole("button", { name: "Exit full screen" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Good" })).not.toBeInTheDocument();
  } finally { vi.useRealTimers(); }
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
it("refreshes the default queue after saving while preserving playback until Next", async () => {
  const defaultFetch = fetcher.getMockImplementation()!;
  let saved = false;
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => {
    if (init?.method === "PUT") saved = true;
    if (url.endsWith("/sets")) return new Response(JSON.stringify({ sets: setRows }));
    if (saved && !init?.method && !url.includes("recording=")) {
      return new Response(JSON.stringify({ recordings: [second], hasMore: false }));
    }
    return defaultFetch(url, init);
  });
  await openFirst();
  const audio = document.querySelector("audio")!;
  Object.defineProperty(audio, "duration", { configurable: true, value: 120 });
  Object.defineProperty(audio, "paused", { configurable: true, value: false });
  audio.currentTime = 37;
  fireEvent.loadedMetadata(audio);
  fireEvent.timeUpdate(audio);
  const pausesBeforeSave = vi.mocked(HTMLMediaElement.prototype.pause).mock.calls.length;

  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  expect(fetcher).toHaveBeenCalledWith(
    "/api/admin/lyrics-review?q=&reviewed=0&offset=0", expect.any(Object),
  );
  expect(screen.getByRole("heading", { name: "第一首" })).toBeInTheDocument();
  expect(document.querySelector("audio")).toBe(audio);
  expect(audio.currentTime).toBe(37);
  expect(audio.paused).toBe(false);
  expect(HTMLMediaElement.prototype.pause).toHaveBeenCalledTimes(pausesBeforeSave);
  expect(fetcher.mock.calls.filter(([url]) => url.includes("recording=")).length).toBe(2);

  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));
  expect(screen.queryByText("第一首")).not.toBeInTheDocument();
  fireEvent.click(screen.getByText("第二首"));
  await screen.findByRole("heading", { name: "第二首" });
});

it("advances from a saved song absent from the refreshed queue only on Next", async () => {
  await openFirst();
  fetcher.mockResolvedValueOnce(new Response(JSON.stringify({ feedback: { rating: "happy", reason: null, note: null } })))
    .mockResolvedValueOnce(new Response(JSON.stringify({ recordings: [second], hasMore: false })));
  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  await waitFor(() => expect(screen.getByRole("button", { name: "Next" })).toBeEnabled());
  expect(screen.getByRole("heading", { name: "第一首" })).toBeInTheDocument();
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
  fetcher.mockImplementation(async (url: string) =>
    url.includes("recording=") ? new Response(null, { status: 503 })
      : url.endsWith("/sets") ? new Response(JSON.stringify({ sets: setRows }))
        : new Response(JSON.stringify({ recordings: [first], hasMore: false })));
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.queryByText("Timed Lyrics missing")).not.toBeInTheDocument();
});

it("keeps Full Screen disabled until the recording details arrive", async () => {
  const pending = Promise.withResolvers<Response>();
  const defaultFetch = fetcher.getMockImplementation()!;
  fetcher.mockImplementation((url: string, init?: RequestInit) => url.includes("recording=") ? pending.promise : defaultFetch(url, init));
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  expect(screen.getByRole("button", { name: "Full screen" })).toBeDisabled();
  pending.resolve(new Response(JSON.stringify({
    recording: first, synced: true, audioUrl: "https://audio.example/song.mp3",
    lines: [{ timeSeconds: 1, text: "第一行" }], feedback: null,
  })));
  await screen.findByText("第一行");
  expect(screen.getByRole("button", { name: "Full screen" })).toBeEnabled();
});

it("keeps the reviewer out of Full Screen when the recording fails to load", async () => {
  fetcher.mockImplementation(async (url: string) => url.includes("recording=")
    ? new Response(null, { status: 503 })
    : url.endsWith("/sets") ? new Response(JSON.stringify({ sets: setRows }))
      : new Response(JSON.stringify({ recordings: [first], hasMore: false })));
  render(<LyricsReviewClient />);
  fireEvent.click(await screen.findByText("第一首"));
  await waitFor(() => expect(screen.getByRole("alert")).toBeInTheDocument());
  expect(screen.getByRole("button", { name: "Full screen" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "Back to queue" })).toBeEnabled();
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

it("subsets the queue to the picked review set and clears back to the full queue", async () => {
  setRows = [
    { id: "set-1", name: "march-batch", total: 2, done: 1 },
    { id: "set-2", name: "finished", total: 3, done: 3 },
  ];
  // The server subsets the pool: a set-filtered request returns only its Songs.
  const defaultFetch = fetcher.getMockImplementation()!;
  fetcher.mockImplementation(async (url: string, init?: RequestInit) => url.includes("set=set-1")
    ? new Response(JSON.stringify({ recordings: [second], hasMore: false }))
    : defaultFetch(url, init));
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  const picker = screen.getByRole("combobox", { name: "Review set" });
  await waitFor(() => expect(within(picker).getByRole("option", { name: "march-batch · 1/2" })).toBeInTheDocument());
  // A completed set stays selectable and marked; nothing prunes it.
  expect(within(picker).getByRole("option", { name: "finished · 3/3 ✓" })).toBeInTheDocument();
  expect(screen.queryByRole("status")).not.toBeInTheDocument();

  fireEvent.change(picker, { target: { value: "set-1" } });
  await waitFor(() => expect(screen.queryByText("第一首")).not.toBeInTheDocument());
  expect(screen.getByText("第二首")).toBeInTheDocument();
  expect(screen.getByRole("status")).toHaveTextContent("march-batch — 1/2 reviewed");

  fireEvent.click(screen.getByRole("button", { name: "Clear set filter" }));
  expect(picker).toHaveValue("");
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  await screen.findByText("第一首");
});

it("drops the filter when the selected set is deleted from the CLI", async () => {
  setRows = [{ id: "set-1", name: "march-batch", total: 2, done: 0 }];
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  const picker = screen.getByRole("combobox", { name: "Review set" });
  await waitFor(() => expect(within(picker).getByRole("option", { name: "march-batch · 0/2" })).toBeInTheDocument());
  fireEvent.change(picker, { target: { value: "set-1" } });
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("march-batch — 0/2 reviewed"));

  // The CLI deleted the set; the next refresh must not leave the queue pointing at it.
  setRows = [];
  fireEvent.change(screen.getByRole("textbox", { name: "Search title or album" }), { target: { value: "第一" } });
  await waitFor(() => expect(picker).toHaveValue(""));
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
  await waitFor(() => {
    const last = queueUrls().at(-1) ?? "";
    expect(new URLSearchParams(last.split("?")[1] ?? "").get("set")).toBeNull();
  });
});

it("refreshes the set progress banner after feedback is saved", async () => {
  setRows = [{ id: "set-1", name: "march-batch", total: 2, done: 0 }];
  render(<LyricsReviewClient />);
  await screen.findByText("第一首");
  fireEvent.change(screen.getByRole("combobox", { name: "Review set" }), { target: { value: "set-1" } });
  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("march-batch — 0/2 reviewed"));

  await waitFor(() => expect(fetcher).toHaveBeenCalledWith(expect.stringContaining("set=set-1"), expect.any(Object)));
  fireEvent.click(screen.getByText("第一首"));
  await screen.findByText("第一行");
  setRows = [{ id: "set-1", name: "march-batch", total: 2, done: 1 }];
  fireEvent.click(screen.getByRole("button", { name: "Good" }));
  await screen.findByText("Saved");
  fireEvent.click(screen.getByRole("button", { name: "Back to queue" }));

  await waitFor(() => expect(screen.getByRole("status")).toHaveTextContent("march-batch — 1/2 reviewed"));
});
