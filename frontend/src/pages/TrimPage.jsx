import { useState, useEffect } from "react";
import { Scissors, Clock } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import TimeInput from "../components/controls/TimeInput.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { getJobDownloadUrl } from "../api/jobs.js";

export default function TrimPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [duration, setDuration] = useState(60);
  const [startSeconds, setStartSeconds] = useState(0);
  const [endSeconds, setEndSeconds] = useState(10);

  useEffect(() => {
    const dur = currentMedia?.metadata?.format?.duration_seconds;
    if (dur && dur > 0) {
      setDuration(dur);
      setEndSeconds(Math.min(dur, Math.max(1, Math.round(dur))));
    }
  }, [currentMedia]);

  const handleMetadataLoaded = (e) => {
    const dur = e.target.duration;
    if (dur && !isNaN(dur) && isFinite(dur)) {
      setDuration(dur);
      if (endSeconds > dur || endSeconds === 10) {
        setEndSeconds(Math.min(dur, Math.max(1, Math.round(dur))));
      }
    }
  };

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    if (startSeconds >= endSeconds) {
      return;
    }

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "trim",
      parameters: {
        start_seconds: Number(startSeconds),
        end_seconds: Number(endSeconds),
      },
    });
  };

  const trimmedLength = Math.max(0, endSeconds - startSeconds);

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Trim Media</h1>
        <p className="workspace-description">
          Upload a video or audio file, enter exact start and end times, and export only the section you want without keeping the unwanted beginning or ending.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*,audio/*"
            title="Select video or audio to trim"
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
              onLoadedMetadata={handleMetadataLoaded}
            />
          </div>
        )}

        {isProcessing && (
          <ProcessingState
            title="Trimming media track"
            subtitle={`Cutting from ${startSeconds.toFixed(1)}s to ${endSeconds.toFixed(1)}s...`}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType={currentMedia?.mediaType || "video"}
            onReset={resetJob}
            title="Trimmed media ready"
            subtitle="Preview your selected clip below or download the finished file."
            variant="trim"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel="Download media"
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType={currentMedia?.mediaType || "video"}
              title={`Trimmed ${currentMedia?.mediaType === "audio" ? "audio" : "video"}`}
            />
          </ResultPanel>
        )}

        {(uploadError || error) && <ErrorBanner message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h2 className="workspace-section-title">
                  <Clock size={16} color="var(--accent-primary)" />
                  Cut Boundaries
                </h2>
                <span style={{ fontSize: "0.85rem", color: "var(--text-secondary)" }}>
                  Output Duration: <strong style={{ color: "var(--accent-primary)" }}>{trimmedLength.toFixed(1)}s</strong>
                </span>
              </div>

              <div className="time-inputs-row">
                <TimeInput
                  label="Start Timestamp (seconds)"
                  value={startSeconds}
                  onChange={setStartSeconds}
                  min={0}
                  max={endSeconds - 0.1}
                />
                <TimeInput
                  label="End Timestamp (seconds)"
                  value={endSeconds}
                  onChange={setEndSeconds}
                  min={startSeconds + 0.1}
                  max={duration}
                />
              </div>

              {startSeconds >= endSeconds && (
                <p style={{ color: "var(--danger)", fontSize: "0.8rem" }}>
                  End timestamp must be strictly greater than start timestamp.
                </p>
              )}
            </div>

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia || startSeconds >= endSeconds}
              >
                <Scissors size={18} />
                {currentMedia ? `Trim Media (${trimmedLength.toFixed(1)}s)` : "Upload media to trim"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
