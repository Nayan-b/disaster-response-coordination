import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Flood Response Coordinator",
  description:
    "Live flood disaster data, agent-drafted resource recommendations, and human approval.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body className="antialiased">{children}</body>
    </html>
  );
}
