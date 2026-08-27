import { Play, Volume2, Film } from "lucide-react";

export default function MediaPreview({
  src,
  mediaType = "video",
  title = "Media Preview",
  onLoadedMetadata,
}) {
  if (!src) return null;

  return (
    <div className="media-preview-box">
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
