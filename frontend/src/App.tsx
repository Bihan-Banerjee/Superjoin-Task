import { Navigate, Route, Routes } from "react-router-dom";

import Shell from "./components/Shell";
import Cases from "./routes/Cases";
import Configure from "./routes/Configure";
import Documents from "./routes/Documents";
import Evaluation from "./routes/Evaluation";
import Facts from "./routes/Facts";
import Landing from "./routes/Landing";
import Registry from "./routes/Registry";
import Relations from "./routes/Relations";

export default function App() {
  return (
    <Routes>
      {/* The landing page sits outside the shell: it has its own chrome, and the sidebar
          would frame it as one more workbench view rather than as the way in. */}
      <Route path="/" element={<Landing />} />
      <Route element={<Shell />}>
        <Route path="documents" element={<Documents />} />
        <Route path="facts" element={<Facts />} />
        <Route path="relations" element={<Relations />} />
        <Route path="cases" element={<Cases />} />
        <Route path="registry" element={<Registry />} />
        <Route path="evaluation" element={<Evaluation />} />
        <Route path="configure" element={<Configure />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Route>
    </Routes>
  );
}
