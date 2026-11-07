"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Pause, Play } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useLocale } from "@/hooks/useLocale";
import type { ReviewCandidate } from "@/lib/lyrics/review";
import type { LRCLine } from "@/lib/render/lrc-parser";
import type { FeedbackReason } from "@/lib/lyrics/situation";

interface Feedback { rating: "happy" | "sad"; reason: FeedbackReason | null; note: string | null }
interface Detail {
  recording: Pick<ReviewCandidate, "contentHash" | "title" | "album">;
  lines: LRCLine[];
  synced: boolean;
  audioUrl: string;
  feedback: Feedback | null;
}
const endpoint = "/api/admin/lyrics-review";
/** Auto-scroll resumes this long after the last manual scroll. */
const FOLLOW_RESUME_MS = 5000;
/** Exit full screen reverts to a single tap after this long. */
const EXIT_ARM_MS = 3000;
const timecode = (seconds: number) => {
  const centiseconds = Math.floor(Math.max(0, seconds) * 100);
  return `${String(Math.floor(centiseconds / 6000)).padStart(2, "0")}:${String(Math.floor(centiseconds / 100) % 60).padStart(2, "0")}.${String(centiseconds % 100).padStart(2, "0")}`;
};
async function api<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, { cache: "no-store", ...init });
  if (!response.ok) throw new Error("Review request failed");
  return response.json();
}

