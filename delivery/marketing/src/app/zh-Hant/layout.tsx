import type { ReactNode } from "react";

// Static export renders a single root <html>; the zh-Hant tree marks its
// language at the wrapper-div level, which is sufficient for a static site.
export default function ZhHantLayout({ children }: { children: ReactNode }) {
  return <div lang="zh-Hant">{children}</div>;
}
