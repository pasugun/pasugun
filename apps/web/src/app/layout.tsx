import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import { Disclaimer } from "@/components/disclaimer";
import "./globals.css";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "시그널 로봇",
  description: "규칙 기반 로봇의 매수·매도 신호와 성과를 보는 개인용 대시보드",
};

export default function RootLayout({ children }: LayoutProps<"/">) {
  return (
    <html
      lang="ko"
      className={`${geistSans.variable} ${geistMono.variable} h-full antialiased`}
    >
      <body className="flex min-h-full flex-col bg-zinc-50 text-zinc-900 dark:bg-zinc-950 dark:text-zinc-100">
        <div className="flex-1">{children}</div>
        <Disclaimer />
      </body>
    </html>
  );
}
