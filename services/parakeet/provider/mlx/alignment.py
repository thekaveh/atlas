"""Serialize the pinned parakeet-mlx aligned transcription result."""

from __future__ import annotations

from typing import Any


def _get(value: Any, name: str, default: Any = None) -> Any:
    if isinstance(value, dict):
        return value.get(name, default)
    return getattr(value, name, default)


def result_text(result: Any) -> str:
    text = _get(result, "text")
    return text if isinstance(text, str) else str(result)


def alignment_payload(result: Any) -> dict[str, Any]:
    segments: list[dict[str, Any]] = []
    words: list[dict[str, Any]] = []
    for index, sentence in enumerate(_get(result, "sentences", []) or []):
        start = _get(sentence, "start")
        end = _get(sentence, "end")
        segments.append(
            {
                "id": index,
                "text": _get(sentence, "text", ""),
                "start": start,
                "end": end,
                "duration": _get(sentence, "duration"),
            }
        )
        for token in _get(sentence, "tokens", []) or []:
            words.append(
                {
                    "text": _get(token, "text", ""),
                    "start": _get(token, "start"),
                    "end": _get(token, "end"),
                    "duration": _get(token, "duration"),
                }
            )
    duration = max(
        (segment["end"] for segment in segments if segment["end"] is not None),
        default=None,
    )
    return {
        "text": result_text(result),
        "duration": duration,
        "segments": segments,
        "words": words,
    }


def advanced_payload(
    result: Any,
    return_timestamps: bool,
    word_timestamps: bool,
) -> dict[str, Any]:
    aligned = alignment_payload(result)
    response: dict[str, Any] = {"text": aligned["text"]}
    if return_timestamps:
        response["segments"] = aligned["segments"]
    if word_timestamps:
        response["words"] = aligned["words"]
    response["has_timestamps"] = bool(
        (return_timestamps and aligned["segments"])
        or (word_timestamps and aligned["words"])
    )
    return response
