import type { Config } from "tailwindcss";

// Every colour maps to a CSS variable from tokens.css — Tailwind is the
// utility layer, tokens.css is the source (ADR-0015 §1, gate G-02).
export default {
  content: ["./index.html", "./src/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        navy: { DEFAULT: "var(--navy)", 2: "var(--navy-2)", soft: "var(--navy-soft)" },
        accent: "var(--accent)",
        ink: "var(--ink)",
        slate: "var(--slate)",
        muted: "var(--muted)",
        bg: "var(--bg)",
        card: "var(--card)",
        line: "var(--line)",
        "blue-bg": "var(--blue-bg)",
        ok: { DEFAULT: "var(--ok)", bg: "var(--ok-bg)", mark: "var(--ok-mark)" },
        warn: { DEFAULT: "var(--warn)", bg: "var(--warn-bg)", mark: "var(--warn-mark)" },
        crit: { DEFAULT: "var(--crit)", bg: "var(--crit-bg)", mark: "var(--crit-mark)" },
        none: { DEFAULT: "var(--none)", bg: "var(--none-bg)", mark: "var(--none-mark)" },
        ai: { DEFAULT: "var(--ai)", bg: "var(--ai-bg)" },
      },
      borderRadius: { cc: "var(--radius)", "cc-sm": "var(--radius-sm)" },
      boxShadow: { cc: "var(--shadow)", "cc-lg": "var(--shadow-lg)" },
      fontFamily: { sans: ["var(--font-sans)"], mono: ["var(--font-mono)"] },
    },
  },
  plugins: [],
} satisfies Config;
