const BACKEND_RATIOS = [
  { id: "16:9", label: "16:9", sub: "Landscape (1920×1080)", w: 32, h: 18 },
  { id: "9:16", label: "9:16", sub: "Portrait (1080×1920)", w: 18, h: 32 },
  { id: "1:1", label: "1:1", sub: "Square (1080×1080)", w: 24, h: 24 },
  { id: "4:5", label: "4:5", sub: "Social (1080×1350)", w: 20, h: 25 },
];

export default function AspectRatioSelector({ value, onChange, options = BACKEND_RATIOS }) {
  return (
    <div className="aspect-grid" role="radiogroup" aria-label="Aspect Ratio">
      {options.map((ratio) => {
        const isSelected = value === ratio.id;
        return (
          <button
            key={ratio.id}
            type="button"
            className={`aspect-btn ${isSelected ? "selected" : ""}`}
            onClick={() => onChange(ratio.id)}
            role="radio"
            aria-checked={isSelected}
          >
            <div
              className="aspect-box-preview"
              style={{
                width: `${ratio.w}px`,
                height: `${ratio.h}px`,
                color: isSelected ? "var(--accent-primary)" : "var(--text-muted)",
              }}
            />
            <span className="aspect-label">{ratio.label}</span>
            <span className="aspect-sub">{ratio.sub}</span>
          </button>
        );
      })}
    </div>
  );
}
