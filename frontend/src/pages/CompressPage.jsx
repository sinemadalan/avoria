import { useState } from "react";
import { Minimize2, Zap, ShieldCheck, HardDrive } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import OptionCard from "../components/controls/OptionCard.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { getJobDownloadUrl } from "../api/jobs.js";

const COMPRESSION_LEVELS = [
  {
    id: "light",
    title: "High Quality",
    badge: "Visually Lossless",
    desc: "Preserves pristine detail and color accuracy with moderate size reduction.",
    icon: ShieldCheck,
  },
  {
    id: "balanced",
    title: "Balanced",
    badge: "Recommended",
    desc: "Optimal balance between small file size and sharp visual clarity for sharing.",
    icon: Zap,
  },
  {
    id: "strong",
    title: "Small File",
    badge: "Maximum Savings",
    desc: "Compact video size ideal for email attachments and bandwidth-constrained apps.",
    icon: HardDrive,
  },
];

export default function CompressPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [level, setLevel] = useState("balanced");

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "compress",
      parameters: {
        compression_level: level,
      },
    });
  };

  return (
    <div className="workspace compress-workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Compress Video</h1>
        <p className="workspace-description">
          Upload a video and choose a compression level to reduce its file size for easier storage or sharing. Avoria re-encodes the file and shows how much space you saved.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*"
            title="Select video to compress"
            subtitle="Drag & drop or browse high-resolution video"
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
            title="Compressing video stream"
            subtitle="Applying rate control and encoder optimization..."
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="video"
            onReset={resetJob}
            title="Compressed video ready"
            subtitle="Your new file is ready to download."
            variant="compression"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel="Download video"
          />
        )}

        {(uploadError || error) && <ErrorBanner message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <h2 className="workspace-section-title">Compression Preset</h2>
              <p className="workspace-section-subtitle">
                Choose the desired balance between file size savings and visual fidelity.
              </p>

              <div className="option-cards-grid">
                {COMPRESSION_LEVELS.map((item) => (
                  <OptionCard
                    key={item.id}
                    title={item.title}
                    badge={item.badge}
                    description={item.desc}
                    icon={item.icon}
                    selected={level === item.id}
                    onClick={() => setLevel(item.id)}
                  />
                ))}
              </div>
            </div>

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia}
              >
                <Minimize2 size={18} />
                {currentMedia ? "Compress Video" : "Upload a video to compress"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
