"use client";

import { Skeleton } from "@/components/ui/skeleton";

function SongsetRowSkeleton() {
  return (
    <div className="flex items-center gap-4 rounded-lg border p-4">
      <div className="flex-1 space-y-2">
        <Skeleton className="h-5 w-48" />
        <Skeleton className="h-4 w-32" />
      </div>
      <Skeleton className="h-9 w-20 rounded-md" />
    </div>
  );
}

export function SongsetListSkeleton() {
  return (
    <div className="space-y-3" aria-label="Loading songsets" role="status">
      <span className="sr-only">Loading songsets…</span>
      {Array.from({ length: 4 }).map((_, i) => (
        <SongsetRowSkeleton key={i} />
      ))}
    </div>
  );
}
