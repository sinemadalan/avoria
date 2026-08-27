import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { MediaProvider } from "./context/MediaContext.jsx";
import AppShell from "./components/layout/AppShell.jsx";

// Feature Page Components
import HomePage from "./pages/HomePage.jsx";
import ConvertPage from "./pages/ConvertPage.jsx";
import CompressPage from "./pages/CompressPage.jsx";
import TrimPage from "./pages/TrimPage.jsx";
import SpeedPage from "./pages/SpeedPage.jsx";
import CropFitPage from "./pages/CropFitPage.jsx";
import MergePage from "./pages/MergePage.jsx";
import ExtractAudioPage from "./pages/ExtractAudioPage.jsx";
import MutePage from "./pages/MutePage.jsx";
import VolumePage from "./pages/VolumePage.jsx";
import ReplaceAudioPage from "./pages/ReplaceAudioPage.jsx";

export default function App() {
  return (
    <MediaProvider>
      <BrowserRouter>
        <Routes>
          <Route path="/" element={<AppShell />}>
            <Route index element={<HomePage />} />
            <Route path="convert" element={<ConvertPage />} />
            <Route path="compress" element={<CompressPage />} />
            <Route path="trim" element={<TrimPage />} />
            <Route path="speed" element={<SpeedPage />} />
            <Route path="crop-fit" element={<CropFitPage />} />
            <Route path="merge" element={<MergePage />} />
            <Route path="extract-audio" element={<ExtractAudioPage />} />
            <Route path="mute" element={<MutePage />} />
            <Route path="volume" element={<VolumePage />} />
            <Route path="replace-audio" element={<ReplaceAudioPage />} />
            <Route path="*" element={<Navigate to="/" replace />} />
          </Route>
        </Routes>
      </BrowserRouter>
    </MediaProvider>
  );
}
