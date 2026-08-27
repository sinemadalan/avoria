import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu, Sparkles } from "lucide-react";
import Sidebar from "./Sidebar.jsx";
import MobileNav from "./MobileNav.jsx";

export default function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);

  return (
    <div className="app-shell">
      {/* Desktop Sidebar */}
      <Sidebar />

      {/* Mobile Drawer Navigation */}
      <MobileNav
        isOpen={mobileNavOpen}
        onClose={() => setMobileNavOpen(false)}
      />

      {/* Main Content Area */}
      <div className="app-main">
        {/* Mobile Header with Hamburger */}
        <header className="mobile-header">
          <div style={{ display: "flex", alignItems: "center", gap: "0.75rem" }}>
            <div className="sidebar-logo-icon" style={{ width: 28, height: 28 }}>
              <Sparkles size={16} />
            </div>
            <span className="sidebar-brand-name" style={{ fontSize: "1.05rem" }}>
              avoria
            </span>
          </div>
          <button
            className="mobile-menu-btn"
            onClick={() => setMobileNavOpen(true)}
            aria-label="Open menu"
          >
            <Menu size={20} />
          </button>
        </header>

        {/* Dynamic Route View */}
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
