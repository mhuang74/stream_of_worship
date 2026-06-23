import { SongsetListSkeleton } from "@/components/songset/SongsetListSkeleton";

export default function SongsetsLoading() {
  return (
    <div className="px-4 py-6 pb-24 lg:pb-6">
      <div className="mb-6">
        <h1 className="text-2xl font-bold">Songsets</h1>
      </div>
      <SongsetListSkeleton />
    </div>
  );
}
