import { CheckCircle2, Download, RefreshCw } from "lucide-react";

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let val = bytes;
  let unitIndex = 0;
  while (val >= 1024 && unitIndex < units.length - 1) {
    val /= 1024;
    unitIndex++;
  }
  return `${val.toFixed(1)} ${units[unitIndex]}`;
}

export default function ResultPanel({
  children,
  output,
  mediaType = "video",
  onReset,
  title = "Your media is ready",
  subtitle = "Your processed file is ready to use.",
  downloadUrl,
  downloadLabel = "Download file",
  variant = "default",
}) {
  return (
    <div className={`result-card ${variant !== "default" ? `${variant}-result-card` : ""}`} role="region" aria-label="Processing result">
      <div className="result-header">
        <div className="result-success-icon" aria-hidden="true">
          <CheckCircle2 size={20} />
        </div>
        <div>
          <h2 className="result-title">
            {title}
          </h2>
          <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginTop: "0.2rem" }}>
            {subtitle}
          </p>
        </div>
      </div>

      {children}

      {/* Metrics (if compression or size reduction present) */}
      {(output?.original_size || output?.compressed_size || output?.reduction_percentage) && (
        <div className="result-metrics-grid">
          {output.original_size && (
            <div className="result-metric-item">
              <span className="result-metric-label">Original Size</span>
              <span className="result-metric-value">{formatBytes(output.original_size)}</span>
            </div>
          )}
          {output.compressed_size && (
            <div className="result-metric-item">
              <span className="result-metric-label">Processed Size</span>
              <span className="result-metric-value">{formatBytes(output.compressed_size)}</span>
            </div>
          )}
          {output.reduction_percentage !== undefined && output.reduction_percentage !== null && (
            <div className="result-metric-item">
              <span className="result-metric-label">Reduction</span>
              <span className="result-metric-value" style={{ color: "var(--success)" }}>
                {output.reduction_percentage.toFixed(1)}%
              </span>
            </div>
          )}
        </div>
      )}

      {/* Action buttons */}
      <div className="result-actions">
        {downloadUrl && (
          <a href={downloadUrl} download className="action-btn-primary result-download-button">
            <Download size={16} />
            {downloadLabel}
          </a>
        )}
        <button
          type="button"
          onClick={onReset}
          className="action-btn-secondary result-reset-button"
        >
          <RefreshCw size={16} />
          Process another file
        </button>
      </div>
    </div>
  );
}
