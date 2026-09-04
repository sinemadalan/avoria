import { Film, Music, RefreshCw } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext.jsx";

function formatFileSize(bytes) {
  if (!bytes) return "";
  const units = ["B", "KB", "MB", "GB"];
  let size = bytes;
  let unitIndex = 0;
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024;
    unitIndex++;
  }
  return `${size.toFixed(1)} ${units[unitIndex]}`;
}

export default function MediaFileCard({ media, onChangeMedia }) {
  const { t } = useLanguage();
  if (!media) return null;

  const isVideo = media.mediaType === "video";
  const durationSec = media.metadata?.format?.duration_seconds;

  return (
    <div className="media-file-card">
      <div className="media-file-info">
        <div className="media-file-icon">
          {isVideo ? <Film size={20} /> : <Music size={20} />}
        </div>
        <div className="media-file-meta">
          <div className="media-file-name" title={media.originalFilename || media.file?.name}>
            {media.originalFilename || media.file?.name || t("Uploaded Media")}
          </div>
          <div className="media-file-details">
            <span>{formatFileSize(media.sizeBytes || media.file?.size)}</span>
            {durationSec ? <span>• {durationSec.toFixed(1)}s</span> : null}
            <span>• {isVideo ? t("Video file") : t("Audio file")}</span>
          </div>
        </div>
      </div>

      {onChangeMedia && (
        <button
          type="button"
          onClick={onChangeMedia}
          className="media-file-action"
          aria-label={t("Change current media file")}
        >
          <RefreshCw size={12} style={{ display: "inline", marginRight: "4px" }} />
          {t("Change file")}
        </button>
      )}
    </div>
  );
}
