// Theme state: light/dark via data-theme attr on <html>. Persisted to
// localStorage, first load follows prefers-color-scheme. Context so charts
// re-render with new colors on toggle.
import { createContext, useContext, useEffect, useState } from "react";

export type Theme = "light" | "dark";

const ThemeContext = createContext<{ theme: Theme; toggle: () => void }>({
  theme: "light", toggle: () => {} });

export function useThemeState() {
  const [theme, setTheme] = useState<Theme>(() =>
    (localStorage.getItem("theme") as Theme | null) ??
    (window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    localStorage.setItem("theme", theme);
  }, [theme]);
  return { theme, toggle: () => setTheme((t) => (t === "light" ? "dark" : "light")) };
}

export const ThemeProvider = ThemeContext.Provider;
export function useTheme() { return useContext(ThemeContext); }

// Hex palettes for recharts (SVG presentation attrs can't resolve CSS vars).
export function useChartColors() {
  const { theme } = useTheme();
  return theme === "dark"
    ? { green: "#6fcf9b", accent: "#ff7a3d", ink: "#eeeee6", hairline: "#3a3a30",
        palette: ["#6fcf9b", "#ff7a3d", "#8fb8a8", "#d9c08a", "#9a9a8a",
                  "#7ea7c2", "#c98a7d", "#a89bc8"] }
    : { green: "#1d5c3e", accent: "#d4500f", ink: "#191914", hairline: "#c9c9bd",
        palette: ["#1d5c3e", "#d4500f", "#5a7a6a", "#a8842c", "#6a6a5a",
                  "#3e6a87", "#a85a4d", "#6a5b88"] };
}
