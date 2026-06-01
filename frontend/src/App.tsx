import { Dashboard } from './pages/Dashboard';
import { MobileDashboard } from './pages/MobileDashboard';

function App() {
  const isMobile = window.location.pathname.startsWith('/mobile');
  return isMobile ? <MobileDashboard /> : <Dashboard />;
}

export default App;
