import { Check } from "lucide-react";

export default function OptionCard({
  title,
  description,
  badge,
  selected = false,
  onClick,
  icon: Icon,
}) {
  return (
    <button
      type="button"
      className={`option-card ${selected ? "selected" : ""}`}
      onClick={onClick}
      aria-pressed={selected}
    >
      <div className="option-card-header">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {Icon && <Icon size={16} color="var(--accent-primary)" />}
          <span className="option-card-title">{title}</span>
        </div>
        {badge && <span className="option-card-badge">{badge}</span>}
      </div>
      {description && <p className="option-card-desc">{description}</p>}
    </button>
  );
}
