/** @type {import('tailwindcss').Config} */
export default {
  content: ["./index.html", "./src/**/*.{js,jsx}"],
  theme: {
    extend: {
      colors: {
        base: "#0a0e1a",
        panel: "#0f1424",
        border: "#1c2438",
        cyan: {
          DEFAULT: "#00d4ff",
        },
        warn: "#ff6b35",
        success: "#00ff88",
        danger: "#ff3366",
      },
      fontFamily: {
        mono: ["JetBrains Mono", "ui-monospace", "SFMono-Regular", "monospace"],
        sans: ["Inter", "ui-sans-serif", "system-ui", "sans-serif"],
      },
    },
  },
  plugins: [],
};
