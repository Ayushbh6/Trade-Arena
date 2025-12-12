"""Execution layer (only place that touches exchange keys).

Keep this package `__init__` lightweight to avoid import cycles.
Import concrete modules directly, e.g.:
  - `from src.execution.executor import OrderExecutor`
  - `from src.execution.planner import build_order_plan`
"""

__all__: list[str] = []
