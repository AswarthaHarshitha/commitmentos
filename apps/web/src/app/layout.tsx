import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";
import { Providers } from "./providers";
import "./globals.css";

export const metadata: Metadata = {
  title: { default: "CommitmentOS", template: "%s · CommitmentOS" },
  description: "Keeps track of what you promised, owe and need to remember - detected from your email, followed up until it is done.",
};

export const viewport: Viewport = { themeColor: "#f6f3ec", width: "device-width", initialScale: 1 };

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[80] focus:rounded-md focus:bg-ink focus:px-3 focus:py-2 focus:text-[14px] focus:text-surface"
        >
          Skip to content
        </a>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
