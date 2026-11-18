"""Checks that system.yaml still describes the code, beyond what the schema can see.

- Every environment variable a service's code reads is listed under its env,
  and nothing is listed that the code no longer reads.
- Every call across the seam names an endpoint that exists in the other
  service's openapi.yaml, with the same method.

CI runs it after the schema check. Run it from the repository root:

    python scripts/check_system.py
"""

import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
# os.environ.get("NAME", ...), os.environ["NAME"], os.environ.setdefault("NAME", ...)
READS = re.compile(r"""os\.environ(?:\.get|\.setdefault)?[\[(]\s*["']([A-Z0-9_]+)["']""")


def variables_read(service_dir: Path) -> set[str]:
    names: set[str] = set()
    for path in (service_dir / "app").rglob("*.py"):
        names.update(READS.findall(path.read_text(encoding="utf-8")))
    # Read by the Dockerfile's HEALTHCHECK and baked in by its ARG.
    dockerfile = (service_dir / "Dockerfile").read_text(encoding="utf-8")
    names.update(re.findall(r"^ENV ([A-Z0-9_]+)=", dockerfile, re.MULTILINE))
    names.discard("PYTHONDONTWRITEBYTECODE")
    names.discard("PYTHONUNBUFFERED")
    return names


def operations(openapi_file: Path) -> set[str]:
    document = yaml.safe_load(openapi_file.read_text(encoding="utf-8"))
    return {f"{method.upper()} {path}" for path, item in document["paths"].items() for method in item}


def problems() -> list[str]:
    system = yaml.safe_load((ROOT / "system.yaml").read_text(encoding="utf-8"))
    services = system["services"]
    found = []
    for name, service in services.items():
        listed = set(service["env"])
        read = variables_read(ROOT / service["path"])
        for missing in sorted(read - listed):
            found.append(f"{name}: the code reads {missing}, but system.yaml does not list it")
        for extra in sorted(listed - read):
            found.append(f"{name}: system.yaml lists {extra}, but the code never reads it")
        for call in service["calls"]:
            other = services.get(call["service"])
            if other is None:
                found.append(f"{name}: calls unknown service {call['service']}")
                continue
            if call["url_env"] not in listed:
                found.append(f"{name}: {call['url_env']} is not in its env")
            known = operations(ROOT / other["openapi"])
            for endpoint in call["endpoints"]:
                if endpoint not in known:
                    found.append(f"{name}: calls {endpoint}, which {call['service']}'s openapi.yaml does not have")
    for target in system["ui"]["calls"]:
        if target not in services:
            found.append(f"ui: calls unknown service {target}")
    return found


def main() -> int:
    found = problems()
    for problem in found:
        print(problem, file=sys.stderr)
    if not found:
        print("system.yaml matches the code.")
    return 1 if found else 0


if __name__ == "__main__":
    sys.exit(main())
