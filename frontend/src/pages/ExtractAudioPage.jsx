import { useState } from "react";
import { Music } from "lucide-react";
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
import { useLanguage } from "../context/LanguageContext.jsx";

const AUDIO_FORMATS = [
  { id: "mp3", label: "MP3 Audio", badge: "Universal", desc: "Standard 320kbps MP3 compatible with every audio player." },
  { id: "wav", label: "WAV (Uncompressed)", badge: "Studio Master", desc: "Lossless uncompressed PCM audio preserving pristine frequency response." },
  { id: "flac", label: "FLAC (Lossless)", badge: "High Fidelity", desc: "Bit-perfect lossless compression for audiophile preservation." },
  { id: "m4a", label: "M4A (AAC)", badge: "Apple Standard", desc: "Efficient AAC codec ideal for iPhone, iPad, and macOS ecosystems." },
  { id: "opus", label: "Opus Audio", badge: "Modern Web", desc: "State-of-the-art interactive audio codec with ultra-low latency & high efficiency." },
  { id: "ogg", label: "OGG Vorbis", badge: "Open Standard", desc: "Open-source audio container favored by games and web apps." },
];

export default function ExtractAudioPage() {
  const { t } = useLanguage();
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [format, setFormat] = useState("mp3");

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "extract_audio",
      format: format,
      parameters: {},
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">{t("Extract Audio Track")}</h1>
        <p className="workspace-description">
          {t("Upload a video, choose MP3, WAV, FLAC, M4A, Opus or OGG, and extract its soundtrack as a separate audio file for listening, editing or reuse.")}
        </p>
      </header>

      <div className="workspace-body">
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*"
            title={t("Select video file")}
            subtitle={t("Drag & drop or browse video containing audio")}
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
            title={t("Extracting audio track")}
            subtitle={t("Transcoding audio stream to high-quality {format}...", { format: format.toUpperCase() })}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="audio"
            onReset={resetJob}
            title={t("Audio extraction complete")}
            subtitle={t("Listen to your extracted {format} audio below or download the finished file.", { format: format.toUpperCase() })}
            variant="extract-audio"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel={t("Download audio")}
          >
            <MediaPreview
              src={getJobDownloadUrl(jobId)}
              mediaType="audio"
              title={t("Extracted {format} audio", { format: format.toUpperCase() })}
            />
          </ResultPanel>
        )}

        {(uploadError || error) && <ErrorBanner title={t(uploadError ? "Upload Error" : "Processing Error")} message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <h2 className="workspace-section-title">{t("Output Audio Format")}</h2>
              <p className="workspace-section-subtitle">
                {t("Choose the destination audio container and codec.")}
              </p>

              <div className="option-cards-grid extract-audio-format-grid">
                {AUDIO_FORMATS.map((item) => (
                  <OptionCard
                    key={item.id}
                    title={t(item.label)}
                    badge={t(item.badge)}
                    description={t(item.desc)}
                    selected={format === item.id}
                    onClick={() => setFormat(item.id)}
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
                <Music size={18} />
                {currentMedia ? t("Extract as {format}", { format: format.toUpperCase() }) : t("Upload video to extract audio")}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
