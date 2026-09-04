"""Live operational figures read from the tenant database.

This package is the only route by which hospital data may reach the assistant's
model, and it keeps the properties the content tools already have: the server
decides which metric runs, the SQL is fixed text with bound parameters, every
row is projected through a deny-by-default column allowlist, and the transaction
is pinned read-only in the database itself.
"""

from app.assistant.live.registry import (  # noqa: F401
    METRIC_REGISTRY,
    MetricDefinition,
    get_metric,
    load_catalog,
    permitted_metrics,
)

load_catalog()
