import { NavLink } from "react-router-dom";
import {
  Home,
  RefreshCw,
  Minimize2,
  Scissors,
  Gauge,
  Crop,
  Layers,
  Music,
  VolumeX,
  Volume2,
  AudioLines,
  ChevronRight,
} from "lucide-react";
import avoriaLogo from "../../styles/avoria_logo.png";
import { useLanguage } from "../../context/LanguageContext.jsx";

export const navItems = [
  {
    category: null,
    items: [{ to: "/", label: "Home", icon: Home }],
  },
  {
    category: "Video",
    items: [
      { to: "/convert", label: "Convert", icon: RefreshCw },
      { to: "/compress", label: "Compress", icon: Minimize2 },
      { to: "/trim", label: "Trim", icon: Scissors },
      { to: "/speed", label: "Speed", icon: Gauge },
      { to: "/crop-fit", label: "Crop & Fit", icon: Crop },
      { to: "/merge", label: "Merge Videos", icon: Layers },
      { to: "/mute", label: "Mute Video", icon: VolumeX },
    ],
  },
  {
    category: "Audio",
    items: [
      { to: "/extract-audio", label: "Extract Audio", icon: Music },
      { to: "/volume", label: "Volume", icon: Volume2 },
      { to: "/replace-audio", label: "Replace Audio", icon: AudioLines },
    ],
  },
];

export default function Sidebar({ onItemClick, collapsed = false, onToggle }) {
  const { t } = useLanguage();
  return (
    <aside className={`sidebar${collapsed ? " sidebar--collapsed" : ""}`} aria-label={t("Main Navigation")}>
      <NavLink to="/" className="sidebar-brand" onClick={onItemClick}>
        <img className="sidebar-logo-image" src={avoriaLogo} alt="" />
        <span className="sidebar-brand-name">Avoria</span>
      </NavLink>

      {onToggle && (
        <button
          type="button"
          className="sidebar-toggle"
          onClick={onToggle}
          aria-label={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}
          aria-expanded={!collapsed}
          title={t(collapsed ? "Expand sidebar" : "Collapse sidebar")}
        >
          <ChevronRight size={17} />
        </button>
      )}

      <nav className="sidebar-nav">
        {navItems.map((section, idx) => (
          <div key={idx} className="sidebar-section">
            {section.category && (
              <h2 className="sidebar-section-title">{t(section.category)}</h2>
            )}
            <div className="sidebar-link-group">
              {section.items.map((item) => {
                const Icon = item.icon;
                return (
                  <NavLink
                    key={item.to}
                    to={item.to}
                    end={item.to === "/"}
                    className={({ isActive }) =>
                      isActive ? "sidebar-link active" : "sidebar-link"
                    }
                    onClick={onItemClick}
                    aria-label={t(item.label)}
                    title={collapsed ? t(item.label) : undefined}
                  >
                    <Icon size={18} />
                    <span>{t(item.label)}</span>
                  </NavLink>
                );
              })}
            </div>
          </div>
        ))}
      </nav>

      <div className="sidebar-credit">
        <span>{t("Powered by")}</span>
        <strong>Sinem ADALAN</strong>
      </div>
    </aside>
  );
}
