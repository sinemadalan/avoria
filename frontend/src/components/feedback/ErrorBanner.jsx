import { AlertCircle, RefreshCw } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext.jsx";

export default function ErrorBanner({
  message,
  onRetry,
  title,
}) {
  const { t } = useLanguage();
  if (!message) return null;

  return (
    <div className="error-banner" role="alert">
      <AlertCircle size={20} style={{ flexShrink: 0, marginTop: "2px" }} />
      <div className="error-banner-content">
        <div className="error-banner-title">{title || t("Processing Error")}</div>
        <p>{message}</p>
      </div>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="action-btn-secondary"
          style={{
            padding: "0.35rem 0.75rem",
            fontSize: "0.8rem",
            borderColor: "rgba(239, 68, 68, 0.4)",
            color: "#fca5a5",
          }}
        >
          <RefreshCw size={12} />
          {t("Retry")}
        </button>
      )}
    </div>
  );
}
