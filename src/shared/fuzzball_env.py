"""Pipe selected dotenv values into user-scoped Fuzzball value secrets."""

import argparse
import ipaddress
import json
import os
import shlex
import subprocess
from pathlib import Path
from urllib.parse import urlsplit

import yaml
from dotenv import dotenv_values

VARIABLES = {
    "POSTGRES_PASSWORD": "postgres-password",
    "HF_TOKEN": "hf-token",
    "VLLM_REASONING_EFFORT": "vllm-reasoning-effort",
    "VLLM_REASONING_TOKENS": "vllm-reasoning-tokens",
    "LOG_LEVEL": "log-level",
}
DEFAULTS = {
    "VLLM_REASONING_EFFORT": "low",
    "VLLM_REASONING_TOKENS": "4096",
    "LOG_LEVEL": "INFO",
}
ALLOW_EMPTY = {"VLLM_REASONING_EFFORT"}


def read_values(path: Path) -> dict[str, str]:
    if not path.is_file():
        raise FileNotFoundError(f"Environment file not found: {path}")
    # Do not source shell code or expand references from the submitter environment.
    values = dotenv_values(path, interpolate=False)
    return DEFAULTS | {
        name: value
        for name, value in values.items()
        if name in VARIABLES and value is not None
    }


def validate_value(name: str, value: str) -> None:
    if not value.strip() and name not in ALLOW_EMPTY:
        raise ValueError(f"{name} must not be empty")
    if name == "VLLM_BASE_URL":
        try:
            url = urlsplit(value)
            host = url.hostname
            port = url.port
        except ValueError:
            raise ValueError(f"{name} is not a valid service URL") from None
        if url.scheme not in {"http", "https"} or not host or not url.path.strip("/"):
            raise ValueError(f"{name} must be a complete service URL")
        if port is not None and port < 1:
            raise ValueError(f"{name} has an invalid port")
        normalized = host.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(normalized)
        except ValueError:
            local = normalized == "localhost" or normalized.endswith(".localhost")
        else:
            local = address.is_loopback or address.is_unspecified
        if local:
            raise ValueError(f"{name} must use a cluster-reachable host, not loopback")
    if name == "VLLM_REASONING_TOKENS" and not value.isdecimal():
        raise ValueError(f"{name} must be a nonnegative integer")
    if name == "LOG_LEVEL" and value.upper() not in {
        "DEBUG",
        "INFO",
        "WARNING",
        "ERROR",
        "CRITICAL",
    }:
        raise ValueError(f"{name} is not a supported logging level")


def selected_values(values: dict[str, str], names: list[str]) -> dict[str, str]:
    errors = []
    for name in names:
        if name not in values:
            errors.append(f"Missing {name}")
            continue
        try:
            validate_value(name, values[name])
        except ValueError as error:
            errors.append(str(error))
    if errors:
        raise ValueError("\n".join(errors))
    return {name: values[name] for name in names}


def run_cli(fuzzball: str, arguments: list[str], *, payload: str | None = None) -> str:
    result = subprocess.run(
        [fuzzball, *arguments],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        # CLI output can echo secret material; never propagate it into logs.
        raise RuntimeError(
            f"Fuzzball {' '.join(arguments[:2])} failed "
            f"(exit {result.returncode}); check cluster login and permissions"
        )
    return result.stdout


def sync_values(fuzzball: str, values: dict[str, str]) -> None:
    result = run_cli(
        fuzzball,
        ["secret", "list", "--name", "chi-ciq-*", "--output", "json"],
    )
    existing = json.loads(result)
    refs = {
        item["reference"]: item
        for item in existing
        if item.get("reference", "").startswith("secret://user/chi-ciq-")
    }
    for name, value in values.items():
        if not value and name in ALLOW_EMPTY:
            continue
        ref = f"secret://user/chi-ciq-{VARIABLES[name]}"
        if ref in refs and refs[ref].get("type") != "SECRET_TYPE_VALUE":
            raise ValueError(f"{ref} exists but is not a value secret")
    for name, value in values.items():
        if not value and name in ALLOW_EMPTY:
            print(f"inline empty: {name} (Fuzzball rejects empty value secrets)")
            continue
        ref = f"secret://user/chi-ciq-{VARIABLES[name]}"
        action = "update" if ref in refs else "create"
        arguments = ["secret", action, ref]
        if action == "create":
            arguments.extend(["--type", "value"])
        # JSON handles quotes and multiline values without
        # putting credentials in argv or temporary files.
        run_cli(
            fuzzball,
            arguments,
            payload=json.dumps({"type": "value", "secret": {"value": value}}),
        )
        print(f"{action}: {name} -> {ref}")


def configured_workflow(path: Path, values: dict[str, str]) -> dict:
    definition = yaml.safe_load(path.read_text())
    nodes = list(definition.get("jobs", {}).values()) + list(
        definition.get("services", {}).values()
    )
    nodes.append(definition.get("defaults", {}).get("job", {}))
    for node in nodes:
        env = node.get("env", [])
        updated = []
        empty = []
        for entry in env:
            name = entry.partition("=")[0]
            if name in ALLOW_EMPTY and name in values:
                if values[name]:
                    entry = f"{name}=secret://user/chi-ciq-{VARIABLES[name]}"
                else:
                    empty.append(name)
                    continue
            updated.append(entry)
        if "env" in node:
            node["env"] = updated
        if empty:
            exports = "\n".join(f"export {name}=''" for name in empty) + "\n"
            if "command" in node:
                node["script"] = (
                    "#!/bin/sh\n"
                    + exports
                    + "exec "
                    + shlex.join(node.pop("command"))
                    + "\n"
                )
            elif "script" in node:
                first, separator, rest = node["script"].partition("\n")
                node["script"] = first + separator + exports + rest
    return definition


def write_workflow(path: Path, definition: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(
        path, "w", encoding="utf-8", opener=lambda p, flags: os.open(p, flags, 0o600)
    ) as stream:
        yaml.safe_dump(definition, stream, sort_keys=False)
    print(f"Configured workflow: {path}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=Path(".env"))
    parser.add_argument(
        "--fuzzball", default=os.environ.get("FUZZBALL_BIN", "fuzzball")
    )
    parser.add_argument(
        "--only",
        nargs="+",
        choices=tuple(VARIABLES),
        help="Sync just these variables; otherwise require the complete deployment",
    )
    parser.add_argument(
        "--check", action="store_true", help="Validate without uploading"
    )
    parser.add_argument("--workflow", type=Path, default=Path("fuzzball.yaml"))
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/deployment/fuzzball.yaml"),
        help="Configured Fuzzfile containing references, never credential values",
    )
    args = parser.parse_args(argv)
    try:
        values = selected_values(
            read_values(args.env_file), args.only or list(VARIABLES)
        )
        definition = configured_workflow(args.workflow, values)
        if args.check:
            for name in values:
                print(f"ready: {name} -> secret://user/chi-ciq-{VARIABLES[name]}")
        else:
            sync_values(args.fuzzball, values)
            write_workflow(args.output, definition)
    except (OSError, ValueError, RuntimeError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
