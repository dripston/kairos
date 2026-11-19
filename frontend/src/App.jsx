import { Navigate, Route, Routes } from 'react-router-dom'
import Shell from './components/Shell'
import Campaigns from './pages/Campaigns'
import ControlRoom from './pages/ControlRoom'
import Crm from './pages/Crm'
import CrmAccount from './pages/CrmAccount'
import Learning from './pages/Learning'

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/control" replace />} />
        <Route path="/campaigns" element={<Campaigns />} />
        <Route path="/control" element={<ControlRoom />} />
        <Route path="/crm" element={<Crm />} />
        <Route path="/crm/:id" element={<CrmAccount />} />
        <Route path="/learning" element={<Learning />} />
        <Route path="*" element={<Navigate to="/control" replace />} />
      </Route>
    </Routes>
  )
}
