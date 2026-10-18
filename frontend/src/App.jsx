// App：顶部导航 + 三页面路由（规格第七章，第一版只读）
import { NavLink, Route, Routes } from 'react-router-dom'
import Watchlist from './pages/Watchlist.jsx'
import Signals from './pages/Signals.jsx'
import Timeline from './pages/Timeline.jsx'

export default function App() {
  return (
    <div className="app">
      <header className="topbar">
        <span className="brand">A股公告事件跟踪系统</span>
        <nav className="nav">
          <NavLink to="/" end>Watchlist</NavLink>
          <NavLink to="/signals">Signals</NavLink>
          <NavLink to="/timeline">Timeline</NavLink>
        </nav>
      </header>
      <main className="content">
        <Routes>
          <Route path="/" element={<Watchlist />} />
          <Route path="/signals" element={<Signals />} />
          <Route path="/timeline" element={<Timeline />} />
          <Route path="*" element={<Watchlist />} />
        </Routes>
      </main>
    </div>
  )
}