export function LyricsReviewClient() {
  const { t } = useLocale();
  const [query, setQuery] = useState("");
  const [showReviewed, setShowReviewed] = useState(false);
  const [rows, setRows] = useState<ReviewCandidate[]>([]);
  const [hasMore, setHasMore] = useState(false);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState(false);
  const [selected, setSelected] = useState<string | null>(null);
  const [detail, setDetail] = useState<Detail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);
  const [audioError, setAudioError] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<"saved" | "removed" | null>(null);
  const [poorOpen, setPoorOpen] = useState(false);
  const [reason, setReason] = useState<FeedbackReason>("timing");
  const [note, setNote] = useState("");
  const [position, setPosition] = useState(0);
  const [duration, setDuration] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [following, setFollowing] = useState(true);
  const [fullscreen, setFullscreen] = useState(false);
  const [exitArmed, setExitArmed] = useState(false);
  const audio = useRef<HTMLAudioElement>(null);
  const lyrics = useRef<HTMLDivElement>(null);
  const detailRequest = useRef(0);
  const listRequest = useRef(0);
  const resumeTimer = useRef<number | null>(null);
  const exitTimer = useRef<number | null>(null);

  const clearResumeTimer = useCallback(() => {
    clearTimeout(resumeTimer.current ?? undefined);
    resumeTimer.current = null;
  }, []);

  const clearExitTimer = useCallback(() => {
    clearTimeout(exitTimer.current ?? undefined);
    exitTimer.current = null;
  }, []);

  /** A manual scroll suspends auto-scroll, which resumes on its own after a pause. */
  const pauseFollow = useCallback(() => {
    setFollowing(false);
    clearResumeTimer();
    resumeTimer.current = window.setTimeout(() => {
      resumeTimer.current = null;
      setFollowing(true);
    }, FOLLOW_RESUME_MS);
  }, [clearResumeTimer]);

  // Timers must not outlive the screen.
  useEffect(() => () => {
    clearTimeout(resumeTimer.current ?? undefined);
    clearTimeout(exitTimer.current ?? undefined);
  }, []);

  const loadList = useCallback(async (offset = 0) => {
    const requestId = ++listRequest.current;
    setListLoading(true);
    setListError(false);
    const params = new URLSearchParams({ q: query.trim(), reviewed: showReviewed ? "1" : "0", offset: String(offset) });
    try {
      const data = await api<{ recordings: ReviewCandidate[]; hasMore: boolean }>(`${endpoint}?${params}`);
      if (requestId !== listRequest.current) return;
      setRows(old => offset ? [...old, ...data.recordings.filter(row => !old.some(item => item.contentHash === row.contentHash))] : data.recordings);
      setHasMore(data.hasMore);
      return data;
    } catch { if (requestId === listRequest.current) setListError(true); }
    finally { if (requestId === listRequest.current) setListLoading(false); }
  }, [query, showReviewed]);

  useEffect(() => {
    const timer = setTimeout(() => { void loadList(); }, 250);
    return () => {
      clearTimeout(timer);
      // Invalidate pending requests when the search changes or the screen leaves.
      // eslint-disable-next-line react-hooks/exhaustive-deps
      ++listRequest.current;
    };
  }, [loadList]);

  const open = async (hash: string) => {
    const requestId = ++detailRequest.current;
    audio.current?.pause();
    setSelected(hash); setDetail(null); setLoading(true); setError(false);
    setAudioError(false); setMessage(null); setPoorOpen(false);
    setPosition(0); setDuration(0); setPlaying(false); setFollowing(true);
    clearResumeTimer(); clearExitTimer();
    setFullscreen(false); setExitArmed(false);
    try {
      const data = await api<Detail>(`${endpoint}?recording=${encodeURIComponent(hash)}`);
      if (requestId !== detailRequest.current) return;
      setDetail(data);
      const previousReason = data.feedback?.reason;
      const validPreviousReason = previousReason &&
        !(previousReason === "missing" && data.synced) &&
        !(previousReason === "timing" && !data.synced);
      setReason(validPreviousReason ? previousReason : (data.synced ? "timing" : "missing"));
      setNote(data.feedback?.note ?? "");
    } catch { if (requestId === detailRequest.current) setError(true); }
    finally { if (requestId === detailRequest.current) setLoading(false); }
  };

  const save = async (rating: "happy" | "sad" | null) => {
    if (!selected) return;
    setSaving(true); setError(false); setMessage(null);
    try {
      const data = await api<{ feedback: Feedback | null }>(`${endpoint}?recording=${encodeURIComponent(selected)}`, {
        method: rating === null ? "DELETE" : "PUT",
        headers: { "Content-Type": "application/json" },
        ...(rating ? { body: JSON.stringify({ rating, reason: rating === "sad" ? reason : null, note: rating === "sad" ? note : null }) } : {}),
      });
      setDetail(old => old ? { ...old, feedback: data.feedback } : old);
      setMessage(rating ? "saved" : "removed"); setPoorOpen(false);
      void loadList();
    } catch { setError(true); }
    finally { setSaving(false); }
  };

  let active = -1;
  detail?.lines.forEach((line, index) => { if (line.timeSeconds <= position) active = index; });
  useEffect(() => {
    const container = lyrics.current;
    const line = container?.querySelector<HTMLElement>('[aria-current="true"]');
    if (following && container && line) {
      // Rect-based offset: `offsetTop` arithmetic breaks as soon as the line and
      // the container resolve different `offsetParent`s (the container itself is
      // `relative` here), which used to scroll hundreds of pixels too far up.
      const top = line.getBoundingClientRect().top - container.getBoundingClientRect().top + container.scrollTop;
      container.scrollTo({ top: top - container.clientHeight / 3, behavior: "smooth" });
    }
    // `fullscreen` is a dependency because entering or leaving it swaps the
    // scroll container: the new one must be positioned on the active line.
  }, [active, following, fullscreen]);

  const seek = (time: number) => {
    if (!audio.current || !Number.isFinite(audio.current.duration)) return;
    audio.current.currentTime = Math.max(0, Math.min(time, audio.current.duration));
    setPosition(audio.current.currentTime);
    // Seeking and tapping a line both mean "show me this part", so follow resumes immediately.
    clearResumeTimer();
    setFollowing(true);
  };
  /** Full Screen hides native chrome, so its bar drives playback itself. */
  const togglePlay = () => {
    const el = audio.current;
    if (!el) return;
    if (el.paused) void el.play();
    else el.pause();
  };
  const close = () => {
    ++detailRequest.current; audio.current?.pause(); setSelected(null); setDetail(null);
  };
  const enterFullscreen = () => {
    // Full Screen's only exit control lives inside the `detail` subtree, so
    // entering before details exist would strand the reviewer with no toolbar.
    if (!detail) return;
    clearResumeTimer();
    setFollowing(true);
    clearExitTimer();
    setExitArmed(false);
    setFullscreen(true);
  };
  /** Two taps to leave: the first arms the button, the second (within the window) exits. */
  const toggleExit = () => {
    clearExitTimer();
    if (exitArmed) {
      setExitArmed(false);
      setFollowing(true);
      setFullscreen(false);
      return;
    }
    setExitArmed(true);
    exitTimer.current = window.setTimeout(() => { exitTimer.current = null; setExitArmed(false); }, EXIT_ARM_MS);
  };
  const index = rows.findIndex(row => row.contentHash === selected);
  const next = index >= 0 ? rows[index + 1] : rows[0];
  const reasons: FeedbackReason[] = detail?.synced ? ["timing", "wrong_text", "other"] : ["missing", "wrong_text", "other"];

  return (
    <div className="mx-auto w-full max-w-2xl p-4 space-y-4">
      <div><h1 className="text-2xl font-semibold">{t("review.title")}</h1>
        <p className="text-sm text-muted-foreground">{t("review.canonical")}</p></div>
      {!selected ? <>
        <Input aria-label={t("review.search")} placeholder={t("review.search")} value={query} onChange={event => setQuery(event.target.value)} />
        <label className="flex min-h-11 items-center gap-2"><input type="checkbox" checked={showReviewed} onChange={event => setShowReviewed(event.target.checked)} />{t("review.reviewed")}</label>
        {listError && <div role="alert">{t("review.error")} <Button onClick={() => void loadList()}>{t("review.retry")}</Button></div>}
        {!listLoading && !listError && rows.length === 0 && <p>{t("review.empty")}</p>}
        <div className="divide-y rounded-lg border">{rows.map(row => <button key={row.contentHash} className="block min-h-16 w-full p-3 text-left hover:bg-muted" onClick={() => void open(row.contentHash)}>
          <span className="block font-medium">{row.title}</span><span className="block text-sm text-muted-foreground">{row.album}</span>
          {row.reviewed && <span className="text-xs">{t("review.reviewed")}</span>}
          {showReviewed && row.feedback.length > 0 && <span className="mt-2 block space-y-2 text-sm" role="list" aria-label={t("review.feedback")}>
            {row.feedback.map(feedback => <span key={feedback.id} role="listitem" className="block whitespace-pre-wrap break-words [overflow-wrap:anywhere]">
              <span className="font-medium">{t(feedback.rating === "happy" ? "review.good" : "review.poor")}</span>
              {feedback.rating === "sad" && <>
                {feedback.reason && <span> · {t(`review.${feedback.reason}Reason`)}</span>}
                {feedback.note && <span className="block text-muted-foreground">{feedback.note}</span>}
              </>}
            </span>)}
          </span>}
        </button>)}</div>
        {listLoading && <p role="status">{t("review.loading")}</p>}
        {hasMore && <Button disabled={listLoading} onClick={() => void loadList(rows.length)}>{t("review.more")}</Button>}
      </> : <>
        {!fullscreen && <div className="flex justify-between gap-2"><Button variant="outline" disabled={saving} onClick={close}>{t("review.back")}</Button>
          <div className="flex gap-2">
            <Button size="sm" variant="outline" disabled={!detail} onClick={enterFullscreen}>{t("review.fullscreen")}</Button>
            <Button disabled={saving || listLoading || (!next && !hasMore)} onClick={() => {
              if (next) void open(next.contentHash);
              else void loadList(rows.length).then(data => {
                if (data?.recordings[0]) void open(data.recordings[0].contentHash);
              });
            }}>{t("review.next")}</Button>
          </div></div>}
        {loading && <p role="status">{t("review.loading")}</p>}
        {error && <div role="alert">{t("review.error")} <Button disabled={saving} onClick={() => void open(selected)}>{t("review.retry")}</Button></div>}
        {/* One detail tree for both layouts: the audio element and the lyrics
            container keep their DOM nodes when Full Screen toggles, so playback
            and the follow position survive entering and leaving it. */}
        {detail && <div className={fullscreen
          ? "fixed inset-0 z-[60] flex flex-col bg-background pt-[env(safe-area-inset-top)] pb-[env(safe-area-inset-bottom)]"
          : "space-y-4"}>
          <div className={fullscreen ? "flex items-center gap-2 border-b px-2 py-1" : ""}>
            <h2 className={fullscreen ? "min-w-0 flex-1 truncate text-sm font-medium" : "text-xl font-medium"}>{detail.recording.title}</h2>
            {fullscreen && <>
              <Button size="icon" variant="ghost" aria-label={t(playing ? "review.pause" : "review.play")} onClick={togglePlay}>
                {playing ? <Pause className="size-5" /> : <Play className="size-5" />}
              </Button>
              <span className="shrink-0 font-mono text-xs tabular-nums">{timecode(position)} / {timecode(duration)}</span>
              <Button size="sm" variant="outline" onClick={toggleExit}>
                {exitArmed ? t("review.confirmExitFullscreen") : t("review.exitFullscreen")}</Button>
            </>}
          </div>
          <div className={fullscreen ? "border-b px-2 pt-1 pb-1" : "sticky top-14 z-10 bg-background py-2"}>
            <audio key={selected} ref={audio} controls={fullscreen ? undefined : true} preload="metadata" className="w-full" src={detail.audioUrl}
              onTimeUpdate={event => setPosition(event.currentTarget.currentTime)}
              onLoadedMetadata={event => setDuration(event.currentTarget.duration)}
              onPlay={() => setPlaying(true)}
              onPause={() => setPlaying(false)}
              onError={() => setAudioError(true)} />
            {/* Kept mounted in both layouts so the node survives the Full Screen toggle. */}
            <label className="sr-only" htmlFor="review-position">{t("review.seek")}</label>
            <input id="review-position" type="range" className={fullscreen ? "mt-1 h-4 w-full accent-primary" : ""} hidden={!fullscreen} min={0} max={duration || 0} step={0.01}
              value={Math.min(position, duration || 0)} disabled={!duration} onChange={event => seek(Number(event.target.value))} />
          </div>
          {audioError && <div role="alert">{t("review.audioError")} <Button onClick={() => void open(selected)}>{t("review.retry")}</Button></div>}
          {!detail.synced && <p role="status">{t("review.missing")}</p>}
          <div ref={lyrics}
            className={fullscreen
              ? "flex-1 min-h-0 overflow-y-auto overscroll-contain"
              : "relative h-[38dvh] min-h-40 overflow-y-auto overscroll-contain rounded-lg border"}
            onWheel={pauseFollow} onTouchMove={pauseFollow}
            onKeyDown={event => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(event.key)) pauseFollow(); }}>
            {detail.lines.map((line, i) => <button key={i} aria-current={i === active ? "true" : undefined}
              className={`flex min-h-12 w-full items-start gap-3 px-3 py-3 text-left ${i === active ? "bg-primary/15 font-semibold" : "hover:bg-muted"}`}
              onClick={() => seek(line.timeSeconds)}>
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{timecode(line.timeSeconds)}</span>
              <span>{line.text || t("review.gap")}</span>
            </button>)}
          </div>
          {!fullscreen && <section className="space-y-3 rounded-lg border p-3" aria-label={t("review.feedback")}>
            {detail.feedback && <p>{t(detail.feedback.rating === "happy" ? "review.ratedGood" : "review.ratedPoor")}</p>}
            {message && <p role="status">{t(message === "saved" ? "review.saved" : "review.removed")}</p>}
            <div className="flex flex-wrap gap-2"><Button variant={detail.feedback?.rating === "happy" ? "default" : "outline"}
              aria-pressed={detail.feedback?.rating === "happy"} disabled={saving || !detail.synced} onClick={() => void save("happy")}>{t("review.good")}</Button>
              <Button variant={detail.feedback?.rating === "sad" ? "default" : "outline"}
                aria-pressed={detail.feedback?.rating === "sad"} disabled={saving} onClick={() => setPoorOpen(value => !value)}>{t("review.poor")}</Button>
              {detail.feedback && <Button variant="ghost" disabled={saving} onClick={() => void save(null)}>{t("review.retract")}</Button>}</div>
            {poorOpen && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void save("sad"); }}>
              <label className="block space-y-1"><span>{t("review.reason")}</span><select className="block min-h-11 w-full rounded border bg-background px-2" value={reason} onChange={event => setReason(event.target.value as FeedbackReason)}>
                {reasons.map(value => <option key={value} value={value}>{t(`review.${value}Reason`)}</option>)}
              </select></label>
              <label className="block space-y-1"><span>{t("review.note")}</span><textarea className="block min-h-24 w-full rounded border p-2" maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>
              <Button disabled={saving} type="submit">{t("review.save")}</Button>
            </form>}
          </section>}
        </div>}
      </>}
    </div>
  );
}
