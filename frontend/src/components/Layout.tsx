import { NavLink, Outlet, Link } from "react-router-dom";

const NAV = [
  { to: "/", label: "提问", end: true },
  { to: "/stats", label: "运营指标" },
  { to: "/samples", label: "示例问题" },
];

export function Layout() {
  return (
    <div className="flex min-h-full flex-col">
      <header className="border-b border-slate-200 bg-white">
        <div className="mx-auto flex max-w-6xl items-center justify-between px-6 py-4">
          <Link to="/" className="flex items-center gap-2">
            <span className="text-xl">🛒</span>
            <span className="font-semibold text-slate-900">电商问数助手</span>
            <span className="ml-2 rounded bg-slate-100 px-1.5 py-0.5 text-xs text-slate-500">
              v1.0
            </span>
          </Link>
          <nav className="flex gap-1">
            {NAV.map((n) => (
              <NavLink
                key={n.to}
                to={n.to}
                end={n.end}
                className={({ isActive }) =>
                  "rounded-md px-3 py-1.5 text-sm transition " +
                  (isActive
                    ? "bg-slate-900 text-white"
                    : "text-slate-600 hover:bg-slate-100")
                }
              >
                {n.label}
              </NavLink>
            ))}
          </nav>
        </div>
      </header>
      <main className="mx-auto w-full max-w-6xl flex-1 px-6 py-6">
        <Outlet />
      </main>
      <footer className="border-t border-slate-200 bg-white py-3 text-center text-xs text-slate-400">
        本地版 · FastAPI + LangGraph · 不依赖 Docker
      </footer>
    </div>
  );
}