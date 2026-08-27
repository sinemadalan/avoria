import { useState, useCallback } from "react";
import { uploadMedia, inspectMedia } from "../api/media.js";
import { useMediaContext } from "../context/MediaContext.jsx";

const ALLOWED_EXTENSIONS = [
  ".mp4", ".mov", ".mkv", ".webm", ".avi",
  ".mp3", ".wav", ".m4a", ".aac", ".flac", ".ogg", ".opus"
];

const MAX_SIZE_BYTES = 2 * 1024 * 1024 * 1024; // 2GB

export function useMediaUpload() {
  const { currentMedia, setCurrentMedia, clearCurrentMedia } = useMediaContext();
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState(null);

  const validateFile = (file) => {
    if (!file) {
      throw new Error("Please select a file to upload.");
    }
    const ext = "." + file.name.split(".").pop().toLowerCase();
    if (!ALLOWED_EXTENSIONS.includes(ext)) {
      throw new Error(`Unsupported file type (${ext}). Allowed: MP4, MOV, MKV, WebM, MP3, WAV, FLAC, etc.`);
    }
    if (file.size > MAX_SIZE_BYTES) {
      throw new Error("File exceeds maximum allowed size (2GB).");
    }
  };

  const upload = useCallback(
    async (file) => {
      try {
        setError(null);
        validateFile(file);
        setUploading(true);

        const isVideo = file.type.startsWith("video/") || /\.(mp4|mov|mkv|webm|avi)$/i.test(file.name);
        const localPreviewUrl = URL.createObjectURL(file);

        // Upload to backend
        const uploadRes = await uploadMedia(file);

        // Attempt media inspection in background for duration/codec info
        let metadata = null;
        try {
          const inspectRes = await inspectMedia(uploadRes.media_id);
          metadata = inspectRes;
        } catch {
          // Non-blocking if ffprobe inspection encounters an issue
        }

        const mediaObj = {
          mediaId: uploadRes.media_id,
          file,
          previewUrl: localPreviewUrl,
          mediaType: isVideo ? "video" : "audio",
          sizeBytes: uploadRes.size_bytes,
          originalFilename: uploadRes.original_filename,
          metadata,
        };

        setCurrentMedia(mediaObj);
        return mediaObj;
      } catch (err) {
        setError(err.message || "Failed to upload media file.");
        throw err;
      } finally {
        setUploading(false);
      }
    },
    [setCurrentMedia]
  );

  return {
    currentMedia,
    setCurrentMedia,
    clearCurrentMedia,
    upload,
    uploading,
    error,
    setError,
  };
}
