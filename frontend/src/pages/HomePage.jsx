import { useRef } from "react";
import { Link } from "react-router-dom";
import {
  RefreshCw,
  Minimize2,
  Scissors,
  Gauge,
  Crop,
  Layers,
  Music,
  VolumeX,
  Volume2,
  AudioLines,
  ArrowRight,
  UploadCloud,
} from "lucide-react";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";

const VIDEO_TOOLS = [
  {
    to: "/convert",
    title: "Convert",
    desc: "Change video format",
    icon: RefreshCw,
  },
  {
    to: "/compress",
    title: "Compress",
    desc: "Reduce file size",
    icon: Minimize2,
  },
  {
    to: "/crop-fit",
    title: "Crop & Fit",
    desc: "Frame for any platform",
    icon: Crop,
  },
  {
    to: "/trim",
    title: "Trim",
    desc: "Cut your video",
    icon: Scissors,
  },
  {
    to: "/speed",
    title: "Speed",
    desc: "Adjust playback speed",
    icon: Gauge,
  },
  {
    to: "/merge",
    title: "Merge Videos",
    desc: "Join clips together",
    icon: Layers,
  },
];

const AUDIO_TOOLS = [
  {
    to: "/extract-audio",
    title: "Extract Audio",
    desc: "Strip high-bitrate MP3, WAV, FLAC, M4A, or Opus audio tracks from any video.",
    icon: Music,
  },
  {
    to: "/mute",
    title: "Mute Video",
    desc: "Remove every audio track and export a clean, completely silent video.",
    icon: VolumeX,
  },
  {
    to: "/volume",
    title: "Adjust Volume",
    desc: "Boost quiet recordings or attenuate loud soundtracks up to 300%.",
    icon: Volume2,
  },
  {
    to: "/replace-audio",
    title: "Replace Audio",
    desc: "Swap or overlay a fresh soundtrack onto your video with optional looping.",
    icon: AudioLines,
  },
];

export default function HomePage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error } = useMediaUpload();
  const fileInputRef = useRef(null);

  const handleQuickUpload = async (file) => {
    try {
      await upload(file);
    } catch {
      // handled by useMediaUpload error state
    }
  };

  return (
    <div className="home-page">
      <section className="home-hero">
        <div className="home-eyebrow"><span /> Your complete media workspace</div>
        <h1 className="home-hero-title">Every media tool you need.<br /><em>None of the complexity.</em></h1>
        <p className="home-hero-desc">
          Convert, compress, trim, crop, merge, fine-tune video and audio — all from one fast, focused workspace.
        </p>
        {!currentMedia && (
          <>
            <input
              ref={fileInputRef}
              className="hero-file-input"
              type="file"
              accept="video/*,audio/*"
              onChange={(event) => event.target.files?.[0] && handleQuickUpload(event.target.files[0])}
            />
            <button
              type="button"
              className="hero-upload-button"
              onClick={() => fileInputRef.current?.click()}
              disabled={uploading}
            >
              <UploadCloud size={18} />
              {uploading ? "Uploading..." : "Start with a video"}
            </button>
          </>
        )}
      </section>

      {/* Error display if upload failed */}
      {error && (
        <div style={{ marginBottom: "1.5rem" }}>
          <ErrorBanner message={error} />
        </div>
      )}

      {/* Media Status or Quick Dropzone */}
      {currentMedia && (
        <div style={{ marginBottom: "2.5rem" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
              <span style={{ fontSize: "0.85rem", color: "var(--text-muted)", fontWeight: 500 }}>
                Active media in workspace:
              </span>
            </div>
            <MediaFileCard
              media={currentMedia}
              onChangeMedia={clearCurrentMedia}
            />
            <MediaPreview
              src={currentMedia.previewUrl}
              mediaType={currentMedia.mediaType}
              title={currentMedia.originalFilename}
            />
          </div>
        </div>
      )}

      {/* Video Workspaces */}
      <section style={{ marginBottom: "2.5rem" }}>
        <div className="home-section-heading">
          <div><span className="home-section-kicker">Video</span><h2>Make every frame count.</h2></div>
          <span>{VIDEO_TOOLS.length} tools</span>
        </div>

        <div className="home-tools-grid video-tools-grid">
          {VIDEO_TOOLS.map((tool) => {
            const Icon = tool.icon;
            return (
              <Link key={tool.to} to={tool.to} className="home-tool-card">
                <div className="home-tool-card-top">
                  <div className="home-tool-card-icon">
                    <Icon size={20} />
                  </div>
                  <div className="home-tool-card-title">
                    {tool.title}
                    <ArrowRight size={16} color="var(--text-muted)" />
                  </div>
                </div>
                <p className="home-tool-card-desc">{tool.desc}</p>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Audio Workspaces */}
      <section>
        <div className="home-section-heading">
          <div><span className="home-section-kicker">Audio</span><h2>Sound exactly right.</h2></div>
          <span>{AUDIO_TOOLS.length} tools</span>
        </div>

        <div className="home-tools-grid audio-tools-grid">
          {AUDIO_TOOLS.map((tool) => {
            const Icon = tool.icon;
            return (
              <Link key={tool.to} to={tool.to} className="home-tool-card">
                <div className="home-tool-card-top">
                  <div className="home-tool-card-icon">
                    <Icon size={20} />
                  </div>
                  <div className="home-tool-card-title">
                    {tool.title}
                    <ArrowRight size={16} color="var(--text-muted)" />
                  </div>
                </div>
                <p className="home-tool-card-desc">{tool.desc}</p>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
