#!/usr/bin/env python3
"""Create a slot or infrastructure Compose model from the canonical model.

The canonical Compose file contains both stateful dependencies and application
services. Blue/green deployment must not duplicate the stateful dependencies,
so this small transformer keeps the source of truth in one file while
generating isolated stateless slot definitions.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


INFRASTRUCTURE = {"postgres-master", "redis", "rabbitmq", "keycloak"}
BOOTSTRAP = "bootstrap"


def transform(
    source: Path,
    destination: Path,
    mode: str,
    shared_network: str,
    gateway_port: int,
    slot_alias: str = "",
) -> None:
    model = json.loads(source.read_text())
    all_services = model["services"]

    if mode == "slot":
        names = set(all_services) - INFRASTRUCTURE - {BOOTSTRAP}
    elif mode == "infra":
        names = INFRASTRUCTURE
    else:
        raise SystemExit(f"Unsupported Compose transform mode: {mode}")

    services = {}
    for name in sorted(names):
        service = dict(all_services[name])
        service.pop("container_name", None)
        service.pop("ports", None)

        dependencies = service.get("depends_on")
        if isinstance(dependencies, dict):
            service["depends_on"] = {
                dependency: condition
                for dependency, condition in dependencies.items()
                if dependency in names
            }
        elif isinstance(dependencies, list):
            service["depends_on"] = [dependency for dependency in dependencies if dependency in names]

        service["networks"] = {
            "shared-infrastructure": {},
        }
        if mode == "slot":
            aliases = [f"{name}-{slot_alias}"]
            service["networks"]["shared-infrastructure"] = {"aliases": aliases}
            service["networks"]["slot-network"] = {"aliases": aliases}
            environment = service.get("environment")
            if isinstance(environment, dict):
                for key, value in environment.items():
                    if isinstance(value, str):
                        environment[key] = re.sub(
                            r"http://(" + "|".join(map(re.escape, sorted(names))) + r"):",
                            lambda match: f"http://{match.group(1)}-{slot_alias}:",
                            value,
                        )
            if name == "api-gateway":
                service["ports"] = [f"127.0.0.1:{gateway_port}:8000"]

        services[name] = service

    model["services"] = services
    model["networks"] = {
        "shared-infrastructure": {"external": True, "name": shared_network},
    }
    if mode == "slot":
        model["networks"]["slot-network"] = {}

    destination.write_text(json.dumps(model, indent=2) + "\n")


if __name__ == "__main__":
    if len(sys.argv) not in (6, 7):
        raise SystemExit(
            "usage: compose-slot.py SOURCE DESTINATION slot|infra SHARED_NETWORK GATEWAY_PORT"
        )
    transform(
        Path(sys.argv[1]),
        Path(sys.argv[2]),
        sys.argv[3],
        sys.argv[4],
        int(sys.argv[5]),
        sys.argv[6] if len(sys.argv) == 7 else "",
    )
