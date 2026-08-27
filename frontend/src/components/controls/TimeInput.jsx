export default function TimeInput({
  label,
  value,
  onChange,
  max,
  min = 0,
  step = 0.5,
}) {
  const handleChange = (e) => {
    const num = parseFloat(e.target.value);
    if (isNaN(num)) {
      onChange(0);
    } else {
      onChange(Math.max(min, max !== undefined ? Math.min(max, num) : num));
    }
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
        value={value}
        onChange={handleChange}
        className="time-input-field"
        aria-label={label}
      />
    </div>
  );
}
