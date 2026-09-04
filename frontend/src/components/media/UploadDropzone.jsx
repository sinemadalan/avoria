import { useState, useRef } from "react";
import { UploadCloud, Film, Loader2 } from "lucide-react";
import { useLanguage } from "../../context/LanguageContext.jsx";

export default function UploadDropzone({
  onFileSelect,
  uploading = false,
  accept = "video/*,audio/*",
  title,
  subtitle,
  hint,
}) {
  const { t } = useLanguage();
  const resolvedTitle = title || t("Drop your media here");
  const resolvedSubtitle = subtitle || t("or browse from your device");
  const resolvedHint = hint === undefined
    ? t("Supports MP4, MOV, MKV, WebM, MP3, WAV, FLAC (up to 2GB)")
    : hint;
  const [isDragOver, setIsDragOver] = useState(false);
  const inputRef = useRef(null);

  const handleDragEnter = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(true);
  };

  const handleDragLeave = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);
  };

  const handleDragOver = (e) => {
    e.preventDefault();
    e.stopPropagation();
  };

  const handleDrop = (e) => {
    e.preventDefault();
    e.stopPropagation();
    setIsDragOver(false);

    if (uploading) return;

    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
      const file = e.dataTransfer.files[0];
      onFileSelect(file);
    }
  };

  const handleChange = (e) => {
    if (e.target.files && e.target.files.length > 0) {
      const file = e.target.files[0];
      onFileSelect(file);
    }
  };

  return (
    <div
      className={`dropzone-container ${isDragOver ? "is-dragover" : ""}`}
      onDragEnter={handleDragEnter}
      onDragLeave={handleDragLeave}
      onDragOver={handleDragOver}
      onDrop={handleDrop}
      onClick={() => !uploading && inputRef.current?.click()}
      role="button"
      tabIndex={0}
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          inputRef.current?.click();
        }
      }}
      aria-label={t("Upload dropzone")}
    >
      <input
        ref={inputRef}
        type="file"
        accept={accept}
        onChange={handleChange}
        className="dropzone-input"
        disabled={uploading}
        aria-hidden="true"
        tabIndex={-1}
      />

      <div className="dropzone-icon-wrap">
        {uploading ? (
          <Loader2 className="spinner-pulse" size={24} style={{ animation: "spin 0.8s linear infinite" }} />
        ) : (
          <UploadCloud size={28} />
        )}
      </div>

      <div className="dropzone-title">
        {uploading ? t("Uploading media...") : resolvedTitle}
      </div>
      <div className="dropzone-subtitle">
        {uploading ? t("Please wait while your media is being uploaded") : resolvedSubtitle}
      </div>
      {resolvedHint && <div className="dropzone-hint">{resolvedHint}</div>}
    </div>
  );
}
