"use client";

import { useState, useEffect, useCallback, useRef } from "react";

export type FeedbackRating = "happy" | "sad";
export type FeedbackReason = "missing" | "timing" | "wrong_text" | "other";

export interface LyricsFeedbackState {
  rating: FeedbackRating;
  reason: FeedbackReason | null;
}

export interface LyricsFeedbackResult {
  /** The caller's current feedback for this Recording, or null. */
  feedback: LyricsFeedbackState | null;
  loading: boolean;
  /** Upsert feedback; on success reflects the server's response. */
  submit: (rating: FeedbackRating, reason?: FeedbackReason) => Promise<boolean>;
  /** Delete the caller's feedback row. */
  retract: () => Promise<boolean>;
}

/**
 * Per-Recording Lyrics Feedback for the signed-in user (issue #194).
 * Loads the caller's current feedback on mount and applies optimistic
 * updates with rollback on failure. Retraction by re-tapping the active
 * icon is handled by the caller: submit() with the same rating it already
 * shows, or retract() explicitly.
 */
export function useLyricsFeedback(recordingContentHash: string | undefined): LyricsFeedbackResult {
  const [feedback, setFeedback] = useState<LyricsFeedbackState | null>(null);
  const [loading, setLoading] = useState<boolean>(Boolean(recordingContentHash));
  const abortControllerRef = useRef<AbortController | null>(null);
  // Latest-state ref so optimistic rollback reads fresh feedback without
  // making submit/retract identities churn on every state change.
  const feedbackRef = useRef<LyricsFeedbackState | null>(null);
  feedbackRef.current = feedback;

  useEffect(() => {
    if (!recordingContentHash) {
      setFeedback(null);
      setLoading(false);
      return;
    }

    const abortController = new AbortController();
    abortControllerRef.current = abortController;

    setLoading(true);

    fetch(`/api/lyrics/feedback/${recordingContentHash}`, { signal: abortController.signal })
      .then(async (res) => {
        if (res.status === 401) {
          // Not signed in: no feedback affordance state to show.
          setFeedback(null);
          return;
        }
        if (!res.ok) {
          throw new Error(`GET feedback failed: ${res.status}`);
        }
        const data = (await res.json()) as { feedback: LyricsFeedbackState | null };
        setFeedback(data.feedback);
      })
      .catch((err: unknown) => {
        if ((err as { name?: string })?.name === "AbortError") return;
        console.error("Error loading lyrics feedback:", err);
      })
      .finally(() => {
        setLoading(false);
      });

    return () => {
      abortController.abort();
    };
  }, [recordingContentHash]);

  const submit = useCallback(
    async (rating: FeedbackRating, reason?: FeedbackReason): Promise<boolean> => {
      if (!recordingContentHash) return false;
      const previous = feedbackRef.current;
      const optimistic: LyricsFeedbackState = {
        rating,
        reason: rating === "sad" ? (reason ?? null) : null,
      };
      setFeedback(optimistic);
      try {
        const res = await fetch(`/api/lyrics/feedback/${recordingContentHash}`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ rating, reason: optimistic.reason ?? undefined }),
        });
        if (!res.ok) throw new Error(`PUT feedback failed: ${res.status}`);
        const data = (await res.json()) as { feedback: LyricsFeedbackState | null };
        setFeedback(data.feedback);
        return true;
      } catch (err) {
        console.error("Error saving lyrics feedback:", err);
        setFeedback(previous);
        return false;
      }
    },
    [recordingContentHash]
  );

  const retract = useCallback(async (): Promise<boolean> => {
    if (!recordingContentHash) return false;
    const previous = feedbackRef.current;
    setFeedback(null);
    try {
      const res = await fetch(`/api/lyrics/feedback/${recordingContentHash}`, {
        method: "DELETE",
      });
      if (!res.ok) throw new Error(`DELETE feedback failed: ${res.status}`);
      return true;
    } catch (err) {
      console.error("Error deleting lyrics feedback:", err);
      setFeedback(previous);
      return false;
    }
  }, [recordingContentHash]);

  return { feedback, loading, submit, retract };
}