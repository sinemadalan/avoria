import { Check } from "lucide-react";

export default function OptionCard({
  title,
  description,
  badge,
  selected = false,
  onClick,
  icon: Icon,
  trailing,
}) {
  const CardElement = trailing ? "div" : "button";

  return (
    <CardElement
      type={trailing ? undefined : "button"}
      className={`option-card ${selected ? "selected" : ""}`}
      onClick={onClick}
      onKeyDown={trailing ? (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          onClick?.();
        }
      } : undefined}
      role={trailing ? "button" : undefined}
      tabIndex={trailing ? 0 : undefined}
      aria-pressed={selected}
    >
      <div className="option-card-header">
        <div style={{ display: "flex", alignItems: "center", gap: "0.5rem" }}>
          {Icon && <Icon size={16} color="var(--accent-primary)" />}
          <span className="option-card-title">{title}</span>
        </div>
        {trailing || (badge && <span className="option-card-badge">{badge}</span>)}
      </div>
      {description && <p className="option-card-desc">{description}</p>}
    </CardElement>
  );
}
