import Link from "next/link";

export function Header() {
  return (
    <header className="sticky top-0 z-50 w-full border-b border-border bg-background/95 backdrop-blur">
      <div className="flex h-14 items-center gap-4 px-4">
        <Link href="/" className="flex items-center gap-2 font-semibold">
          <span className="text-primary">Stream of Worship</span>
        </Link>
        <nav className="hidden lg:flex items-center gap-6 ml-6" aria-label="Main navigation">
          <Link
            href="/songsets"
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            Songsets
          </Link>
          <Link
            href="/settings"
            className="text-sm font-medium text-muted-foreground hover:text-foreground transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring rounded"
          >
            Settings
          </Link>
        </nav>
        <div className="ml-auto" />
      </div>
    </header>
  );
}
