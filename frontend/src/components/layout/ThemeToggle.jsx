import { Moon, Sun } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext.jsx";
import { useTheme } from "../../context/ThemeContext.jsx";

export default function ThemeToggle() {
  const { t } = useLanguage();
  const { theme, toggleTheme } = useTheme();
  const isDark = theme === "dark";
  const label = t(isDark ? "Enable light theme" : "Enable dark theme");

  return (
    <button
      type="button"
      className="theme-toggle"
      onClick={toggleTheme}
      aria-label={label}
      title={label}
    >
      {isDark ? <Sun size={17} /> : <Moon size={17} />}
    </button>
  );
}
