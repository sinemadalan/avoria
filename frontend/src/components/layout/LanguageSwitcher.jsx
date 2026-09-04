import { useLanguage } from "../../context/LanguageContext.jsx";

export default function LanguageSwitcher() {
  const { language, setLanguage, t } = useLanguage();

  return (
    <div className="language-switcher" role="group" aria-label={t("Switch language")}>
      <button
        type="button"
        className={language === "tr" ? "active" : ""}
        onClick={() => setLanguage("tr")}
        aria-pressed={language === "tr"}
      >
        TR
      </button>
      <span aria-hidden="true" />
      <button
        type="button"
        className={language === "en" ? "active" : ""}
        onClick={() => setLanguage("en")}
        aria-pressed={language === "en"}
      >
        EN
      </button>
    </div>
  );
}
