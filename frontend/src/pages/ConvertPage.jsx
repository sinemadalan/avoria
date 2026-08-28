import { useState, useEffect } from "react";
import { RefreshCw, Play, Settings2 } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import MediaPreview from "../components/media/MediaPreview.jsx";
import MediaFileCard from "../components/media/MediaFileCard.jsx";
import OptionCard from "../components/controls/OptionCard.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { useMediaUpload } from "../hooks/useMediaUpload.js";
import { useJobPolling } from "../hooks/useJobPolling.js";
import { getConversionOptions } from "../api/media.js";
import { getJobDownloadUrl } from "../api/jobs.js";

const DEFAULT_CONTAINERS = [
  { id: "mp4", label: "MP4", desc: "Maximum compatibility across all devices and web platforms.", videoCodec: "h264", audioCodec: "aac", isAudioOnly: false },
  { id: "mov", label: "MOV", desc: "Apple standard format, optimal for editing in Final Cut / Premiere.", videoCodec: "h264", audioCodec: "aac", isAudioOnly: false },
  { id: "webm", label: "WebM", desc: "Modern open web format with superior compression for browsers.", videoCodec: "vp9", audioCodec: "opus", isAudioOnly: false },
  { id: "mkv", label: "MKV", desc: "Flexible container supporting high fidelity and multiple audio streams.", videoCodec: "h264", audioCodec: "aac", isAudioOnly: false },
  { id: "mp3", label: "MP3", desc: "Universal compressed audio for playback and sharing.", videoCodec: "none", audioCodec: "mp3", isAudioOnly: true },
  { id: "wav", label: "WAV", desc: "Uncompressed PCM audio for editing and archiving.", videoCodec: "none", audioCodec: "pcm", isAudioOnly: true },
  { id: "flac", label: "FLAC", desc: "Lossless compressed audio with full source quality.", videoCodec: "none", audioCodec: "flac", isAudioOnly: true },
  { id: "ogg", label: "OGG", desc: "Open audio container supporting Vorbis, Opus and FLAC.", videoCodec: "none", audioCodec: "vorbis", isAudioOnly: true },
  { id: "m4a", label: "M4A", desc: "Efficient AAC audio for Apple devices and modern players.", videoCodec: "none", audioCodec: "aac", isAudioOnly: true },
  { id: "opus", label: "OPUS", desc: "Modern high-efficiency audio for web and speech.", videoCodec: "none", audioCodec: "opus", isAudioOnly: true },
];

