import { useLanguage } from "../../context/LanguageContext.jsx";

export default function ProcessingState({
  title,
  subtitle,
  progress = null,
}) {
  const { t } = useLanguage();
  return (
    <div className="processing-card" role="status" aria-live="polite">
      <div className="spinner-pulse" aria-hidden="true" />
      <div>
        <div className="processing-title">{title || t("Processing your media")}</div>
        <div className="processing-sub">{subtitle || t("Avoria is executing your media transformation...")}</div>
      </div>
      {progress !== null && (
        <div style={{ width: "100%", maxWidth: "300px", marginTop: "0.5rem" }}>
          <div
            style={{
              height: "6px",
              backgroundColor: "var(--bg-surface-elevated)",
              borderRadius: "var(--radius-full)",
              overflow: "hidden",
            }}
          >
            <div
              style={{
                width: `${Math.min(100, Math.max(0, progress))}%`,
                height: "100%",
                background: "var(--accent-gradient)",
                transition: "width 0.3s ease",
              }}
            />
          </div>
          <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", marginTop: "4px", display: "block" }}>
            {t("{progress}% completed", { progress })}
          </span>
        </div>
      )}
    </div>
  );
}
