import type { Metadata, Viewport } from "next";
import "./globals.css";
import { Providers } from "./providers";

export const metadata: Metadata = {
  title: "Moo Setup",
  description: "Set up and control your Moo storyteller over wifi or Bluetooth.",
  manifest: `${process.env.NEXT_PUBLIC_BASE_PATH || ""}/manifest.json`,
  appleWebApp: { capable: true, title: "Moo", statusBarStyle: "default" },
};

export const viewport: Viewport = {
  themeColor: "#F5C97B",
  width: "device-width",
  initialScale: 1,
  maximumScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
