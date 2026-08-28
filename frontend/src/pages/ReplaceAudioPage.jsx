import { useState } from "react";
import { AudioLines, Music, Film } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { uploadMedia } from "../api/media.js";
import { getJobDownloadUrl } from "../api/jobs.js";

export default function ReplaceAudioPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [audioMedia, setAudioMedia] = useState(null);
  const [uploadingAudio, setUploadingAudio] = useState(false);
  const [audioError, setAudioError] = useState(null);
  const [loop, setLoop] = useState(false);

  const handleUploadAudio = async (file) => {
    try {
      setAudioError(null);
      setUploadingAudio(true);
      const res = await uploadMedia(file);
      const previewUrl = URL.createObjectURL(file);
      setAudioMedia({
        mediaId: res.media_id,
        filename: res.original_filename,
        sizeBytes: res.size_bytes,
        previewUrl,
      });
    } catch (err) {
      setAudioError(err.message || "Failed to upload audio file.");
    } finally {
      setUploadingAudio(false);
    }
  };

  const handleProcess = async () => {
    if (!currentMedia?.mediaId || !audioMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "replace_audio",
      parameters: {
        audio_media_id: audioMedia.mediaId,
        loop: Boolean(loop),
      },
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Replace Audio</h1>
        <p className="workspace-description">
          Upload a base video and a new audio track to replace the original soundtrack. You can also loop shorter audio so it continues for the full video duration.
        </p>
      </header>

      <div className="workspace-body">
        {isProcessing && (
          <ProcessingState
            title="Multiplexing new audio track"
            subtitle="Aligning replacement audio with video stream..."
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="video"
            onReset={() => {
              resetJob();
              setAudioMedia(null);
            }}
            title="Audio replaced successfully"
            subtitle="Preview the video with its new soundtrack below or download the finished file."
            variant="replace-audio"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel="Download video"
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType="video"
              title="Video with replaced audio"
            />
          </ResultPanel>
        )}

        {error && <ErrorBanner message={error} onRetry={handleProcess} />}

        {!isProcessing && !isCompleted && (
          <>
            {/* Step 1: Base Video */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">
                <Film size={16} color="var(--accent-primary)" />
                1. Base Video File
              </h2>

              {!currentMedia ? (
                <UploadDropzone
                  onFileSelect={upload}
                  uploading={uploading}
                  accept="video/*"
                  title="Select base video file"
                  subtitle="Drag & drop or browse video"
                />
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "1rem" }}>
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
            </div>

            {/* Step 2: Replacement Audio */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">
                <Music size={16} color="var(--accent-primary)" />
                2. Replacement Audio Track
              </h2>

              {audioError && <ErrorBanner message={audioError} />}

              {!audioMedia ? (
                <UploadDropzone
                  onFileSelect={handleUploadAudio}
                  uploading={uploadingAudio}
                  accept="audio/*"
                  title="Select replacement audio file"
                  subtitle="Drag & drop MP3, WAV, FLAC, M4A, Opus, or AAC"
                />
              ) : (
                <div style={{ display: "flex", flexDirection: "column", gap: "0.75rem" }}>
                  <div className="media-file-card">
                    <div className="media-file-info">
                      <div className="media-file-icon">
                        <Music size={20} />
                      </div>
                      <div className="media-file-meta">
                        <div className="media-file-name">{audioMedia.filename}</div>
                        <div className="media-file-details">
                          <span>Audio Track</span>
                        </div>
                      </div>
                    </div>
                    <button
                      type="button"
                      onClick={() => setAudioMedia(null)}
                      className="media-file-action"
                    >
                      Change audio
                    </button>
                  </div>

                  <audio
                    src={audioMedia.previewUrl}
                    controls
                    style={{ width: "100%", height: "40px", marginTop: "0.25rem" }}
                  />
                </div>
              )}
            </div>

            {/* Step 3: Playback Options */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">Audio Loop Settings</h2>

              <label
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.75rem",
                  backgroundColor: "var(--bg-surface)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-md)",
                  padding: "1rem 1.25rem",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={loop}
                  onChange={(e) => setLoop(e.target.checked)}
                  style={{ width: 18, height: 18, accentColor: "var(--accent-primary)" }}
                />
                <div>
                  <div style={{ fontSize: "0.95rem", fontWeight: 600, color: "var(--text-primary)" }}>
                    Loop soundtrack
                  </div>
                  <div style={{ fontSize: "0.8rem", color: "var(--text-secondary)" }}>
                    Repeat audio if it is shorter than the total video duration.
                  </div>
                </div>
              </label>
            </div>

            {/* Submit Action */}
            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia || !audioMedia}
              >
                <AudioLines size={18} />
                {currentMedia && audioMedia
                  ? "Replace Audio Track"
                  : !currentMedia
                  ? "Upload base video first"
                  : "Upload replacement audio track"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
