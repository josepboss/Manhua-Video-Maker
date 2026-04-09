from collections import deque
from typing import List

MAX_CONTEXT = 5

_context_buffer: deque = deque(maxlen=MAX_CONTEXT)


def update_context(panel_summary: str) -> None:
    _context_buffer.append(panel_summary.strip())


def get_context() -> str:
    if not _context_buffer:
        return ""
    summaries = list(_context_buffer)
    lines = [f"- {s}" for s in summaries]
    return "Recent story context (last panels):\n" + "\n".join(lines)


def reset_context() -> None:
    _context_buffer.clear()
