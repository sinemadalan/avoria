import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu } from "lucide-react";
import Sidebar from "./Sidebar.jsx";
import MobileNav from "./MobileNav.jsx";
import avoriaLogo from "../../styles/avoria_logo.png";

export default function AppShell() {
  const [mobileNavOpen, setMobileNavOpen] = useState(false);
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false);

  return (
    <div className="app-shell">
      {/* Desktop Sidebar */}
      <Sidebar
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((current) => !current)}
      />

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
            <img
              className="sidebar-logo-image sidebar-logo-image--mobile"
              src={avoriaLogo}
              alt=""
            />
            <span className="sidebar-brand-name">Avoria</span>
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
