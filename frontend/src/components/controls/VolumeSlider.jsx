import { useLanguage } from "../../context/LanguageContext.jsx";

export default function VolumeSlider({
  value = 100,
  onChange,
  min = 0,
  max = 200,
  step = 5,
}) {
  const { t } = useLanguage();
  return (
    <div className="slider-container">
      <div className="slider-header">
        <span style={{ fontSize: "0.9rem", fontWeight: 600, color: "var(--text-primary)" }}>
          {t("Output Volume Level")}
        </span>
        <span className="slider-value-badge">{value}%</span>
      </div>

      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(parseInt(e.target.value, 10))}
        className="range-input"
        aria-label={t("Volume percentage")}
      />

      <div className="slider-ticks">
        <span>0% ({t("Mute")})</span>
        <span>100% ({t("Original")})</span>
        <span>200% (2x Max)</span>
      </div>
    </div>
  );
}
