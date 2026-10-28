/** Tokens live in src/styles/tokens.css; Tailwind only exposes them. */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        base: "var(--bg-base)",
        ink: { DEFAULT: "var(--text-primary)", soft: "var(--text-secondary)" },
        kb: { personal: "var(--kb-personal)", role: "var(--kb-role)", company: "var(--kb-company)" },
        ok: "var(--success)",
        warn: "var(--warning)",
        danger: "var(--danger)",
        info: "var(--info)",
        accent: { from: "var(--accent-from)", via: "var(--accent-via)", to: "var(--accent-to)" },
      },
      borderRadius: { md: "var(--radius-md)", lg: "var(--radius-lg)" },
      boxShadow: { glass: "var(--shadow-glass)", glow: "var(--shadow-glow)" },
      fontFamily: { sans: ["Inter", "system-ui", "sans-serif"], mono: ["JetBrains Mono", "ui-monospace", "monospace"] },
    },
  },
  plugins: [],
};
