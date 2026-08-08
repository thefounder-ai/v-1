from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.observability import elapsed_ms, log_event


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RecommendationGraphState:
    """Small explicit state machine for the recommendation agent.

    Keeping this state local makes every stage inspectable without introducing a
    heavyweight orchestration dependency for the core request path.
    """

    trace_id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "started"
    stages: list[dict[str, Any]] = field(default_factory=list)
    retrieval: dict[str, Any] = field(default_factory=dict)
    error_stage: str | None = None

    def start_stage(self, name: str) -> float:
        started = time.perf_counter()
        self.stages.append({
            "name": name,
            "status": "running",
            "started_at": _utc_now_iso(),
        })
        return started

    def finish_stage(
        self,
        name: str,
        started: float,
        *,
        status: str = "completed",
        **metadata: Any,
    ) -> None:
        stage = next(
            item for item in reversed(self.stages) if item["name"] == name and item["status"] == "running"
        )
        stage.update({
            "status": status,
            "duration_ms": elapsed_ms(started),
            "completed_at": _utc_now_iso(),
        })
        stage.update(metadata)

    def fail_stage(self, name: str, started: float, error_code: str) -> None:
        self.error_stage = name
        self.status = "failed"
        self.finish_stage(name, started, status="failed", error_code=error_code)

    def complete(self) -> None:
        self.status = "completed"

    def metadata(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "status": self.status,
            "stages": self.stages,
            "retrieval": self.retrieval,
            "error_stage": self.error_stage,
        }


def log_graph_state(logger: logging.Logger, state: RecommendationGraphState) -> None:
    log_event(
        logger,
        logging.INFO if state.status == "completed" else logging.WARNING,
        "recommendation_graph_finished",
        trace_id=state.trace_id,
        graph_status=state.status,
        stages=state.stages,
        retrieval=state.retrieval,
        error_stage=state.error_stage,
    )