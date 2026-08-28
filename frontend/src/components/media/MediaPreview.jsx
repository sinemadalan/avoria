import { Play, Volume2, Film } from "lucide-react";

export default function MediaPreview({
  src,
  mediaType = "video",
  title = "Media Preview",
  onLoadedMetadata,
  aspectRatio,
}) {
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
          {title}
        </span>
        <span>Local Preview</span>
      </div>

      {mediaType === "video" ? (
        <video
          src={src}
          controls
          playsInline
          className="media-preview-element"
          onLoadedMetadata={onLoadedMetadata}
        >
          Your browser does not support video playback.
        </video>
      ) : (
        <audio
          src={src}
          controls
          className="audio-preview-element"
          onLoadedMetadata={onLoadedMetadata}
        >
          Your browser does not support audio playback.
        </audio>
      )}
    </div>
  );
}
