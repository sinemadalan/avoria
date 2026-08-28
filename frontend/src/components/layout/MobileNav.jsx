import { NavLink } from "react-router-dom";
import { X } from "lucide-react";
import { navItems } from "./Sidebar.jsx";
import avoriaLogo from "../../styles/avoria_logo.png";

export default function MobileNav({ isOpen, onClose }) {
  if (!isOpen) return null;

  return (
    <>
      <div
        className="mobile-drawer-overlay"
        onClick={onClose}
        aria-hidden="true"
      />
      <aside
        className={`mobile-drawer ${isOpen ? "open" : ""}`}
        aria-label="Mobile Navigation"
      >
        <div
          style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "1.25rem",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <NavLink
            to="/"
            className="sidebar-brand"
            style={{ padding: 0 }}
            onClick={onClose}
          >
            <img className="sidebar-logo-image" src={avoriaLogo} alt="" />
            <span className="sidebar-brand-name">Avoria</span>
          </NavLink>
          <button
            onClick={onClose}
            aria-label="Close navigation"
            style={{
              color: "var(--text-secondary)",
              padding: "0.5rem",
              borderRadius: "var(--radius-sm)",
            }}
          >
            <X size={20} />
          </button>
        </div>

        <nav className="sidebar-nav">
          {navItems.map((section, idx) => (
            <div key={idx} className="sidebar-section">
              {section.category && (
                <h2 className="sidebar-section-title">{section.category}</h2>
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
                      onClick={onClose}
                    >
                      <Icon size={18} />
                      <span>{item.label}</span>
                    </NavLink>
                  );
                })}
              </div>
            </div>
          ))}
        </nav>
      </aside>
    </>
  );
}
