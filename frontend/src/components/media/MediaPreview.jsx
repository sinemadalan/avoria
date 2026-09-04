import { Play, Volume2, Film } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext.jsx";

export default function MediaPreview({
  src,
  mediaType = "video",
  title,
  onLoadedMetadata,
  aspectRatio,
}) {
  const { t } = useLanguage();
  if (!src) return null;

  const aspectRatioClass = {
    "9:16": " media-preview-box--portrait",
    "1:1": " media-preview-box--square",
    "4:5": " media-preview-box--social-portrait",
  }[aspectRatio] || "";

  return (
    <div className={`media-preview-box${aspectRatioClass}`}>
      <div className="media-preview-header">
        <span style={{ display: "flex", alignItems: "center", gap: "0.4rem", fontWeight: 500 }}>
          {mediaType === "video" ? <Film size={14} /> : <Volume2 size={14} />}
          {title || t("Media Preview")}
        </span>
        <span>{t("Local Preview")}</span>
      </div>

      {mediaType === "video" ? (
        <video
          src={src}
          controls
          playsInline
          className="media-preview-element"
          onLoadedMetadata={onLoadedMetadata}
        >
          {t("Your browser does not support video playback.")}
        </video>
      ) : (
        <div className="audio-preview-body">
          <audio
            src={src}
            controls
            className="audio-preview-element"
            onLoadedMetadata={onLoadedMetadata}
          >
            {t("Your browser does not support audio playback.")}
          </audio>
        </div>
      )}
    </div>
  );
}
