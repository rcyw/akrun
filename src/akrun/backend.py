"""Public execution boundary; implementations never expose backend objects."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from akrun.models import ExecutionRef, StagePlan, StageResult


class ExecutionBackend(Protocol):
    def execute(
        self,
        plan: StagePlan,
        *,
        artifact_dir: Path,
        on_bound: Callable[[ExecutionRef], None],
        model: Any = None,
    ) -> StageResult: ...

    def recover(
        self, execution: ExecutionRef, plan: StagePlan, *, artifact_dir: Path, timeout: float = 0
    ) -> StageResult | None: ...

    def cancel(self, execution: ExecutionRef) -> None: ...

    def inspect(self, execution: ExecutionRef) -> dict[str, Any]: ...
