import { BrowserRouter, Route, Routes } from "react-router-dom";
import { Layout } from "./components/Layout";
import { HomePage } from "./pages/Home";
import { StatsPage } from "./pages/Stats";
import { SamplesPage } from "./pages/Samples";
import { HistoryPage } from "./pages/History";

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Layout />}>
          <Route index element={<HomePage />} />
          <Route path="stats" element={<StatsPage />} />
          <Route path="history" element={<HistoryPage />} />
          <Route path="samples" element={<SamplesPage />} />
          <Route path="*" element={<HomePage />} />
        </Route>
      </Routes>
    </BrowserRouter>
  );
}