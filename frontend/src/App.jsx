import { AnimatePresence, motion } from "framer-motion";
import { BarChart3, BookOpen, Briefcase, ClipboardList, LogOut, Menu, Moon, PanelLeftClose, PanelLeftOpen, Sparkles, Sun, Upload, User } from "lucide-react";
import { useEffect, useState } from "react";
import { NavLink, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { IconButton, Select, Spinner } from "./components/ui";
import { useAuth } from "./hooks/useAuth";
import { TrackProvider, useTrack } from "./hooks/useTrack";
import Jobs from "./pages/Jobs";
import Knowledge from "./pages/Knowledge";
import Login from "./pages/Login";
import Onboarding from "./pages/Onboarding";
import Prep from "./pages/Prep";
import Profile from "./pages/Profile";
import Reports from "./pages/Reports";
import Tracker from "./pages/Tracker";
import Tracks from "./pages/Tracks";

const NAV = [
  ["/onboarding", "Onboarding", Upload],
  ["/jobs", "Jobs", Briefcase],
  ["/prep", "Prep", Sparkles],
  ["/reports", "Reports", BarChart3],
  ["/tracker", "Tracker", ClipboardList],
  ["/knowledge", "Knowledge", BookOpen],
  ["/profile", "Profile", User],
];

function useTheme() {
  const [theme, setTheme] = useState(() => {
    try {
      return localStorage.getItem("cp_theme") || "dark";
    } catch {
      return "dark";
    }
  });
  useEffect(() => {
    document.documentElement.dataset.theme = theme;
    try {
      localStorage.setItem("cp_theme", theme);
    } catch {
      /* ignore */
    }
  }, [theme]);
  return [theme, () => setTheme(theme === "dark" ? "light" : "dark")];
}

function Shell() {
  const { user, logout } = useAuth();
  const { tracks, track, select } = useTrack();
  const [theme, toggleTheme] = useTheme();
  const [collapsed, setCollapsed] = useState(false);
  const [drawer, setDrawer] = useState(false);
  const loc = useLocation();
  useEffect(() => setDrawer(false), [loc.pathname]);

  const nav = (
    <nav aria-label="Main" className="flex flex-col gap-1">
      {NAV.map(([to, label, Icon]) => (
        <NavLink
          key={to}
          to={to}
          className={({ isActive }) =>
            `flex min-h-[44px] items-center gap-3 rounded-md px-3 transition ${isActive ? "bg-[image:var(--accent-grad)] text-white shadow-glow" : "text-ink-soft hover:bg-[var(--glass-2)] hover:text-ink"}`
          }
        >
          <Icon className="h-5 w-5 shrink-0" aria-hidden />
          {(!collapsed || drawer) && <span>{label}</span>}
        </NavLink>
      ))}
    </nav>
  );

  return (
    <div className="flex min-h-screen">
      <aside className={`glass-1 sticky top-0 m-3 hidden h-[calc(100vh-24px)] shrink-0 flex-col justify-between rounded-lg p-3 md:flex ${collapsed ? "w-[72px]" : "w-60"}`}>
        <div className="space-y-6">
          <div className="flex items-center justify-between px-2 pt-2">
            {!collapsed && <span className="bg-[image:var(--accent-grad)] bg-clip-text text-lg font-semibold text-transparent">CareerPilot</span>}
            <IconButton label={collapsed ? "Expand sidebar" : "Collapse sidebar"} icon={collapsed ? PanelLeftOpen : PanelLeftClose} onClick={() => setCollapsed(!collapsed)} />
          </div>
          {nav}
        </div>
        <div className="flex flex-col gap-1">
          <IconButton label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} icon={theme === "dark" ? Sun : Moon} onClick={toggleTheme} />
          <IconButton label="Log out" icon={LogOut} onClick={logout} />
        </div>
      </aside>

      <div className="flex min-w-0 flex-1 flex-col">
        <header className="glass-1 sticky top-3 z-20 mx-3 mt-3 flex items-center gap-3 rounded-lg px-4 py-2 md:mx-0 md:mr-3">
          <IconButton label="Open menu" icon={Menu} className="md:hidden" onClick={() => setDrawer(true)} />
          <span className="font-semibold md:hidden">CareerPilot</span>
          <div className="ml-auto flex items-center gap-3">
            {tracks.length > 0 && (
              <Select aria-label="Select track" value={track?.id || ""} onChange={(e) => select(e.target.value)} className="!min-h-[40px] max-w-[220px]">
                {tracks.map((t) => (
                  <option key={t.id} value={t.id}>{t.name}</option>
                ))}
              </Select>
            )}
            <NavLink to="/tracks" className="text-sm text-ink-soft hover:text-ink">Manage tracks</NavLink>
            <span className="hidden text-sm text-ink-soft lg:inline">{user?.email}</span>
            <IconButton label="Toggle theme" icon={theme === "dark" ? Sun : Moon} className="md:hidden" onClick={toggleTheme} />
          </div>
        </header>

        <AnimatePresence>
          {drawer && (
            <>
              <motion.div className="fixed inset-0 z-30 bg-black/60 md:hidden" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => setDrawer(false)} />
              <motion.aside className="glass-solid fixed left-0 top-0 z-40 h-full w-64 rounded-l-none p-4 md:hidden" initial={{ x: -280 }} animate={{ x: 0 }} exit={{ x: -280 }} transition={{ duration: 0.2 }}>
                <div className="mb-4 flex items-center justify-between">
                  <span className="font-semibold">CareerPilot</span>
                </div>
                {nav}
                <button onClick={logout} className="mt-6 flex min-h-[44px] items-center gap-3 px-3 text-ink-soft"><LogOut className="h-5 w-5" aria-hidden />Log out</button>
              </motion.aside>
            </>
          )}
        </AnimatePresence>

        <main className="mx-auto w-full max-w-[1200px] flex-1 p-4 md:p-6">
          <AnimatePresence mode="wait">
            <motion.div key={loc.pathname} initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.2, ease: "easeOut" }}>
              <Routes location={loc}>
                <Route path="/tracks" element={<Tracks />} />
                <Route path="/onboarding" element={<Onboarding />} />
                <Route path="/jobs" element={<Jobs />} />
                <Route path="/prep" element={<Prep />} />
                <Route path="/reports" element={<Reports />} />
                <Route path="/tracker" element={<Tracker />} />
                <Route path="/knowledge" element={<Knowledge />} />
                <Route path="/profile" element={<Profile />} />
                <Route path="*" element={<Navigate to={tracks.length ? "/jobs" : "/tracks"} replace />} />
              </Routes>
            </motion.div>
          </AnimatePresence>
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const { user, ready } = useAuth();
  return (
    <>
      <div className="backdrop" aria-hidden>
        <div className="orb a" />
        <div className="orb b" />
        <div className="orb c" />
      </div>
      {!ready ? (
        <div className="flex min-h-screen items-center justify-center"><Spinner label="Loading CareerPilot" /></div>
      ) : !user ? (
        <Login />
      ) : (
        <TrackProvider>
          <Shell />
        </TrackProvider>
      )}
    </>
  );
}
