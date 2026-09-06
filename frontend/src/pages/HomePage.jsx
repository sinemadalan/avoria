import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import {
  ArrowRight,
  AudioLines,
  Crop,
  FastForward,
  Layers,
  Minimize2,
  Music,
  RefreshCw,
  Scissors,
  UploadCloud,
  Volume2,
  VolumeX,
} from "lucide-react";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useLanguage } from "../context/LanguageContext.jsx";

const VIDEO_GUIDES = [
  {
    title: "Convert",
    to: "/convert",
    icon: RefreshCw,
    description: "Convert a video or audio file to a different format.",
    steps: [
      "Upload your video or audio file.",
      "Choose one of the available output formats.",
      "Select Convert and wait for processing to finish.",
      "Preview or download the converted file.",
    ],
    note: "Video can be converted to formats such as MP4, MOV, MKV, and WebM. Compatible media can also be exported as audio.",
  },
  {
    title: "Compress",
    to: "/compress",
    icon: Minimize2,
    description: "Reduce a video's file size to make it easier to store and share.",
    steps: [
      "Upload the video you want to compress.",
      "Choose High Quality, Balanced, or Small File.",
      "Select Compress Video.",
      "Compare the original and processed sizes, then download the result.",
    ],
    note: "Stronger compression creates a smaller file, but it may reduce visual quality.",
  },
  {
    title: "Trim",
    to: "/trim",
    icon: Scissors,
    description: "Export only the selected time range from a video or audio file.",
    steps: [
      "Upload your video or audio file.",
      "Enter the start and end times in seconds.",
      "Select Trim Media.",
      "Preview and download the selected section.",
    ],
    note: "The start time must be earlier than the end time, and both values must be within the media duration.",
  },
  {
    title: "Speed",
    to: "/speed",
    icon: FastForward,
    description: "Create a faster or slower version of a video or audio file.",
    steps: [
      "Upload your media file.",
      "Choose a playback speed between 0.5x and 4x.",
      "Apply the selected playback speed.",
      "Preview or download the processed file.",
    ],
    note: "Increasing the speed shortens the output duration, while slowing it down makes the output longer.",
  },
  {
    title: "Crop & Fit",
    to: "/crop-fit",
    icon: Crop,
    description: "Prepare a video for different screens and social media aspect ratios.",
    steps: [
      "Upload your video.",
      "Choose 16:9, 9:16, 1:1, or 4:5 as the target aspect ratio.",
      "Choose Crop to Fill or Fit Inside.",
      "If you use Fit Inside, select a solid color or blurred background.",
      "Process the video, then preview or download the result.",
    ],
    note: "Crop to Fill fills the frame by removing content outside its edges. Fit Inside keeps the entire video visible and fills empty space with the selected background.",
  },
  {
    title: "Merge Videos",
    to: "/merge",
    icon: Layers,
    description: "Combine multiple video clips into one continuous video.",
    steps: [
      "Upload at least two video clips.",
      "Use the arrow controls to arrange the clips in playback order.",
      "Remove any clips you do not want to include.",
      "Choose a shared output aspect ratio.",
      "Select Merge Videos, then preview or download the result.",
    ],
    note: "First Video uses the first clip's aspect ratio. A fixed ratio fits every clip into the same canvas and fills any remaining space.",
  },
  {
    title: "Mute Video",
    to: "/mute",
    icon: VolumeX,
    description: "Create a silent copy by removing every audio track from a video.",
    steps: [
      "Upload the video you want to mute.",
      "Select Mute Video to start processing.",
      "After processing is complete, preview or download the silent video.",
    ],
    note: "The picture and playback duration remain unchanged.",
  },
];

const AUDIO_GUIDES = [
  {
    title: "Extract Audio",
    to: "/extract-audio",
    icon: Music,
    description: "Save a video's audio track as a separate audio file.",
    steps: [
      "Upload a video that contains audio.",
      "Choose MP3, WAV, FLAC, M4A, Opus, or OGG.",
      "Select Extract Audio.",
      "Listen to or download the resulting audio file.",
    ],
    note: "The video remains unchanged. Only its audio track is saved as a separate file in the format you select.",
  },
  {
    title: "Volume",
    to: "/volume",
    icon: Volume2,
    description: "Lower, mute, or boost the sound of a video or audio file.",
    steps: [
      "Upload your video or audio file.",
      "Set the volume level between 0% and 200%.",
      "Apply the selected volume level.",
      "Preview or download the processed file.",
    ],
    note: "0% mutes the sound, 100% keeps the original level, and 200% doubles it.",
  },
  {
    title: "Replace Audio",
    to: "/replace-audio",
    icon: AudioLines,
    description: "Replace a video's current soundtrack with another audio file.",
    steps: [
      "Upload the base video.",
      "Upload the replacement audio track.",
      "Enable looping if the new audio is shorter than the video.",
      "Select Replace Audio Track.",
      "Preview the video with its new soundtrack, then download it.",
    ],
    note: "When looping is enabled, shorter audio repeats until the video ends.",
  },
];

