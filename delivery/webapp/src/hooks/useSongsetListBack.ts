"use client";

import { useCallback } from "react";
import { useRouter } from "next/navigation";
import { songsetsListUrl } from "@/lib/songset-list-state";

/**
 * Stable callback that navigates to the songsets list at the saved
 * page/search (from sessionStorage), falling back to bare /songsets.
 * Centralizes the 5 back-to-list exit points so they stay in lockstep.
 */
export function useSongsetListBack(): () => void {
  const router = useRouter();
  return useCallback(() => {
    router.push(songsetsListUrl());
  }, [router]);
}
