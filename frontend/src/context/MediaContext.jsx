import { createContext, useContext, useState, useEffect, useCallback } from "react";

const MediaContext = createContext(null);

export function MediaProvider({ children }) {
  const [currentMedia, setCurrentMediaState] = useState(null);

  // Helper to set media and clean up previous object URL if any
  const setCurrentMedia = useCallback((media) => {
    setCurrentMediaState((prev) => {
      if (prev?.previewUrl && prev.previewUrl !== media?.previewUrl && prev.previewUrl.startsWith("blob:")) {
        URL.revokeObjectURL(prev.previewUrl);
      }
      return media;
    });
  }, []);

  const clearCurrentMedia = useCallback(() => {
    setCurrentMediaState((prev) => {
      if (prev?.previewUrl && prev.previewUrl.startsWith("blob:")) {
        URL.revokeObjectURL(prev.previewUrl);
      }
      return null;
    });
  }, []);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (currentMedia?.previewUrl && currentMedia.previewUrl.startsWith("blob:")) {
        URL.revokeObjectURL(currentMedia.previewUrl);
      }
    };
  }, [currentMedia]);

  return (
    <MediaContext.Provider
      value={{
        currentMedia,
        setCurrentMedia,
        clearCurrentMedia,
      }}
    >
      {children}
    </MediaContext.Provider>
  );
}

export function useMediaContext() {
  const context = useContext(MediaContext);
  if (!context) {
    throw new Error("useMediaContext must be used within a MediaProvider");
  }
  return context;
}
