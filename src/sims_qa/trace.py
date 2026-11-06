"""Small local JSONL trace for one ask request and its workflow nodes."""

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


_WRITE_LOCK = Lock()


class TraceWriter:
    def __init__(self, path: str, *, log_questions: bool = False):
        self.path = Path(path)
        self.log_questions = log_questions

    def record(self, **fields: Any) -> None:
        event = {
            "at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            **fields,
        }
        line = json.dumps(event, ensure_ascii=False, default=str)
        try:
            with _WRITE_LOCK:
                self.path.parent.mkdir(parents=True, exist_ok=True)
                with self.path.open("a", encoding="utf-8") as file:
                    file.write(line + "\n")
        except OSError as exc:
            logging.getLogger(__name__).warning("链路日志写入失败：%s", type(exc).__name__)
