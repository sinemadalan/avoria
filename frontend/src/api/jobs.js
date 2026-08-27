import { apiClient } from "./client.js";

/**
 * Create a new asynchronous media processing job
 * @param {object} request
 * @param {string} [request.media_id]
 * @param {string} request.operation
 * @param {object} request.parameters
 * @param {string} [request.format]
 * @returns {Promise<{ job_id: string, media_id: string, operation: string, status: string }>}
 */
export async function createJob(request) {
  return apiClient("/jobs", {
    method: "POST",
    body: request,
  });
}

/**
 * Fetch current status, progress, output, or error of a processing job
 * @param {string} jobId
 * @returns {Promise<{ job_id: string, media_id: string, operation: string, status: string, output?: object, progress?: number, error?: string }>}
 */
export async function getJobStatus(jobId) {
  return apiClient(`/jobs/${jobId}`, {
    method: "GET",
  });
}

export function getJobDownloadUrl(jobId) {
  const baseUrl = import.meta.env.VITE_API_BASE_URL || "/api/v1";
  return `${baseUrl}/jobs/${encodeURIComponent(jobId)}/download`;
}
