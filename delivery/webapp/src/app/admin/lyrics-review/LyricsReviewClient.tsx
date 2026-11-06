"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { useLocale } from "@/hooks/useLocale";
import type { ReviewCandidate } from "@/lib/lyrics/review";
import type { LRCLine } from "@/lib/render/lrc-parser";
import type { FeedbackReason } from "@/lib/lyrics/situation";

interface Feedback { rating: "happy" | "sad"; reason: FeedbackReason | null; note: string | null }
interface Detail {
  recording: ReviewCandidate;
  lines: LRCLine[];
  synced: boolean;
  audioUrl: string;
  feedback: Feedback | null;
}
const endpoint = "/api/admin/lyrics-review";
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
  const [following, setFollowing] = useState(true);
  const audio = useRef<HTMLAudioElement>(null);
  const lyrics = useRef<HTMLDivElement>(null);
  const detailRequest = useRef(0);
  const listRequest = useRef(0);

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
    setPosition(0); setDuration(0); setFollowing(true);
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
      container.scrollTo({ top: line.offsetTop - container.offsetTop - container.clientHeight / 3, behavior: "smooth" });
    }
  }, [active, following]);

  const seek = (time: number) => {
    if (!audio.current || !Number.isFinite(audio.current.duration)) return;
    audio.current.currentTime = Math.max(0, Math.min(time, audio.current.duration));
    setPosition(audio.current.currentTime);
  };
  const close = () => {
    ++detailRequest.current; audio.current?.pause(); setSelected(null); setDetail(null);
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
        </button>)}</div>
        {listLoading && <p role="status">{t("review.loading")}</p>}
        {hasMore && <Button disabled={listLoading} onClick={() => void loadList(rows.length)}>{t("review.more")}</Button>}
      </> : <>
        <div className="flex justify-between gap-2"><Button variant="outline" disabled={saving} onClick={close}>{t("review.back")}</Button>
          <Button disabled={saving || listLoading || (!next && !hasMore)} onClick={() => {
            if (next) void open(next.contentHash);
            else void loadList(rows.length).then(data => {
              if (data?.recordings[0]) void open(data.recordings[0].contentHash);
            });
          }}>{t("review.next")}</Button></div>
        {loading && <p role="status">{t("review.loading")}</p>}
        {error && <div role="alert">{t("review.error")} <Button disabled={saving} onClick={() => void open(selected)}>{t("review.retry")}</Button></div>}
        {detail && <>
          <h2 className="text-xl font-medium">{detail.recording.title}</h2>
          <div className="sticky top-14 z-10 space-y-2 bg-background py-2">
            <audio key={selected} ref={audio} controls preload="metadata" className="w-full" src={detail.audioUrl}
              onTimeUpdate={event => setPosition(event.currentTarget.currentTime)}
              onLoadedMetadata={event => setDuration(event.currentTarget.duration)}
              onError={() => setAudioError(true)} />
            <label className="sr-only" htmlFor="review-position">{t("review.seek")}</label>
            <input id="review-position" type="range" className="h-8 w-full" min={0} max={duration || 0} step={0.01}
              value={Math.min(position, duration || 0)} disabled={!duration} onChange={event => seek(Number(event.target.value))} />
            <div className="flex items-center justify-between text-sm"><span className="font-mono tabular-nums">{timecode(position)} / {timecode(duration)}</span>
              {!following && <Button size="sm" variant="outline" onClick={() => setFollowing(true)}>{t("review.follow")}</Button>}</div>
          </div>
          {audioError && <div role="alert">{t("review.audioError")} <Button onClick={() => void open(selected)}>{t("review.retry")}</Button></div>}
          {!detail.synced && <p role="status">{t("review.missing")}</p>}
          <div ref={lyrics} className="relative h-[38dvh] min-h-40 overflow-y-auto overscroll-contain rounded-lg border"
            onWheel={() => setFollowing(false)} onTouchMove={() => setFollowing(false)}
            onKeyDown={event => { if (["ArrowUp", "ArrowDown", "PageUp", "PageDown", "Home", "End"].includes(event.key)) setFollowing(false); }}>
            {detail.lines.map((line, i) => <button key={i} aria-current={i === active ? "true" : undefined}
              className={`flex min-h-12 w-full items-start gap-3 px-3 py-3 text-left ${i === active ? "bg-primary/15 font-semibold" : "hover:bg-muted"}`}
              onClick={() => seek(line.timeSeconds)}>
              <span className="shrink-0 font-mono text-xs tabular-nums text-muted-foreground">{timecode(line.timeSeconds)}</span>
              <span>{line.text || t("review.gap")}</span>
            </button>)}
          </div>
          <section className="space-y-3 rounded-lg border p-3" aria-label={t("review.feedback")}>
            {detail.feedback && <p>{t(detail.feedback.rating === "happy" ? "review.ratedGood" : "review.ratedPoor")}</p>}
            {message && <p role="status">{t(message === "saved" ? "review.saved" : "review.removed")}</p>}
            <div className="flex flex-wrap gap-2"><Button disabled={saving || !detail.synced} onClick={() => void save("happy")}>{t("review.good")}</Button>
              <Button variant="outline" disabled={saving} onClick={() => setPoorOpen(value => !value)}>{t("review.poor")}</Button>
              {detail.feedback && <Button variant="ghost" disabled={saving} onClick={() => void save(null)}>{t("review.retract")}</Button>}</div>
            {poorOpen && <form className="space-y-3" onSubmit={event => { event.preventDefault(); void save("sad"); }}>
              <label className="block space-y-1"><span>{t("review.reason")}</span><select className="block min-h-11 w-full rounded border bg-background px-2" value={reason} onChange={event => setReason(event.target.value as FeedbackReason)}>
                {reasons.map(value => <option key={value} value={value}>{t(`review.${value}Reason`)}</option>)}
              </select></label>
              <label className="block space-y-1"><span>{t("review.note")}</span><textarea className="block min-h-24 w-full rounded border p-2" maxLength={2000} value={note} onChange={event => setNote(event.target.value)} /></label>
              <Button disabled={saving} type="submit">{t("review.save")}</Button>
            </form>}
          </section>
        </>}
      </>}
    </div>
  );
}