const QUICK_START_STEPS = [
  "Upload the media file you want to process.",
  "Open the tool you want to use from the sidebar.",
  "Choose the format, quality, time range, or visual settings.",
  "Start processing and wait for it to finish.",
  "Preview the result or download it to your device.",
];

function GuideDetail({ guide }) {
  const { t } = useLanguage();
  const Icon = guide.icon;

  return (
    <article className="guide-detail-panel">
      <header className="guide-detail-header">
        <span className="guide-detail-icon"><Icon size={23} /></span>
        <div>
          <h2>{t(guide.title)}</h2>
          <p>{t(guide.description)}</p>
        </div>
      </header>

      <div className="guide-detail-content">
        <div className="guide-detail-steps">
          <h3>{t("How to use it")}</h3>
          <ol>
            {guide.steps.map((step) => <li key={step}>{t(step)}</li>)}
          </ol>
        </div>
        <p className="guide-note"><strong>{t("Good to know:")}</strong> {t(guide.note)}</p>
      </div>

      <footer className="guide-detail-footer">
        <Link className="guide-tool-link" to={guide.to}>
          {t("Open Tool")} <ArrowRight size={15} />
        </Link>
      </footer>
    </article>
  );
}

export default function HomePage() {
  const { t } = useLanguage();
  const { currentMedia, upload, uploading, clearCurrentMedia, error } = useMediaUpload();
  const fileInputRef = useRef(null);
  const [activeCategory, setActiveCategory] = useState("video");
  const [selectedGuide, setSelectedGuide] = useState(VIDEO_GUIDES[0]);

  const selectCategory = (category) => {
    setActiveCategory(category);
    setSelectedGuide(category === "video" ? VIDEO_GUIDES[0] : AUDIO_GUIDES[0]);
  };

  const activeGuides = activeCategory === "video" ? VIDEO_GUIDES : AUDIO_GUIDES;

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
        <div className="home-eyebrow"><span /> {t("Your complete media workspace")}</div>
        <h1 className="home-hero-title">{t("Every media tool you need.")}<br /><em>{t("None of the complexity.")}</em></h1>
        <p className="home-hero-desc">
          {t("Convert, compress, trim, crop, merge, fine-tune video and audio — all from one fast, focused workspace.")}
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
              {uploading ? t("Uploading...") : t("Start with a video")}
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
                {t("Active media in workspace:")}
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

      <div className="home-guide-content">
        <section className="guide-quick-start" aria-labelledby="quick-start-title">
          <h2 id="quick-start-title">{t("Quick Start")}</h2>
          <ol>
            {QUICK_START_STEPS.map((step) => <li key={step}>{t(step)}</li>)}
          </ol>
        </section>

        <section className="guide-browser" aria-label={t("Media Tools Guide")}>
          <div className="guide-category-tabs" role="tablist" aria-label={t("Media Type")}>
            <button
              type="button"
              className={activeCategory === "video" ? "active" : ""}
              onClick={() => selectCategory("video")}
              role="tab"
              aria-selected={activeCategory === "video"}
            >
              {t("Video Tools")}
            </button>
            <button
              type="button"
              className={activeCategory === "audio" ? "active" : ""}
              onClick={() => selectCategory("audio")}
              role="tab"
              aria-selected={activeCategory === "audio"}
            >
              {t("Audio Tools")}
            </button>
          </div>

          <div
            className={`guide-tool-tabs guide-tool-tabs--${activeCategory}`}
            role="tablist"
            aria-label={t(activeCategory === "video" ? "Video Tools" : "Audio Tools")}
          >
            {activeGuides.map((guide) => {
              const Icon = guide.icon;
              const isActive = selectedGuide.to === guide.to;
              return (
                <button
                  type="button"
                  className={isActive ? "active" : ""}
                  key={guide.to}
                  onClick={() => setSelectedGuide(guide)}
                  role="tab"
                  aria-selected={isActive}
                >
                  <Icon size={17} />
                  <span>{t(guide.title)}</span>
                </button>
              );
            })}
          </div>

          <GuideDetail guide={selectedGuide} />
        </section>
      </div>
    </div>
  );
}
