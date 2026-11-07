"""Local PCM measurements, not voice quality scores or speech recognition."""
from __future__ import annotations

from array import array
from hashlib import sha256
import math
from pathlib import Path
import sys
import wave

MAX_AUDIO_BYTES = 64 * 1024 * 1024
MAX_DURATION_SECONDS = 30 * 60
MODEL_CAPABILITY = {
    "model": "gpt-6-luna", "audioInputSupported": False,
    "checkedAt": "2026-11-08",
    "source": "https://developers.openai.com/api/docs/models/gpt-6-luna",
    "diction": {"status": "not_assessed", "score": None,
                "reason": "GPT-6 Luna has no audio input; transcript cannot establish pronunciation."},
    "intonation": {"status": "not_assessed", "score": None,
                   "reason": "No authorized audio-capable model or expert labels."},
}


def inspect_pcm_wav(path: str | Path, *, quiet_threshold_dbfs: float = -40.0) -> dict:
    """Measure 16-bit PCM, mono/stereo. A quiet frame is NOT a speech pause."""
    if not math.isfinite(quiet_threshold_dbfs) or not -90 <= quiet_threshold_dbfs <= -10:
        raise ValueError("quiet threshold must be within -90..-10 dBFS")
    path = Path(path)
    with path.open("rb") as stream:
        raw = stream.read(MAX_AUDIO_BYTES + 1)
    if len(raw) > MAX_AUDIO_BYTES:
        raise ValueError("audio exceeds local 64 MiB limit")
    import io
    try:
        with wave.open(io.BytesIO(raw), "rb") as audio:
            channels, rate, frames = audio.getnchannels(), audio.getframerate(), audio.getnframes()
            if audio.getsampwidth() != 2 or audio.getcomptype() != "NONE" or channels not in (1, 2):
                raise ValueError("requires uncompressed 16-bit PCM WAV, mono or stereo")
            if not 8000 <= rate <= 96000 or not 0 < frames / rate <= MAX_DURATION_SECONDS:
                raise ValueError("invalid sample rate or duration")
            samples = array("h", audio.readframes(frames))
    except (wave.Error, EOFError) as exc:
        raise ValueError("invalid PCM WAV") from exc
    if sys.byteorder != "little":
        samples.byteswap()
    if len(samples) != frames * channels:
        raise ValueError("truncated PCM data")
    window = max(1, round(rate * 0.02))  # 20 ms, including weighted final partial frame
    reports = []
    for channel in range(channels):
        values = samples[channel::channels]
        rms = math.sqrt(sum(x * x for x in values) / frames) / 32768
        quiet_frames = 0
        for start in range(0, frames, window):
            segment = values[start:start + window]
            local_rms = math.sqrt(sum(x * x for x in segment) / len(segment)) / 32768
            if local_rms <= 10 ** (quiet_threshold_dbfs / 20):
                quiet_frames += len(segment)
        reports.append({"channelIndex": channel, "rmsDbfs": 20 * math.log10(rms) if rms else None,
                        "silent": rms == 0, "peakFraction": max(abs(x) for x in values) / 32768,
                        "fullScaleSampleRatio": sum(x in (-32768, 32767) for x in values) / frames,
                        "quietFrameDurationSeconds": quiet_frames / rate,
                        "quietFrameRatio": quiet_frames / frames})
    return {"methodVersion": "pcm-signal-1", "audioSha256": sha256(raw).hexdigest(),
            "durationSeconds": frames / rate, "sampleRate": rate, "channels": reports,
            "quietThresholdDbfs": quiet_threshold_dbfs, "windowMs": 20,
            "status": "signal_measurements_only", "voiceQualityScore": None,
            "limitations": ["Energy threshold is not VAD; music/noise can be loud and speech can be quiet.",
                            "No speaker identification, diction, intonation, or quality penalty is inferred.",
                            "Full-scale samples indicate possible clipping, not proof of distortion."],
            "modelCapability": MODEL_CAPABILITY}


def measured_tempo(*, audio_sha256: str, duration_seconds: float, segments: list[dict],
                   reviewer: str, language: str) -> dict:
    """Words/minute over human-verified, non-overlapping operator turns including pauses.

    Each segment must bind the same audio hash and provide start/end seconds,
    wordCount, speaker='operator', verified=True. This is not an articulation rate.
    """
    if language not in ("RU", "KZ", "EN") or not reviewer.strip():
        raise ValueError("language and human verifier are required")
    if len(audio_sha256) != 64 or any(c not in "0123456789abcdef" for c in audio_sha256):
        raise ValueError("invalid audio hash")
    if not math.isfinite(duration_seconds) or duration_seconds <= 0:
        raise ValueError("invalid duration")
    if not segments:
        return {"status": "not_assessed", "wordsPerMinute": None,
                "reason": "No human-verified operator intervals and word counts"}
    previous_end, elapsed, words = 0.0, 0.0, 0
    for segment in segments:
        start, end, count = segment["startSeconds"], segment["endSeconds"], segment["wordCount"]
        if any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end)):
            raise ValueError("invalid timing")
        if not previous_end <= start < end <= duration_seconds:
            raise ValueError("overlapping, unordered or out-of-bounds operator intervals")
        if type(count) is not int or count < 0:
            raise ValueError("wordCount must be a nonnegative integer")
        if segment.get("audioSha256") != audio_sha256 or segment.get("verified") is not True or segment.get("speaker") != "operator":
            raise ValueError("word counts and speaker intervals must be verified for this exact audio")
        elapsed += end - start
        words += count
        previous_end = end
    return {"status": "measured_from_human_verified_timing", "methodVersion": "turn-tempo-1",
            "audioSha256": audio_sha256, "language": language, "verifiedBy": reviewer,
            "wordCount": words, "operatorTurnSeconds": elapsed, "wordsPerMinute": words * 60 / elapsed,
            "qualityScore": None, "dictionScore": None,
            "limitation": "Includes pauses inside annotated turns; word segmentation differs across languages. No fast/slow judgment without an approved rubric."}