export default function ConvertPage() {
  const { currentMedia, upload, uploading, clearCurrentMedia, error: uploadError } = useMediaUpload();
  const { jobId, submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [selectedContainer, setSelectedContainer] = useState("mp4");
  const [videoCodec, setVideoCodec] = useState("h264");
  const [audioCodec, setAudioCodec] = useState("aac");
  const [containers, setContainers] = useState(DEFAULT_CONTAINERS);

  useEffect(() => {
    async function loadOptions() {
      try {
        const res = await getConversionOptions();
        if (res?.containers?.length > 0) {
          const mapped = res.containers.map((c) => {
            const validVideoCodecs = (c.video_codecs || []).filter((codec) => codec !== "none" && codec !== "copy");
            const validAudioCodecs = (c.audio_codecs || []).filter((codec) => codec !== "none" && codec !== "copy");
            const isAudioOnly = validVideoCodecs.length === 0;

            const displayCodecs = isAudioOnly
              ? (validAudioCodecs.length > 0 ? validAudioCodecs : (c.audio_codecs || []).filter((codec) => codec !== "none"))
              : (validVideoCodecs.length > 0 ? validVideoCodecs : (c.video_codecs || []).filter((codec) => codec !== "none"));

            return {
              id: c.id,
              label: c.id.toUpperCase(),
              desc: `Extension: ${c.extension}, Codecs: ${displayCodecs.join(", ")}`,
              videoCodec: c.video_codecs?.[0] || "h264",
              audioCodec: c.audio_codecs?.[0] || "aac",
              isAudioOnly,
            };
          });
          setContainers(mapped);
        }
      } catch {
        // Fallback to default presets
      }
    }
    loadOptions();
  }, []);

  const isAudioInput = currentMedia?.mediaType === "audio";
  const visibleContainers = isAudioInput
    ? containers.filter((item) => item.isAudioOnly)
    : containers;
  const selectedTarget = containers.find((item) => item.id === selectedContainer);
  const outputMediaType = selectedTarget?.isAudioOnly ? "audio" : "video";

  useEffect(() => {
    if (visibleContainers.length === 0) return;
    if (visibleContainers.some((item) => item.id === selectedContainer)) return;

    const firstAvailable = visibleContainers[0];
    setSelectedContainer(firstAvailable.id);
    setVideoCodec(firstAvailable.videoCodec);
    setAudioCodec(firstAvailable.audioCodec);
  }, [selectedContainer, visibleContainers]);

  const handleSelectContainer = (item) => {
    setSelectedContainer(item.id);
    setVideoCodec(item.videoCodec);
    setAudioCodec(item.audioCodec);
  };

  const handleProcess = async () => {
    if (!currentMedia?.mediaId) return;

    await submitAndTrack({
      media_id: currentMedia.mediaId,
      operation: "convert",
      parameters: {
        container: selectedContainer,
        video_codec: videoCodec,
        audio_codec: audioCodec,
      },
    });
  };

  return (
    <div className="workspace convert-workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Convert Video & Audio</h1>
        <p className="workspace-description">
          Upload a video or audio file and convert it to a compatible media container and codec without changing its original content or duration.
        </p>
      </header>

      <div className="workspace-body">
        {/* Media Upload or Active Preview */}
        {!currentMedia ? (
          <UploadDropzone
            onFileSelect={upload}
            uploading={uploading}
            accept="video/*,audio/*,.m4a,.aac,.flac,.ogg,.opus"
            title="Select video or audio to convert"
            subtitle="Video: MP4, MOV, MKV, WebM, AVI · Audio: MP3, WAV, M4A, AAC, FLAC, OGG, Opus"
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

        {/* Processing State */}
        {isProcessing && (
          <ProcessingState
            title={`Converting your ${isAudioInput ? "audio" : "video"}`}
            subtitle={`Transcoding to ${selectedContainer.toUpperCase()} (${videoCodec === "none" ? audioCodec.toUpperCase() : `${videoCodec.toUpperCase()}/${audioCodec.toUpperCase()}`})...`}
          />
        )}

        {/* Completed State */}
        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType={outputMediaType}
            onReset={resetJob}
            title={`Your converted ${outputMediaType} is ready`}
            subtitle="The new file is ready for playback, editing or sharing."
            variant="conversion"
            downloadUrl={getJobDownloadUrl(jobId)}
            downloadLabel={`Download ${outputMediaType}`}
          />
        )}

        {/* Error Banner */}
        {(uploadError || error) && <ErrorBanner title={uploadError ? "Upload Error" : "Processing Error"} message={uploadError || error} onRetry={error ? handleProcess : undefined} />}

        {/* Controls (always visible when not processing/completed) */}
        {!isProcessing && !isCompleted && (
          <>
            <div className="workspace-section">
              <h2 className="workspace-section-title">Target Container Format</h2>
              <p className="workspace-section-subtitle">
                Select the format you want your {isAudioInput ? "audio" : "video"} saved as.
              </p>

              <div className="option-cards-grid">
                {visibleContainers.map((item) => (
                  <OptionCard
                    key={item.id}
                    title={item.label}
                    description={item.desc}
                    selected={selectedContainer === item.id}
                    onClick={() => handleSelectContainer(item)}
                  />
                ))}
              </div>
            </div>

            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary action-btn-compact"
                onClick={handleProcess}
                disabled={!currentMedia}
              >
                <RefreshCw size={18} />
                {currentMedia ? `Convert to ${selectedContainer.toUpperCase()}` : "Upload video or audio to convert"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
