import { useState } from "react";
import { Crop, Maximize2, Palette, Sparkles } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import AspectRatioSelector from "../components/controls/AspectRatioSelector.jsx";
import OptionCard from "../components/controls/OptionCard.jsx";
import ColorPicker from "../components/controls/ColorPicker.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";

const FIT_MODES = [
  {
    id: "crop",
    title: "Crop to Fill",
    desc: "Fills the entire target aspect ratio frame by center-cropping edges.",
    icon: Crop,
  },
  {
    id: "fit",
    title: "Fit Inside (Letterbox/Pillarbox)",
    desc: "Keeps 100% of video content visible, filling empty outer margins.",
    icon: Maximize2,
  },
];

const BG_TYPES = [
  {
    id: "color",
    title: "Solid Color Background",
    desc: "Fill outer bars with a clean custom color or dark slate palette.",
    icon: Palette,
  },
  {
    id: "blur",
    title: "Blurred Video Background",
    desc: "Generates an aesthetic Gaussian-blurred background from the video.",
    icon: Sparkles,
  },
];

export default function CropFitPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [aspectRatio, setAspectRatio] = useState("9:16");
  const [fitMode, setFitMode] = useState("fit");
  const [backgroundType, setBackgroundType] = useState("color");
  const [backgroundColor, setBackgroundColor] = useState("#16161A");

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    const params = {
      aspect_ratio: aspectRatio,
      mode: fitMode,
    };

    if (fitMode === "fit") {
      params.background_type = backgroundType;
      if (backgroundType === "color") {
        params.background_color = backgroundColor;
      }
    }

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "crop",
      parameters: params,
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Crop & Fit</h1>
        <p className="workspace-description">
          Upload a video, choose the aspect ratio you need, then crop it to fill the frame or fit it with a color or blurred background for different screens and social platforms.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*"
            title="Select video to reframe"
            subtitle="Drag & drop or browse video file"
          />
        ) : (
          <div className="workspace-section">
            <MediaFileCard
              media={currentMedia}
              onChangeMedia={clearCurrentMedia}
            />
            <MediaPreview
              src={currentMedia.previewUrl}
              mediaType="video"
              title={currentMedia.originalFilename}
            />
          </div>
        )}

        {isProcessing && (
          <ProcessingState
            title="Rendering aspect ratio geometry"
            subtitle={`Target ratio: ${aspectRatio} with ${fitMode === "crop" ? "center crop" : `fit (${backgroundType})`}...`}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="video"
            onReset={resetJob}
            title="Crop & Fit complete"
          />
        )}

        {(uploadError || error) && <ErrorBanner message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            {/* Aspect Ratio Selection */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">Target Aspect Ratio</h2>
              <p className="workspace-section-subtitle">
                Select your target canvas format.
              </p>
              <AspectRatioSelector
                value={aspectRatio}
                onChange={setAspectRatio}
              />
            </div>

            {/* Fit Mode Selection */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">Framing Mode</h2>
              <p className="workspace-section-subtitle">
                Choose how content aligns within the target aspect ratio.
              </p>
              <div className="option-cards-grid">
                {FIT_MODES.map((mode) => (
                  <OptionCard
                    key={mode.id}
                    title={mode.title}
                    description={mode.desc}
                    icon={mode.icon}
                    selected={fitMode === mode.id}
                    onClick={() => setFitMode(mode.id)}
                  />
                ))}
              </div>
            </div>

            {/* Conditional Background Options for Fit Mode */}
            {fitMode === "fit" && (
              <div className="workspace-section">
                <h2 className="workspace-section-title">Background Style</h2>
                <p className="workspace-section-subtitle">
                  Fill style for outer letterbox/pillarbox margins.
                </p>

                <div className="option-cards-grid" style={{ marginBottom: "1rem" }}>
                  {BG_TYPES.map((bg) => (
                    <OptionCard
                      key={bg.id}
                      title={bg.title}
                      description={bg.desc}
                      icon={bg.icon}
                      selected={backgroundType === bg.id}
                      onClick={() => setBackgroundType(bg.id)}
                    />
                  ))}
                </div>

                {backgroundType === "color" && (
                  <div style={{ marginTop: "0.5rem" }}>
                    <label style={{ fontSize: "0.85rem", color: "var(--text-secondary)", display: "block", marginBottom: "0.5rem" }}>
                      Selected Margin Color:
                    </label>
                    <ColorPicker
                      value={backgroundColor}
                      onChange={setBackgroundColor}
                    />
                  </div>
                )}
              </div>
            )}

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia}
              >
                <Crop size={18} />
                {currentMedia ? `Process Crop & Fit (${aspectRatio})` : "Upload a video to reframe"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
