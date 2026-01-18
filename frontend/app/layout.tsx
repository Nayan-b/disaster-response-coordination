import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flood Response Coordinator",
  description:
    "Live flood disaster data, agent-drafted resource recommendations, and human approval.",
};

/**
 * Applies the stored theme before first paint. Inline and blocking on purpose — deferring it to a
 * React effect means a full frame of the wrong theme on every load, which on a dark-mode console is
 * a white flash in a dark room.
 */
const THEME_SCRIPT = `
try {
  var stored = localStorage.getItem('flood-theme');
  var dark = stored ? stored === 'dark'
    : window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (dark) document.documentElement.classList.add('dark');
} catch (e) {}
`;

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="antialiased">{children}</body>
    </html>
  );
}
