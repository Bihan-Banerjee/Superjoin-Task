import { Navigate, Route, Routes } from "react-router-dom";

import Shell from "./components/Shell";
import Cases from "./routes/Cases";
import Documents from "./routes/Documents";
import Evaluation from "./routes/Evaluation";
import Facts from "./routes/Facts";
import Registry from "./routes/Registry";
import Relations from "./routes/Relations";

export default function App() {
  return (
    <Routes>
      <Route element={<Shell />}>
        <Route index element={<Navigate to="/documents" replace />} />
        <Route path="documents" element={<Documents />} />
        <Route path="facts" element={<Facts />} />
        <Route path="relations" element={<Relations />} />
        <Route path="cases" element={<Cases />} />
        <Route path="registry" element={<Registry />} />
        <Route path="evaluation" element={<Evaluation />} />
        <Route path="*" element={<Navigate to="/documents" replace />} />
      </Route>
    </Routes>
  );
}
