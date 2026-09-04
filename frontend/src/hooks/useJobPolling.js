import { useState, useEffect, useRef, useCallback } from "react";
import { createJob, getJobStatus } from "../api/jobs.js";
import { useLanguage } from "../context/LanguageContext.jsx";

const POLLING_INTERVAL_MS = 1500;
const MAX_TRANSIENT_POLL_ERRORS = 6;
const RETRYABLE_STATUS_CODES = new Set([429, 500, 502, 503, 504]);

function isRetryablePollingError(error) {
  return error instanceof TypeError || RETRYABLE_STATUS_CODES.has(error?.status);
}

export function useJobPolling() {
  const { t } = useLanguage();
  const [jobId, setJobId] = useState(null);
  const [status, setStatus] = useState("idle"); // idle | queued | processing | completed | failed
  const [progress, setProgress] = useState(null);
  const [output, setOutput] = useState(null);
  const [error, setError] = useState(null);

  const pollingTimerRef = useRef(null);
  const consecutivePollErrorsRef = useRef(0);
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
    consecutivePollErrorsRef.current = 0;

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
      setError(err.message || t("Failed to start processing job."));
      throw err;
    }
  }, [t]);

  const poll = useCallback(async (id) => {
    clearTimer();

    try {
      const data = await getJobStatus(id);
      if (!isMountedRef.current) return;

      consecutivePollErrorsRef.current = 0;
      setStatus(data.status);
      if (data.progress !== undefined) {
        setProgress(data.progress);
      }

      if (data.status === "completed") {
        setOutput(data.output || {});
        return;
      }

      if (data.status === "failed") {
        setError(data.error || t("Media processing failed."));
        return;
      }

      // If still queued or processing, schedule next poll
      pollingTimerRef.current = setTimeout(() => {
        poll(id);
      }, POLLING_INTERVAL_MS);
    } catch (err) {
      if (!isMountedRef.current) return;

      if (
        isRetryablePollingError(err)
        && consecutivePollErrorsRef.current < MAX_TRANSIENT_POLL_ERRORS
      ) {
        consecutivePollErrorsRef.current += 1;
        pollingTimerRef.current = setTimeout(() => {
          poll(id);
        }, POLLING_INTERVAL_MS);
        return;
      }

      setStatus("failed");
      setError(
        isRetryablePollingError(err)
          ? t("Processing finished, but its status could not be checked. Please try again.")
          : (err.message || t("Error checking job status.")),
      );
    }
  }, [t]);

  const resetJob = useCallback(() => {
    clearTimer();
    setJobId(null);
    setStatus("idle");
    setProgress(null);
    setOutput(null);
    setError(null);
    consecutivePollErrorsRef.current = 0;
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
