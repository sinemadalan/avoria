import { useLanguage } from "../../context/LanguageContext.jsx";

const PRESETS = ["#000000", "#16161A", "#1e293b", "#0f172a", "#ffffff"];

export default function ColorPicker({ value = "#000000", onChange }) {
  const { t } = useLanguage();
  const handleHexChange = (e) => {
    let val = e.target.value.trim();
    if (!val.startsWith("#")) val = "#" + val;
    onChange(val);
  };

  return (
    <div className="color-picker-group">
      <input
        type="color"
        value={value.length === 7 ? value : "#000000"}
        onChange={(e) => onChange(e.target.value)}
        className="color-input-native"
        aria-label={t("Select color")}
      />
      <input
        type="text"
        value={value}
        onChange={handleHexChange}
        maxLength={7}
        className="color-hex-text"
        placeholder="#000000"
        aria-label={t("Hex color value")}
      />
      <div className="color-presets">
        {PRESETS.map((preset) => (
          <button
            key={preset}
            type="button"
            className="color-preset-btn"
            style={{ backgroundColor: preset }}
            onClick={() => onChange(preset)}
            title={t("Select {color}", { color: preset })}
            aria-label={t("Select preset color {color}", { color: preset })}
          />
        ))}
      </div>
    </div>
  );
}
