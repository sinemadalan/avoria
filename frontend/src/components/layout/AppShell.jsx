import { useState } from "react";
import { Outlet } from "react-router-dom";
import { Menu } from "lucide-react";
import Sidebar from "./Sidebar.jsx";
import MobileNav from "./MobileNav.jsx";
import LanguageSwitcher from "./LanguageSwitcher.jsx";
import avoriaLogo from "../../styles/avoria_logo.png";
import { useLanguage } from "../../context/LanguageContext.jsx";

export default function AppShell() {
  const { t } = useLanguage();
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
        <div className="desktop-language-switcher">
          <LanguageSwitcher />
        </div>
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
          <div className="mobile-header-actions">
            <LanguageSwitcher />
            <button
              className="mobile-menu-btn"
              onClick={() => setMobileNavOpen(true)}
              aria-label={t("Open menu")}
            >
              <Menu size={20} />
            </button>
          </div>
        </header>

        {/* Dynamic Route View */}
        <main className="app-content">
          <Outlet />
        </main>
      </div>
    </div>
  );
}
