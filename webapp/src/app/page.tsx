import Link from "next/link";
import { buttonVariants } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export default function HomePage() {
  return (
    <div className="flex flex-col items-center justify-center min-h-[60vh] gap-6 px-4">
      <h1 className="text-3xl font-bold text-center">Stream of Worship</h1>
      <p className="text-muted-foreground text-center max-w-md">
        Worship music transition and playback system. Manage songsets, render
        audio and video, and lead worship seamlessly.
      </p>
      <Link href="/songsets" className={cn(buttonVariants())}>
        View Songsets
      </Link>
    </div>
  );
}
