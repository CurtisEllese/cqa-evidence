import { useEffect, useRef, useState } from "react";
import type { CSSProperties, RefObject } from "react";
import {
  Headphones,
  Pause,
  Play,
  RotateCcw,
  Volume2,
  VolumeX,
} from "lucide-react";
import "./AudioPlayer.css";
import { loadAuthorizedAudio } from "../services/authMedia";

type Status = "loading" | "ready" | "buffering" | "stalled" | "error";

function timeLabel(seconds: number) {
  if (!Number.isFinite(seconds) || seconds < 0) return "—:—";
  const total = Math.floor(seconds);
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

export function AudioPlayer({
  src,
  audioRef,
  onLoadedMetadata,
  onSourceChange,
}: {
  src: string;
  audioRef: RefObject<HTMLAudioElement | null>;
  onLoadedMetadata: () => void;
  onSourceChange?: (url: string | null) => void;
}) {
  const [status, setStatus] = useState<Status>("loading");
  const [playing, setPlaying] = useState(false);
  const [duration, setDuration] = useState(0);
  const [position, setPosition] = useState(0);
  const [volume, setVolume] = useState(1);
  const [muted, setMuted] = useState(false);
  const [attempt, setAttempt] = useState(0);
  const [error, setError] = useState("");
  const [sourceReady, setSourceReady] = useState(false);
  const generation = useRef(0);
  const loadTimeout = useRef<number | null>(null);
  const retryPosition = useRef(0);
  const source = useRef(src);

  function clearLoadTimeout() {
    if (loadTimeout.current !== null) window.clearTimeout(loadTimeout.current);
    loadTimeout.current = null;
  }

  function armLoadTimeout() {
    clearLoadTimeout();
    loadTimeout.current = window.setTimeout(() => {
      loadTimeout.current = null;
      if (!audioRef.current?.error) setStatus("stalled");
    }, 20000);
  }

  function retry() {
    retryPosition.current = audioRef.current?.hasAttribute("src")
      ? audioRef.current.currentTime
      : position;
    setAttempt((value) => value + 1);
  }

  useEffect(() => {
    const player = audioRef.current;
    if (!player) return;
    generation.current += 1;
    if (source.current !== src) retryPosition.current = 0;
    source.current = src;
    setStatus("loading");
    setPlaying(false);
    setDuration(0);
    setPosition(retryPosition.current);
    setError("");
    setSourceReady(false);
    onSourceChange?.(null);
    const controller = new AbortController();
    let dispose: (() => void) | undefined;
    void loadAuthorizedAudio(src, controller.signal)
      .then((media) => {
        if (controller.signal.aborted) {
          media.dispose();
          return;
        }
        dispose = media.dispose;
        player.src = media.url;
        player.load();
        setSourceReady(true);
        onSourceChange?.(media.url);
      })
      .catch((reason: unknown) => {
        if (controller.signal.aborted) return;
        clearLoadTimeout();
        setStatus("error");
        setError(
          reason instanceof Error
            ? reason.message
            : "Не удалось получить доступ к записи. Повторите загрузку.",
        );
      });
    // Re-arm for every loading/buffering episode, including late network stalls.
    armLoadTimeout();
    return () => {
      generation.current += 1;
      controller.abort();
      clearLoadTimeout();
      player.pause();
      player.removeAttribute("src");
      player.load();
      dispose?.();
      onSourceChange?.(null);
    };
  }, [src, attempt, audioRef, onSourceChange]);

  function syncTime() {
    const player = audioRef.current;
    if (!player) return;
    setDuration(Number.isFinite(player.duration) ? player.duration : 0);
    setPosition(player.currentTime);
  }

  async function togglePlayback() {
    const player = audioRef.current;
    if (!player) return;
    if (!player.paused) {
      player.pause();
      return;
    }
    const currentGeneration = generation.current;
    try {
      await player.play();
    } catch (reason) {
      if (currentGeneration !== generation.current) return;
      if (reason instanceof DOMException && reason.name === "AbortError")
        return;
      clearLoadTimeout();
      setPlaying(false);
      setStatus("error");
      setError("Не удалось начать воспроизведение. Попробуйте ещё раз.");
    }
  }

  function seek(value: number) {
    const player = audioRef.current;
    if (!player || !duration) return;
    player.currentTime = Math.min(duration, Math.max(0, value));
    setPosition(player.currentTime);
  }

  const statusText =
    status === "error"
      ? error
      : status === "loading"
        ? "Загружаем запись…"
        : status === "buffering"
          ? "Буферизация…"
          : status === "stalled"
            ? "Загрузка задерживается. Можно повторить попытку."
            : playing
              ? "Воспроизведение"
              : "Запись готова";
  const progress = duration ? Math.min(100, (position / duration) * 100) : 0;

  return (
    <div
      className="call-player"
      role="group"
      aria-label="Плеер записи разговора"
    >
      <audio
        ref={audioRef}
        preload="auto"
        hidden
        onLoadedMetadata={() => {
          const player = audioRef.current;
          if (player && retryPosition.current > 0) {
            player.currentTime = Number.isFinite(player.duration)
              ? Math.min(retryPosition.current, player.duration)
              : retryPosition.current;
            retryPosition.current = 0;
          }
          onLoadedMetadata();
          syncTime();
        }}
        onLoadStart={() => {
          if (!audioRef.current?.hasAttribute("src")) return;
          setStatus("loading");
          armLoadTimeout();
        }}
        onDurationChange={syncTime}
        onTimeUpdate={syncTime}
        onCanPlay={() => {
          clearLoadTimeout();
          setStatus("ready");
        }}
        onPlay={() => {
          clearLoadTimeout();
          setPlaying(true);
        }}
        onPlaying={() => {
          clearLoadTimeout();
          setPlaying(true);
          setStatus("ready");
        }}
        onPause={() => {
          if (!audioRef.current?.hasAttribute("src")) return;
          clearLoadTimeout();
          setPlaying(false);
          if (!audioRef.current?.error)
            setStatus(
              (audioRef.current?.readyState ?? 0) >= 3 ? "ready" : "stalled",
            );
        }}
        onEnded={() => {
          clearLoadTimeout();
          setPlaying(false);
          syncTime();
        }}
        onWaiting={() => {
          setStatus("buffering");
          armLoadTimeout();
        }}
        onStalled={() => {
          if ((audioRef.current?.readyState ?? 0) < 3) setStatus("stalled");
        }}
        onError={() => {
          clearLoadTimeout();
          setPlaying(false);
          setStatus("error");
          setError(
            "Не удалось загрузить запись. Проверьте соединение и повторите попытку.",
          );
        }}
        onVolumeChange={() => {
          const player = audioRef.current;
          if (player) {
            setVolume(player.volume);
            setMuted(player.muted);
          }
        }}
      />
      <div className="call-player-heading">
        <Headphones size={18} aria-hidden="true" />
        <strong>Запись разговора</strong>
      </div>
      <div className="call-player-controls">
        <button
          type="button"
          className="call-player-play"
          onClick={() => void togglePlayback()}
          disabled={status === "error" || !sourceReady}
          aria-label={playing ? "Приостановить запись" : "Воспроизвести запись"}
        >
          {playing ? (
            <Pause size={21} aria-hidden="true" />
          ) : (
            <Play size={21} aria-hidden="true" />
          )}
        </button>
        <div className="call-player-timeline">
          <input
            type="range"
            min={0}
            max={duration || 1}
            step={0.1}
            value={Math.min(position, duration)}
            disabled={!duration || status === "error"}
            aria-label="Позиция записи"
            aria-valuetext={`${timeLabel(position)} из ${duration ? timeLabel(duration) : "длительность неизвестна"}`}
            style={{ "--audio-progress": `${progress}%` } as CSSProperties}
            onChange={(event) => seek(Number(event.target.value))}
          />
          <div className="call-player-times">
            <span>{timeLabel(position)}</span>
            <span>{duration ? timeLabel(duration) : "—:—"}</span>
          </div>
        </div>
      </div>
      <div className="call-player-footer">
        <p className="call-player-status" role="status" aria-live="polite">
          {statusText}
        </p>
        <div className="call-player-volume">
          <button
            type="button"
            aria-label={
              muted || volume === 0 ? "Включить звук" : "Выключить звук"
            }
            onClick={() => {
              const player = audioRef.current;
              if (!player) return;
              if (player.muted || player.volume === 0) {
                player.muted = false;
                if (!player.volume) player.volume = 1;
              } else player.muted = true;
            }}
          >
            {muted || volume === 0 ? (
              <VolumeX size={18} aria-hidden="true" />
            ) : (
              <Volume2 size={18} aria-hidden="true" />
            )}
          </button>
          <input
            type="range"
            min={0}
            max={1}
            step={0.05}
            value={muted ? 0 : volume}
            aria-label="Громкость записи"
            aria-valuetext={`${Math.round((muted ? 0 : volume) * 100)}%`}
            onChange={(event) => {
              const player = audioRef.current;
              if (player) {
                player.volume = Number(event.target.value);
                player.muted = false;
              }
            }}
          />
        </div>
      </div>
      {(status === "error" || status === "stalled") && (
        <button type="button" className="call-player-retry" onClick={retry}>
          <RotateCcw size={16} aria-hidden="true" />
          Повторить загрузку
        </button>
      )}
    </div>
  );
}
