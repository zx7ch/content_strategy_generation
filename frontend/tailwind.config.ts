import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        ink: "#1a1a1a",
        paper: "#ffffff",
        mist: "#f5f5f5",
        line: "#e7ebef",
        quiet: "#6b7280",
        success: "#006640",
        successBg: "#e6f7ee",
        warning: "#d48806",
        warningBg: "#fff7e6",
        danger: "#cf1322",
        dangerBg: "#fff1f0"
      },
      boxShadow: {
        panel: "0 18px 45px rgba(17, 24, 39, 0.05)"
      },
      borderRadius: {
        panel: "20px"
      }
    }
  },
  plugins: []
};

export default config;
