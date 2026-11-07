from array import array
import math
import wave

import pytest

from evaluator.audio_metrics import inspect_pcm_wav, measured_tempo


def wav(tmp_path, samples, channels=1, width=2):
    path = tmp_path / "synthetic.wav"
    with wave.open(str(path), "wb") as file:
        file.setnchannels(channels)
        file.setsampwidth(width)
        file.setframerate(8000)
        file.writeframes(array("h", samples).tobytes() if width == 2 else bytes(samples))
    return path


def test_silence_is_signal_not_good_diction(tmp_path):
    report = inspect_pcm_wav(wav(tmp_path, [0] * 8000))
    assert report["durationSeconds"] == 1
    channel = report["channels"][0]
    assert channel["rmsDbfs"] is None
    assert channel["quietFrameRatio"] == 1
    assert report["voiceQualityScore"] is None
    assert report["modelCapability"]["diction"]["score"] is None
    assert report["modelCapability"]["audioInputSupported"] is False


def test_tone_known_rms_and_stereo_no_phase_cancellation(tmp_path):
    tone = [int(16384 * math.sin(2 * math.pi * 200 * i / 8000)) for i in range(8000)]
    samples = [sample for v in tone for sample in (v, -v)]
    report = inspect_pcm_wav(wav(tmp_path, samples, channels=2))
    for channel in report["channels"]:
        assert channel["rmsDbfs"] == pytest.approx(-9.031, abs=0.002)
        assert channel["quietFrameRatio"] == 0
        assert channel["fullScaleSampleRatio"] == 0


def test_full_scale_and_final_partial_window(tmp_path):
    report = inspect_pcm_wav(wav(tmp_path, [32767] * 160 + [0] * 80))
    assert report["channels"][0]["fullScaleSampleRatio"] == pytest.approx(2 / 3)
    assert report["channels"][0]["quietFrameRatio"] == pytest.approx(1 / 3)


def test_reject_unsupported_truncated_empty_and_invalid_threshold(tmp_path):
    for path in (wav(tmp_path, [0] * 100, width=1),):
        with pytest.raises(ValueError):
            inspect_pcm_wav(path)
    path = wav(tmp_path, [0] * 8000)
    path.write_bytes(path.read_bytes()[:-20])
    with pytest.raises(ValueError, match="truncated"):
        inspect_pcm_wav(path)
    with pytest.raises(ValueError):
        inspect_pcm_wav(wav(tmp_path, []))
    with pytest.raises(ValueError):
        inspect_pcm_wav(path, quiet_threshold_dbfs=float("nan"))


def tempo_args():
    return dict(audio_sha256="a" * 64, duration_seconds=60, reviewer="human-test-only", language="KZ",
                segments=[dict(audioSha256="a" * 64, startSeconds=10, endSeconds=40, wordCount=60,
                               speaker="operator", verified=True)])


def test_tempo_requires_actual_verified_audio_intervals():
    args = tempo_args()
    result = measured_tempo(**args)
    assert result["wordsPerMinute"] == 120
    assert result["dictionScore"] is None and result["qualityScore"] is None
    args["segments"] = []
    assert measured_tempo(**args)["wordsPerMinute"] is None


@pytest.mark.parametrize("patch", [dict(verified=False), dict(audioSha256="b" * 64),
    dict(speaker="client"), dict(startSeconds=-1), dict(endSeconds=61), dict(startSeconds=float("nan")),
    dict(wordCount=True), dict(wordCount=-1), dict(endSeconds=10)])
def test_tempo_rejects_bad_evidence(patch):
    args = tempo_args()
    args["segments"][0].update(patch)
    with pytest.raises(ValueError):
        measured_tempo(**args)


def test_tempo_rejects_double_counted_overlap():
    args = tempo_args()
    args["segments"].append(dict(args["segments"][0]))
    with pytest.raises(ValueError, match="overlapping"):
        measured_tempo(**args)
