import { apiClient } from "./client.js";

/**
 * Upload a media file to the backend
 * @param {File} file
 * @returns {Promise<{ media_id: string, original_filename: string, filename: string, content_type: string, size_bytes: number }>}
 */
export async function uploadMedia(file) {
  const formData = new FormData();
  formData.append("file", file);

  return apiClient("/media/upload", {
    method: "POST",
    body: formData,
  });
}

/**
 * Inspect uploaded media metadata and streams using FFprobe
 * @param {string} mediaId
 * @returns {Promise<{ media_id: string, format: object, video_streams: Array, audio_streams: Array }>}
 */
export async function inspectMedia(mediaId) {
  return apiClient(`/media/${mediaId}/inspect`, {
    method: "POST",
  });
}

/**
 * Fetch dynamic conversion options supported by FFmpeg capabilities
 * @returns {Promise<{ defaults: object, containers: Array }>}
 */
export async function getConversionOptions() {
  return apiClient("/media/conversion-options", {
    method: "GET",
  });
}
