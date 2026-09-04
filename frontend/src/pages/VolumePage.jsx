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
import { useLanguage } from "../context/LanguageContext.jsx";

export default function VolumePage() {
  const { t } = useLanguage();
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
        <h1 className="workspace-title">{t("Adjust Audio Volume")}</h1>
        <p className="workspace-description">
          {t("Upload a video or audio file and set the volume from 0% to 200% to mute, lower or boost its sound, then export a new file at your chosen level.")}
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*,.mp3,.wav,.m4a,.flac,.ogg,.opus"
            title={t("Select video or audio file")}
            subtitle={t("Drag & drop or browse media file")}
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
            title={t("Adjusting audio gain")}
            subtitle={t("Applying {volume}% gain multiplier...", { volume: volumePercent })}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType={currentMedia?.mediaType || "video"}
            onReset={resetJob}
            title={t("Volume adjustment complete")}
            subtitle={t("Preview your {volume}% volume result below or download the finished file.", { volume: volumePercent })}
            variant="volume"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel={t(currentMedia?.mediaType === "audio" ? "Download audio file" : "Download video file")}
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType={currentMedia?.mediaType || "video"}
              title={t("{volume}% volume {type}", { volume: volumePercent, type: t(currentMedia?.mediaType === "audio" ? "Audio file" : "Video file").toLocaleLowerCase() })}
            />
          </ResultPanel>
        )}

        {(uploadError || error) && <ErrorBanner title={t(uploadError ? "Upload Error" : "Processing Error")} message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <h2 className="workspace-section-title">{t("Gain Multiplier")}</h2>
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
                {currentMedia ? t("Set Volume to {volume}%", { volume: volumePercent }) : t("Upload media to adjust volume")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
