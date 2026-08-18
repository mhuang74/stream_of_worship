"use client";

/**
 * Client-side Completion tracking (CONTEXT.md: Completion): the set of song ids
 * the current user has heard to ≥90% of a full-song play. Soft gate per
 * ADR-0002 — lives entirely in localStorage, never sent to the server.
 * A tiny subscription lets UI flip live when a song crosses the threshold.
 */

const STORAGE_KEY = "sow_completed_song_ids";

type Listener = () => void;
const listeners = new Set<Listener>();

function readCompleted(): Set<string> {
  if (typeof window === "undefined") return new Set();
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    if (!raw) return new Set();
    const parsed: unknown = JSON.parse(raw);
    return new Set(Array.isArray(parsed) ? parsed.filter((x): x is string => typeof x === "string") : []);
  } catch {
    return new Set();
  }
}

export function isSongCompleted(songId: string): boolean {
  return readCompleted().has(songId);
}

export function getCompletedSongIds(): Set<string> {
  return readCompleted();
}

/** Marks a song completed; idempotent. Notifies subscribers on first mark. */
export function markSongCompleted(songId: string): void {
  if (typeof window === "undefined") return;
  const set = readCompleted();
  if (set.has(songId)) return;
  set.add(songId);
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify([...set]));
  } catch {
    // Storage unavailable (private mode / quota) — degrade silently.
  }
  listeners.forEach((listener) => listener());
}

/** Returns an unsubscribe function. */
export function subscribeCompletion(listener: Listener): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function resetCompletionForTests(): void {
  listeners.clear();
  if (typeof window !== "undefined") {
    try {
      window.localStorage.removeItem(STORAGE_KEY);
    } catch {
      // ignore
    }
  }
}
