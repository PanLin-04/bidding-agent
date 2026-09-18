import type { Config } from "tailwindcss";

const config: Config = {
  // class 策略（而非 media）：用户可以在界面上强制切换，且需在 <head> 内联脚本
  // 提前应用，避免首屏闪白（见 app/layout.tsx）
  darkMode: "class",
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
      },
      animation: { "fade-in": "fade-in 0.2s ease-out" },
    },
  },
  plugins: [],
};

export default config;
