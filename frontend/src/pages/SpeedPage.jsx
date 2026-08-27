import { useState } from "react";
import { FastForward } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";

const QUICK_PRESETS = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0, 3.0, 4.0];

export default function SpeedPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [speed, setSpeed] = useState(1.5);

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "speed",
      parameters: {
        speed: Number(speed),
      },
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Adjust Playback Speed</h1>
        <p className="workspace-description">
          Accelerate or slow down video and audio playback while preserving pitch accuracy.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*,audio/*"
            title="Select video or audio to adjust speed"
            subtitle="Drag & drop or browse media file"
          />
        ) : (
          <div className="workspace-section">
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
        )}

        {isProcessing && (
          <ProcessingState
            title="Recalculating frame timestamps"
            subtitle={`Applying ${speed}x tempo transformation...`}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType={currentMedia?.mediaType || "video"}
            onReset={resetJob}
            title="Speed adjustment complete"
          />
        )}

        {(uploadError || error) && <ErrorBanner message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <div className="slider-container">
                <div className="slider-header">
                  <span style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>
                    Speed Multiplier
                  </span>
                  <span className="slider-value-badge" style={{ fontSize: "1.3rem" }}>
                    {speed.toFixed(2).replace(/\.00$/, "")}x
                  </span>
                </div>

                <input
                  type="range"
                  min="0.25"
                  max="4.0"
                  step="0.05"
                  value={speed}
                  onChange={(e) => setSpeed(parseFloat(e.target.value))}
                  className="range-input"
                  aria-label="Speed multiplier"
                />

                <div className="slider-ticks" style={{ marginTop: "0.25rem" }}>
                  <span>0.25x (Slowest)</span>
                  <span>1.0x (Normal)</span>
                  <span>2.0x</span>
                  <span>4.0x (Max)</span>
                </div>

                <div style={{ display: "flex", gap: "0.4rem", flexWrap: "wrap", marginTop: "0.85rem" }}>
                  {QUICK_PRESETS.map((preset) => (
                    <button
                      key={preset}
                      type="button"
                      onClick={() => setSpeed(preset)}
                      style={{
                        padding: "0.35rem 0.75rem",
                        borderRadius: "var(--radius-sm)",
                        fontSize: "0.8rem",
                        fontWeight: 600,
                        backgroundColor: speed === preset ? "var(--bg-surface-active)" : "var(--bg-surface-elevated)",
                        color: speed === preset ? "var(--accent-primary)" : "var(--text-secondary)",
                        border: `1px solid ${speed === preset ? "var(--accent-primary)" : "var(--border-subtle)"}`,
                        transition: "all var(--transition-fast)",
                        cursor: "pointer",
                      }}
                    >
                      {preset}x
                    </button>
                  ))}
                </div>
              </div>
            </div>

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia}
              >
                <FastForward size={18} />
                {currentMedia ? `Apply ${speed}x Playback Speed` : "Upload media to adjust speed"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
