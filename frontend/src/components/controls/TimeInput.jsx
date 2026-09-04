import { useEffect, useState } from "react";

export default function TimeInput({
  label,
  value,
  onChange,
  max,
  min = 0,
  step = 0.5,
}) {
  const [draftValue, setDraftValue] = useState(String(value ?? ""));

  useEffect(() => {
    setDraftValue(String(value ?? ""));
  }, [value]);

  const commitValue = () => {
    const parsedValue = Number(draftValue);
    if (draftValue.trim() === "" || !Number.isFinite(parsedValue)) {
      setDraftValue(String(value ?? ""));
      return;
    }

    setDraftValue(String(parsedValue));
    onChange(parsedValue);
  };

  const formatSeconds = (sec) => {
    if (sec === undefined || sec === null || isNaN(sec)) return "00:00.0";
    const minutes = Math.floor(sec / 60);
    const remainingSec = (sec % 60).toFixed(1);
    const paddedMin = String(minutes).padStart(2, "0");
    const paddedSec = String(remainingSec).padStart(4, "0");
    return `${paddedMin}:${paddedSec}`;
  };

  return (
    <div className="time-input-card">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <label className="time-input-label">{label}</label>
        <span style={{ fontSize: "0.75rem", color: "var(--text-muted)", fontFamily: "var(--font-mono)" }}>
          {formatSeconds(value)}
        </span>
      </div>
      <input
        type="number"
        min={min}
        max={max}
        step={step}
        value={draftValue}
        onChange={(event) => setDraftValue(event.target.value)}
        onBlur={commitValue}
        onWheel={(event) => event.currentTarget.blur()}
        onKeyDown={(event) => {
          if (event.key === "Enter") {
            event.currentTarget.blur();
          }
          if (event.key === "Escape") {
            setDraftValue(String(value ?? ""));
            event.currentTarget.blur();
          }
        }}
        className="time-input-field"
        aria-label={label}
      />
    </div>
  );
}
