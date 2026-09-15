/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0a0e1a",
        panel: "#0f1424",
        border: "#1c2438",
        // Retuned from the original neon set (#00d4ff/#ff6b35/#00ff88/#ff3366)
        // to Tailwind's own -400/-500 shades: same dark "mission control"
        // feel, but restrained enough to read as an enterprise ops tool
        // rather than a hacker-terminal demo. One change here recolors the
        // whole app, since every component references these semantic names
        // (text-cyan, bg-danger/15, etc.) rather than raw hex values.
        cyan: {
          DEFAULT: "#38bdf8",
        },
        warn: "#f59e0b",
        success: "#10b981",
        danger: "#ef4444",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
