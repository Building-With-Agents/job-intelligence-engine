import type { Metadata } from "next";

import "./globals.css";

export const metadata: Metadata = {
  title: "Labor Pulse",
  description: "Workforce intelligence Q&A",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" suppressHydrationWarning>
      <body className="min-h-dvh antialiased">{children}</body>
    </html>
  );
}
