import type { Metadata } from "next";
import "./globals.css";

/**
 * Deliberately NOT using next/font/google here. A hackathon demo
 * shouldn't have a build/runtime dependency on fonts.googleapis.com
 * being reachable — that's exactly the kind of avoidable "demo-day
 * internet failure" risk flagged in the project's own stop
 * conditions. System font stacks (defined in globals.css) deliver
 * the intended display/body/tabular-mono pairing without any
 * external fetch, at build time or runtime.
 */

export const metadata: Metadata = {
  title: "PayShield — payment degradation intelligence",
  description:
    "When a bank has a bad twenty minutes, PayShield is the one system that notices it's one problem, not fifty.",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html lang="en" className="h-full antialiased">
      <body className="min-h-full flex flex-col bg-[var(--ink-950)]">{children}</body>
    </html>
  );
}
