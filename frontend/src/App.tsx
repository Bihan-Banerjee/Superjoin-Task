import { Navigate, Route, Routes } from "react-router-dom";

import Shell from "./components/Shell";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/documents" replace />} />
      </Route>
    </Routes>
  );
}
