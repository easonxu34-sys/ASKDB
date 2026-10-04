import type { Metadata } from "next";
import { TooltipProvider } from "@/components/ui/tooltip";
import { AppShell } from "./app-shell";
import "./globals.css";

export const metadata: Metadata = {
  title: "AskDB 智能助手",
  description: "使用自然语言查询和探索数据。",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="zh-CN">
      <body className="font-sans antialiased">
        <TooltipProvider>
          <AppShell>{children}</AppShell>
        </TooltipProvider>
      </body>
    </html>
  );
}
