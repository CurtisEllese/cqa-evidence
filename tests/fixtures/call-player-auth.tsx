import { StrictMode, useRef, useState } from "react";
import { createRoot } from "react-dom/client";
import { AudioPlayer } from "../../src/components/AudioPlayer";

const controls = window as typeof window & {
  setAudioSource?: (src: string) => void;
  unmountPlayer?: () => void;
  pendingSeek?: number | null;
};

function Fixture() {
  const audioRef = useRef<HTMLAudioElement>(null);
  const [src, setSrc] = useState("/api/conversations/a/audio");
  const [resolved, setResolved] = useState<string | null>(null);
  controls.setAudioSource = setSrc;
  return (
    <>
      <AudioPlayer
        src={src}
        audioRef={audioRef}
        onSourceChange={setResolved}
        onLoadedMetadata={() => {
          if (controls.pendingSeek != null && audioRef.current) {
            audioRef.current.currentTime = controls.pendingSeek;
            controls.pendingSeek = null;
          }
        }}
      />
      {resolved && <a href={resolved}>Открыть запись</a>}
    </>
  );
}
const root = createRoot(document.getElementById("root")!);
controls.unmountPlayer = () => root.unmount();
root.render(
  <StrictMode>
    <Fixture />
  </StrictMode>,
);
