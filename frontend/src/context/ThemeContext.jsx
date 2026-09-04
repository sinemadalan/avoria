import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";

const STORAGE_KEY = "avoria-theme";
const THEMES = new Set(["light", "dark"]);
const ThemeContext = createContext(null);

function getInitialTheme() {
  const documentTheme = document.documentElement.dataset.theme;
  if (THEMES.has(documentTheme)) return documentTheme;

  const savedTheme = window.localStorage.getItem(STORAGE_KEY);
  if (THEMES.has(savedTheme)) return savedTheme;

  return window.matchMedia?.("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

export function ThemeProvider({ children }) {
  const [theme, setThemeState] = useState(getInitialTheme);

  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    window.localStorage.setItem(STORAGE_KEY, theme);
  }, [theme]);

  const setTheme = useCallback((nextTheme) => {
    if (THEMES.has(nextTheme)) setThemeState(nextTheme);
  }, []);

  const toggleTheme = useCallback(() => {
    setThemeState((currentTheme) => currentTheme === "dark" ? "light" : "dark");
  }, []);

  const value = useMemo(
    () => ({ theme, setTheme, toggleTheme }),
    [theme, setTheme, toggleTheme],
  );

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>;
}

export function useTheme() {
  const context = useContext(ThemeContext);
  if (!context) throw new Error("useTheme must be used within ThemeProvider");
  return context;
}
