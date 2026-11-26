import type { Metadata } from "next";
import { Geist, Geist_Mono } from "next/font/google";
import "./globals.css";
import { Toaster } from "@/components/ui/toaster";
import { ThemeProvider } from "next-themes";

const geistSans = Geist({
  variable: "--font-geist-sans",
  subsets: ["latin"],
});

const geistMono = Geist_Mono({
  variable: "--font-geist-mono",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "QSCV B2910BL — квазистатические C–V измерения",
  description:
    "Рабочее место квазистатических C–V измерений на Keysight B2910BL: паспорт программы, пошаговый регламент SMU, заряд/секущая ёмкость, хвост и пропуски, энергия, неопределённость, журнал CSV и SCPI-скрипты.",
  keywords: [
    "QSCV",
    "B2910BL",
    "quasi-static C-V",
    "SMU",
    "Keysight",
    "Кун",
    "секущая ёмкость",
  ],
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="ru" suppressHydrationWarning>
      <body
        className={`${geistSans.variable} ${geistMono.variable} antialiased bg-background text-foreground`}
      >
        <ThemeProvider attribute="class" defaultTheme="light" enableSystem={false}>
          {children}
          <Toaster />
        </ThemeProvider>
      </body>
    </html>
  );
}
