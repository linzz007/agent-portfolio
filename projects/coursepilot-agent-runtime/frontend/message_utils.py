import re


_INTERNAL_META_RE = re.compile(
    r"<!--\s*(?:QUIZ_META|EXAM_META)\b[\s\S]*?-->",
    flags=re.IGNORECASE,
)
_SOURCE_MARKER_RE = re.compile(r"\[来源\d+\]")
_META_START_MARKERS = (
    "<!-- QUIZ_META",
    "<!--QUIZ_META",
    "<!-- EXAM_META",
    "<!--EXAM_META",
)


def strip_hidden_metadata(text: str) -> str:
    if not text:
        return text
    return _INTERNAL_META_RE.sub("", str(text)).strip()


def strip_source_markers(text: str) -> str:
    if not text:
        return text
    return _SOURCE_MARKER_RE.sub("", str(text))


class HiddenMetadataStreamFilter:
    def __init__(self):
        self._buffer = ""

    @staticmethod
    def _find_meta_start(text: str) -> int:
        starts = [text.find(marker) for marker in _META_START_MARKERS]
        starts = [idx for idx in starts if idx >= 0]
        return min(starts) if starts else -1

    @staticmethod
    def _pending_prefix_len(text: str) -> int:
        max_len = 0
        for marker in _META_START_MARKERS:
            limit = min(len(marker) - 1, len(text))
            for size in range(1, limit + 1):
                if text.endswith(marker[:size]):
                    max_len = max(max_len, size)
        return max_len

    def push(self, chunk: str) -> str:
        self._buffer += str(chunk or "")
        visible_parts = []

        while self._buffer:
            start = self._find_meta_start(self._buffer)
            if start < 0:
                keep = self._pending_prefix_len(self._buffer)
                if keep:
                    visible_parts.append(self._buffer[:-keep])
                    self._buffer = self._buffer[-keep:]
                else:
                    visible_parts.append(self._buffer)
                    self._buffer = ""
                break

            visible_parts.append(self._buffer[:start])
            end = self._buffer.find("-->", start)
            if end < 0:
                self._buffer = self._buffer[start:]
                break
            self._buffer = self._buffer[end + 3 :]

        return "".join(visible_parts)

    def flush(self) -> str:
        remaining = self._buffer
        self._buffer = ""
        if any(remaining.startswith(marker) for marker in _META_START_MARKERS):
            return ""
        return strip_hidden_metadata(remaining)
