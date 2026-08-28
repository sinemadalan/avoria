import { VolumeX } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { getJobDownloadUrl } from "../api/jobs.js";

export default function MutePage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "mute",
      parameters: {},
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Mute Video</h1>
        <p className="workspace-description">
          Upload a video to remove every embedded audio stream and create a completely silent copy while keeping the original picture and playback duration unchanged.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*"
            title="Select video to mute"
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
            title="Removing audio streams"
            subtitle="Generating a completely silent video output..."
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="video"
            onReset={resetJob}
            title="Video muted successfully"
            subtitle="Preview your silent video below or download the finished file."
            variant="mute"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel="Download video"
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType="video"
              title="Muted video"
            />
          </ResultPanel>
        )}

        {(uploadError || error) && <ErrorBanner title={uploadError ? "Upload Error" : "Processing Error"} message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <div className="workspace-section">
            <div
              style={{
                backgroundColor: "var(--bg-surface)",
                border: "1px solid var(--border-subtle)",
                borderRadius: "var(--radius-md)",
                padding: "1.25rem",
                display: "flex",
                alignItems: "center",
                gap: "1rem",
              }}
            >
              <div
                style={{
                  width: 40,
                  height: 40,
                  borderRadius: "var(--radius-full)",
                  backgroundColor: "var(--danger-bg)",
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  color: "var(--danger)",
                  flexShrink: 0,
                }}
              >
                <VolumeX size={20} />
              </div>
              <div style={{ flex: 1 }}>
                <div style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>
                  Strip Audio Stream
                </div>
                <p style={{ fontSize: "0.85rem", color: "var(--text-secondary)", marginTop: "0.2rem" }}>
                  All embedded soundtracks and commentary will be eliminated. Visual quality is preserved.
                </p>
              </div>
            </div>

            <button
              type="button"
              className="action-btn-primary"
              onClick={handleProcess}
              disabled={!currentMedia}
            >
              <VolumeX size={18} />
              {currentMedia ? "Mute and Export Video" : "Upload video to mute"}
            </button>
          </div>
        )}
      </div>
    </div>
  );
}
