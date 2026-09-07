import { Outlet } from "react-router-dom";

export default function Shell() {
  return (
    <div className="app">
      <nav className="sidebar">
        <div className="sidebar__brand">
          <span className="sidebar__title">Fact Knowledge Layer</span>
          <span className="sidebar__subtitle">Grounded facts across documents</span>
        </div>
      </nav>
      <div className="main">
        <div className="main__body">
          <Outlet />
        </div>
      </div>
    </div>
  );
}
