import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Header } from "@/components/layout/Header";
import { BottomNav } from "@/components/layout/BottomNav";
import { AuthProvider } from "@/contexts/AuthContext";
import { GlobalAudioPlayer } from "@/components/audio/GlobalAudioPlayer";

const geistSans = Geist({
  variable: "--font-sans",
  subsets: ["latin"],
  display: "swap",
  preload: true,
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
  display: "swap",
  preload: false,
});

export const metadata: Metadata = {
  title: "Stream of Worship",
  description: "Worship music transition and playback system",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html
      lang="en"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="min-h-full flex flex-col bg-background text-foreground">
        <AuthProvider>
          <GlobalAudioPlayer>
            <Header />
            <main className="flex-1 pb-16 lg:pb-0">{children}</main>
            <BottomNav />
          </GlobalAudioPlayer>
        </AuthProvider>
      </body>
    </html>
  );
}
