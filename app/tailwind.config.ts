import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {
      colors: {
        moo: {
          cream: "#FFF7E8",
          warm: "#F5C97B",
          ink: "#1F1A14",
          accent: "#7E5BEF",
        },
      },
      borderRadius: { moo: "1.25rem" },
    },
  },
  plugins: [],
};

export default config;
