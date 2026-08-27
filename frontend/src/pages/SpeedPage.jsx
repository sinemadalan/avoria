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
import { getJobDownloadUrl } from "../api/jobs.js";

const SPEED_PRESETS = [0.5, 0.75, 1, 1.25, 1.5, 2, 3, 4];

export default function SpeedPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [speed, setSpeed] = useState(1);
  const selectedSpeedIndex = SPEED_PRESETS.indexOf(speed);

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
          Upload a video or audio file, choose one of the supported playback speeds from 0.5x to 4x, and create a faster or slower version while keeping voices and music natural.
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
            title="Adjusted media ready"
            subtitle={`Preview your ${speed}x result below or download the finished file.`}
            variant="speed"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel="Download media"
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType={currentMedia?.mediaType || "video"}
              title={`${speed}x ${currentMedia?.mediaType === "audio" ? "audio" : "video"}`}
            />
          </ResultPanel>
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
                  min="0"
                  max={SPEED_PRESETS.length - 1}
                  step="1"
                  value={selectedSpeedIndex}
                  onChange={(e) => setSpeed(SPEED_PRESETS[Number(e.target.value)])}
                  className="range-input"
                  aria-label="Speed multiplier"
                  aria-valuetext={`${speed}x`}
                />

                <div className="speed-slider-options" aria-hidden="true">
                  {SPEED_PRESETS.map((preset) => (
                    <span
                      key={preset}
                      className={speed === preset ? "speed-slider-option-active" : ""}
                    >
                      {preset}x
                    </span>
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
