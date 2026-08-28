import { useState } from "react";
import { Volume2 } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import VolumeSlider from "../components/controls/VolumeSlider.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { getJobDownloadUrl } from "../api/jobs.js";

export default function VolumePage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [volumePercent, setVolumePercent] = useState(150);

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "volume",
      parameters: {
        volume_percent: parseInt(volumePercent, 10),
      },
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Adjust Audio Volume</h1>
        <p className="workspace-description">
          Upload a video or audio file and set the volume from 0% to 200% to mute, lower or boost its sound, then export a new file at your chosen level.
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*,.mp3,.wav,.m4a,.flac,.ogg,.opus"
            title="Select video or audio file"
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
            title="Adjusting audio gain"
            subtitle={`Applying ${volumePercent}% gain multiplier...`}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType={currentMedia?.mediaType || "video"}
            onReset={resetJob}
            title="Volume adjustment complete"
            subtitle={`Preview your ${volumePercent}% volume result below or download the finished file.`}
            variant="volume"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel={`Download ${currentMedia?.mediaType === "audio" ? "audio" : "video"}`}
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType={currentMedia?.mediaType || "video"}
              title={`${volumePercent}% volume ${currentMedia?.mediaType === "audio" ? "audio" : "video"}`}
            />
          </ResultPanel>
        )}

        {(uploadError || error) && <ErrorBanner title={uploadError ? "Upload Error" : "Processing Error"} message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <h2 className="workspace-section-title">Gain Multiplier</h2>
              <VolumeSlider
                value={volumePercent}
                onChange={setVolumePercent}
                min={0}
                max={200}
                step={5}
              />
            </div>

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={!currentMedia}
              >
                <Volume2 size={18} />
                {currentMedia ? `Set Volume to ${volumePercent}%` : "Upload media to adjust volume"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
