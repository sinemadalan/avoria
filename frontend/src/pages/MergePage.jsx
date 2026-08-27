import { useState } from "react";
import { Layers, ArrowUp, ArrowDown, Trash2, Plus, Film } from "lucide-react";
import UploadDropzone from "../components/media/UploadDropzone.jsx";
import AspectRatioSelector from "../components/controls/AspectRatioSelector.jsx";
import ProcessingState from "../components/feedback/ProcessingState.jsx";
import ResultPanel from "../components/feedback/ResultPanel.jsx";
import ErrorBanner from "../components/feedback/ErrorBanner.jsx";
import { uploadMedia } from "../api/media.js";
import { useJobPolling } from "../hooks/useJobPolling.js";

const MERGE_RATIOS = [
  { id: "first_video", label: "First Video", sub: "Match 1st Clip", w: 26, h: 20 },
  { id: "16:9", label: "16:9", sub: "Landscape", w: 32, h: 18 },
  { id: "9:16", label: "9:16", sub: "Portrait", w: 18, h: 32 },
  { id: "1:1", label: "1:1", sub: "Square", w: 24, h: 24 },
  { id: "4:5", label: "4:5", sub: "Social", w: 20, h: 25 },
];

export default function MergePage() {
  const { submitAndTrack, status, isProcessing, isCompleted, output, error, resetJob } = useJobPolling();

  const [videoList, setVideoList] = useState([]);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const [targetAspectRatio, setTargetAspectRatio] = useState("first_video");

  const handleAddVideo = async (file) => {
    try {
      setUploadError(null);
      setUploading(true);

      const res = await uploadMedia(file);
      const newVideo = {
        mediaId: res.media_id,
        filename: res.original_filename,
        sizeBytes: res.size_bytes,
      };

      setVideoList((prev) => [...prev, newVideo]);
    } catch (err) {
      setUploadError(err.message || "Failed to upload video.");
    } finally {
      setUploading(false);
    }
  };

  const handleMoveUp = (index) => {
    if (index <= 0) return;
    setVideoList((prev) => {
      const copy = [...prev];
      const temp = copy[index - 1];
      copy[index - 1] = copy[index];
      copy[index] = temp;
      return copy;
    });
  };

  const handleMoveDown = (index) => {
    if (index >= videoList.length - 1) return;
    setVideoList((prev) => {
      const copy = [...prev];
      const temp = copy[index + 1];
      copy[index + 1] = copy[index];
      copy[index] = temp;
      return copy;
    });
  };

  const handleRemove = (index) => {
    setVideoList((prev) => prev.filter((_, i) => i !== index));
  };

  const handleProcess = async () => {
    if (videoList.length < 2) return;

    await submitAndTrack({
      operation: "merge_videos",
      parameters: {
        media_ids: videoList.map((v) => v.mediaId),
        target_aspect_ratio: targetAspectRatio,
      },
    });
  };

  return (
    <div className="workspace">
      <header className="workspace-header">
        <h1 className="workspace-title">Merge Videos</h1>
        <p className="workspace-description">
          Concatenate multiple video clips sequentially into a unified master video.
        </p>
      </header>

      <div className="workspace-body">
        {uploadError && <ErrorBanner message={uploadError} />}

        {isProcessing && (
          <ProcessingState
            title="Concatenating video sequences"
            subtitle={`Harmonizing ${videoList.length} clips to ${targetAspectRatio} aspect ratio...`}
          />
        )}

        {isCompleted && (
          <ResultPanel
            output={output}
            mediaType="video"
            onReset={() => {
              resetJob();
              setVideoList([]);
            }}
            title="Videos merged successfully"
          />
        )}

        {error && <ErrorBanner message={error} onRetry={handleProcess} />}

        {!isProcessing && !isCompleted && (
          <>
            {/* Video Sequence List */}
            <div className="workspace-section">
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
                <h2 className="workspace-section-title">
                  <Film size={16} color="var(--accent-primary)" />
                  Video Playlist ({videoList.length})
                </h2>
                <span style={{ fontSize: "0.8rem", color: videoList.length < 2 ? "var(--warning)" : "var(--success)" }}>
                  {videoList.length < 2 ? "Minimum 2 videos required" : "Ready to merge"}
                </span>
              </div>

              {videoList.length > 0 ? (
                <div className="merge-list">
                  {videoList.map((video, idx) => (
                    <div key={`${video.mediaId}-${idx}`} className="merge-item">
                      <div className="merge-item-order">{idx + 1}</div>
                      <div className="merge-item-name">{video.filename}</div>

                      <div className="merge-item-controls">
                        <button
                          type="button"
                          className="merge-btn-small"
                          onClick={() => handleMoveUp(idx)}
                          disabled={idx === 0}
                          aria-label="Move clip up"
                        >
                          <ArrowUp size={14} />
                        </button>
                        <button
                          type="button"
                          className="merge-btn-small"
                          onClick={() => handleMoveDown(idx)}
                          disabled={idx === videoList.length - 1}
                          aria-label="Move clip down"
                        >
                          <ArrowDown size={14} />
                        </button>
                        <button
                          type="button"
                          className="merge-btn-small merge-btn-remove"
                          onClick={() => handleRemove(idx)}
                          aria-label="Remove clip"
                        >
                          <Trash2 size={14} />
                        </button>
                      </div>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ fontSize: "0.85rem", color: "var(--text-muted)", fontStyle: "italic" }}>
                  No videos added yet. Upload at least 2 videos below.
                </p>
              )}
            </div>

            {/* Upload Area for adding more clips */}
            <div className="workspace-section">
              <UploadDropzone
                onFileSelect={handleAddVideo}
                uploading={uploading}
                accept="video/*"
                title={videoList.length === 0 ? "Add your first video clip" : "Add another video clip"}
                subtitle="Upload clips in order or rearrange them using controls above"
              />
            </div>

            {/* Aspect Ratio Setting */}
            <div className="workspace-section">
              <h2 className="workspace-section-title">Output Canvas Aspect Ratio</h2>
              <p className="workspace-section-subtitle">
                Normalize merged clips to a uniform aspect ratio.
              </p>
              <AspectRatioSelector
                value={targetAspectRatio}
                onChange={setTargetAspectRatio}
                options={MERGE_RATIOS}
              />
            </div>

            {/* Merge Action */}
            <div className="workspace-section">
              <button
                type="button"
                className="action-btn-primary"
                onClick={handleProcess}
                disabled={videoList.length < 2 || uploading}
              >
                <Layers size={18} />
                {videoList.length >= 2
                  ? `Merge ${videoList.length} Videos`
                  : "Upload at least 2 videos to merge"}
              </button>
            </div>
          </>
        )}
      </div>
    </div>
  );
}
