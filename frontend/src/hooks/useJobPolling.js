import { useState, useEffect, useRef, useCallback } from "react";
import { createJob, getJobStatus } from "../api/jobs.js";

const POLLING_INTERVAL_MS = 1500;

export function useJobPolling() {
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | queued | processing | completed | failed
  const [progress, setProgress] = useState(null);
  const [output, setOutput] = useState(null);
  const [error, setError] = useState(null);

  const pollingTimerRef = useRef(null);
  const isMountedRef = useRef(true);

  const clearTimer = () => {
    if (pollingTimerRef.current) {
      clearTimeout(pollingTimerRef.current);
      pollingTimerRef.current = null;
    }
  };

  const submitAndTrack = useCallback(async (jobRequest) => {
    clearTimer();
    setError(null);
    setOutput(null);
    setProgress(null);
    setStatus("queued");

    try {
      const response = await createJob(jobRequest);
      if (!isMountedRef.current) return;

      setJobId(response.job_id);
      setStatus(response.status || "queued");
      poll(response.job_id);
      return response;
    } catch (err) {
      if (!isMountedRef.current) return;
      setStatus("failed");
      setError(err.message || "Failed to start processing job.");
      throw err;
    }
  }, []);

  const poll = useCallback(async (id) => {
    clearTimer();

    try {
      const data = await getJobStatus(id);
      if (!isMountedRef.current) return;

      setStatus(data.status);
      if (data.progress !== undefined) {
        setProgress(data.progress);
      }

      if (data.status === "completed") {
        setOutput(data.output || {});
        return;
      }

      if (data.status === "failed") {
        setError(data.error || "Media processing failed.");
        return;
      }

      // If still queued or processing, schedule next poll
      pollingTimerRef.current = setTimeout(() => {
        poll(id);
      }, POLLING_INTERVAL_MS);
    } catch (err) {
      if (!isMountedRef.current) return;
      setStatus("failed");
      setError(err.message || "Error checking job status.");
    }
  }, []);

  const resetJob = useCallback(() => {
    clearTimer();
    setJobId(null);
    setStatus("idle");
    setProgress(null);
    setOutput(null);
    setError(null);
  }, []);

  useEffect(() => {
    isMountedRef.current = true;
    return () => {
      isMountedRef.current = false;
      clearTimer();
    };
  }, []);

  return {
    jobId,
    status,
    progress,
    output,
    error,
    isProcessing: status === "queued" || status === "processing",
    isCompleted: status === "completed",
    isFailed: status === "failed",
    submitAndTrack,
    resetJob,
  };
}
