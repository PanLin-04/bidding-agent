import type { Metadata, Viewport } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "招投标采购智能问答",
  description: "基于混合检索（向量 + BM25）的招投标采购知识问答助手",
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
};

// 首屏防闪烁：在 SSR 输出的 HTML 里先把 dark 类挂上去，避免"先亮后暗"闪一下。
// 必须内联在 <head>，等到 React 水合再切就晚了。
const THEME_SCRIPT = `
try {
  var saved = localStorage.getItem('chat_dark_mode');
  var prefersDark = window.matchMedia('(prefers-color-scheme: dark)').matches;
  if (saved === '1' || (saved === null && prefersDark)) {
    document.documentElement.classList.add('dark');
  }
} catch (e) {}
`;

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="zh-CN" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_SCRIPT }} />
      </head>
      <body className="bg-slate-50 text-slate-900 antialiased dark:bg-slate-950 dark:text-slate-100">
        {children}
      </body>
    </html>
  );
}
