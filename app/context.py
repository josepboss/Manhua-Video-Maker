from collections import deque

MAX_CONTEXT = 5

_context_buffer: deque = deque(maxlen=MAX_CONTEXT)


def update_context(panel_summary: str) -> None:
    _context_buffer.append(panel_summary.strip())


def get_context() -> str:
    if not _context_buffer:
        return ""
    summaries = list(_context_buffer)
    joined = " ".join(f"{s}." if not s.endswith(".") else s for s in summaries)
    return f"Previous context: {joined}"


def reset_context() -> None:
    _context_buffer.clear()
