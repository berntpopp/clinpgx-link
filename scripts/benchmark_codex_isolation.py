"""Input, inventory, and Linux namespace isolation for the Codex benchmark."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from scripts.benchmark_codex_protocol import canonical_sha256

MODEL = "gpt-5.6-terra"
MODEL_PROVIDER = "openai"
REASONING_EFFORT = "high"
EXPECTED_SYSTEM_SKILLS = frozenset(
    {
        "imagegen",
        "openai-docs",
        "plugin-creator",
        "review-agent",
        "skill-creator",
        "skill-installer",
    }
)
_VERSION = re.compile(r"(?:^|/)([0-9]+\.[0-9]+\.[0-9]+)(?:$|\s)")


class RunInputError(ValueError):
    """A safe failure detected before launching a model turn."""


def positive_float(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be a number") from exc
    if not 0 < value < float("inf"):
        raise argparse.ArgumentTypeError("must be finite and positive")
    return value


def positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("must be an integer") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError("must be positive")
    return value


def validate_loopback_url(raw: str) -> str:
    try:
        parsed = urlsplit(raw)
        port = parsed.port
    except ValueError as exc:
        raise RunInputError("MCP URL must be a canonical loopback HTTP URL") from exc
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "::1", "localhost"}
        or port is None
        or parsed.username is not None
        or parsed.password is not None
        or not parsed.path.startswith("/")
        or parsed.path == "/"
        or parsed.query
        or parsed.fragment
    ):
        raise RunInputError("MCP URL must be a canonical loopback HTTP URL")
    host = f"[{parsed.hostname}]" if parsed.hostname == "::1" else parsed.hostname
    canonical = f"http://{host}:{port}{parsed.path}"
    if raw != canonical:
        raise RunInputError("MCP URL must be a canonical loopback HTTP URL")
    return canonical


def validate_home_environment(
    home_raw: str | None, account_home: Path, codex_home_raw: str | None
) -> Path:
    if codex_home_raw is not None:
        raise RunInputError("CODEX_HOME must remain unset")
    if home_raw != str(account_home) or not account_home.is_absolute():
        raise RunInputError("HOME must remain the original absolute user home")
    return account_home


def open_prompt(path: Path, *, max_bytes: int) -> tuple[bytes, dict[str, Any]]:
    flags = os.O_RDONLY | os.O_NONBLOCK
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
    except OSError as exc:
        raise RunInputError("prompt must be an accessible non-symlink file") from exc
    with os.fdopen(descriptor, "rb", buffering=0) as handle:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise RunInputError("prompt must be a regular file")
        digest = hashlib.sha256()
        payload = bytearray()
        while chunk := handle.read(64 * 1024):
            if len(payload) + len(chunk) > max_bytes:
                raise RunInputError("prompt size exceeds the configured ceiling")
            payload.extend(chunk)
            digest.update(chunk)
    return bytes(payload), {"bytes": len(payload), "sha256": digest.hexdigest()}


def create_private_run_directory(path: Path) -> Path:
    try:
        path.mkdir(mode=0o700, parents=False)
    except FileExistsError as exc:
        raise RunInputError("output directory already exists") from exc
    except OSError as exc:
        raise RunInputError("cannot create private output directory") from exc
    os.chmod(path, 0o700)
    return path


def build_sandbox_command(
    *,
    bwrap: Path,
    codex: Path,
    home: Path,
    auth: Path,
    cwd: Path,
    mcp_url: str,
) -> list[str]:
    """Build an invocation-local, read-only app-server namespace."""
    trust = json.dumps(str(cwd))
    mcp = (
        "mcp_servers={clinpgx={url="
        + json.dumps(mcp_url)
        + ',required=true,enabled=true,default_tools_approval_mode="approve"}}'
    )
    return [
        str(bwrap),
        "--die-with-parent",
        "--new-session",
        "--unshare-user-try",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--clearenv",
        "--setenv",
        "HOME",
        str(home),
        "--setenv",
        "USER",
        home.name,
        "--setenv",
        "LOGNAME",
        home.name,
        "--setenv",
        "LANG",
        "C.UTF-8",
        "--setenv",
        "PATH",
        "/usr/local/bin:/usr/bin:/bin",
        "--ro-bind",
        "/",
        "/",
        "--tmpfs",
        str(home / ".codex"),
        "--ro-bind",
        str(auth),
        str(auth),
        "--tmpfs",
        str(home / ".agents"),
        "--tmpfs",
        "/tmp",  # noqa: S108 - private tmpfs created inside the namespace
        "--dir",
        str(cwd),
        "--chdir",
        str(cwd),
        str(codex),
        "app-server",
        "--stdio",
        "-c",
        "analytics.enabled=false",
        "-c",
        f'projects.{trust}.trust_level="trusted"',
        "-c",
        f'model="{MODEL}"',
        "-c",
        f'model_provider="{MODEL_PROVIDER}"',
        "-c",
        f'model_reasoning_effort="{REASONING_EFFORT}"',
        "-c",
        mcp,
        "-c",
        'web_search="disabled"',
        "-c",
        "tools.view_image=false",
        "-c",
        "agents.enabled=false",
        "--disable",
        "apps",
        "--disable",
        "plugins",
        "--disable",
        "hooks",
        "--disable",
        "shell_tool",
        "--disable",
        "multi_agent",
        "--disable",
        "skill_search",
        "--disable",
        "skill_mcp_dependency_install",
    ]


def _layer_type(layer: object) -> object:
    if not isinstance(layer, dict):
        return None
    name = layer.get("name")
    return name.get("type") if isinstance(name, dict) else None


def validate_preflight(values: dict[str, Any], *, expected_codex_home: Path) -> dict[str, Any]:
    """Reduce preflight responses to bounded allow-listed evidence."""
    failures: list[str] = []

    def fail(reason: str) -> None:
        if reason not in failures:
            failures.append(reason)

    initialize = values.get("initialize")
    initialize = initialize if isinstance(initialize, dict) else {}
    if initialize.get("codexHome") != str(expected_codex_home):
        fail("codex_home_mismatch")
    user_agent = initialize.get("userAgent")
    match = _VERSION.search(user_agent) if isinstance(user_agent, str) else None
    client_version = match.group(1) if match else None
    if client_version is None:
        fail("client_version_missing")

    config_result = values.get("config")
    config_result = config_result if isinstance(config_result, dict) else {}
    config = config_result.get("config")
    config = config if isinstance(config, dict) else {}
    servers = config.get("mcp_servers")
    servers = servers if isinstance(servers, dict) else {}
    if set(servers) != {"clinpgx"}:
        fail("unexpected_configured_mcp")
    clinpgx_config = servers.get("clinpgx")
    if (
        not isinstance(clinpgx_config, dict)
        or clinpgx_config.get("required") is not True
        or clinpgx_config.get("enabled") is not True
    ):
        fail("clinpgx_not_required")
    elif (
        clinpgx_config.get("enabled_tools") is not None
        or clinpgx_config.get("disabled_tools") is not None
    ):
        fail("configured_tool_filter_mismatch")
    layers = config_result.get("layers")
    layers = layers if isinstance(layers, list) else []
    user_layers = [layer for layer in layers if _layer_type(layer) == "user"]
    project_layers = [layer for layer in layers if _layer_type(layer) == "project"]
    if len(user_layers) != 1 or user_layers[0].get("config") != {}:
        fail("user_config_present")
    if project_layers:
        fail("project_config_present")

    hooks = values.get("hooks")
    hook_data = hooks.get("data", []) if isinstance(hooks, dict) else []
    if not isinstance(hook_data, list) or any(
        isinstance(entry, dict) and entry.get("hooks") for entry in hook_data
    ):
        fail("hooks_present")
    plugins = values.get("plugins")
    marketplaces = plugins.get("marketplaces", []) if isinstance(plugins, dict) else []
    if not isinstance(marketplaces, list) or any(
        isinstance(entry, dict) and entry.get("plugins") for entry in marketplaces
    ):
        fail("plugins_present")

    skills_result = values.get("skills")
    skill_groups = skills_result.get("data", []) if isinstance(skills_result, dict) else []
    skills = [
        skill
        for group in skill_groups
        if isinstance(group, dict)
        for skill in group.get("skills", [])
        if isinstance(skill, dict)
    ]
    skill_names: list[str] = []
    for skill in skills:
        name = skill.get("name")
        if isinstance(name, str):
            skill_names.append(name)
    skill_names.sort()
    if set(skill_names) != EXPECTED_SYSTEM_SKILLS or any(
        skill.get("scope") != "system"
        or skill.get("enabled") is not True
        or skill.get("pluginId") is not None
        for skill in skills
    ):
        fail("unexpected_skills")

    thread = values.get("thread")
    thread = thread if isinstance(thread, dict) else {}
    if thread.get("model") != MODEL:
        fail("model_identity_mismatch")
    if thread.get("modelProvider") != MODEL_PROVIDER:
        fail("model_provider_mismatch")
    if thread.get("reasoningEffort") != REASONING_EFFORT:
        fail("reasoning_effort_mismatch")
    if thread.get("instructionSources") != []:
        fail("instruction_sources_present")
    thread_value = thread.get("thread")
    if not isinstance(thread_value, dict) or thread_value.get("ephemeral") is not True:
        fail("thread_not_ephemeral")

    mcp_result = values.get("mcp")
    runtimes = mcp_result.get("data", []) if isinstance(mcp_result, dict) else []
    if (
        not isinstance(runtimes, list)
        or len(runtimes) != 1
        or not isinstance(runtimes[0], dict)
        or runtimes[0].get("name") != "clinpgx"
        or runtimes[0].get("pluginId") is not None
    ):
        fail("unexpected_runtime_mcp")
        runtime: dict[str, Any] = {}
    else:
        runtime = runtimes[0]
    if runtime.get("runtimeStatus") != "connected":
        fail("mcp_not_connected")
    runtime_tools = runtime.get("tools")
    runtime_tools = runtime_tools if isinstance(runtime_tools, dict) else {}
    tool_schemas: dict[str, dict[str, Any]] = {}
    if not runtime_tools:
        fail("empty_tool_inventory")
    for name, metadata in runtime_tools.items():
        schema = metadata.get("inputSchema") if isinstance(metadata, dict) else None
        if not isinstance(name, str) or not name or not isinstance(schema, dict):
            fail("invalid_tool_schema")
            continue
        try:
            Draft202012Validator.check_schema(schema)
        except (SchemaError, RecursionError):
            fail("invalid_tool_schema")
            continue
        if schema.get("type") != "object":
            fail("invalid_tool_schema")
            continue
        tool_schemas[name] = {"schema": schema, "sha256": canonical_sha256(schema)}
    return {
        "failures": failures,
        "client_version": client_version,
        "intrinsic_system_skills": skill_names,
        "tool_names": sorted(runtime_tools),
        "tool_schemas": tool_schemas,
        "observed_client_identity": {
            key: thread.get(key)
            for key in ("model", "modelProvider", "reasoningEffort", "instructionSources")
        },
    }


def verify_descendants(
    processes: list[dict[str, Any]], allowed_executables: set[Path]
) -> dict[str, Any]:
    allowed = {str(path) for path in allowed_executables}
    observed = [
        {
            "pid": row.get("pid"),
            "ppid": row.get("ppid"),
            "executable": row.get("executable"),
            "inspection_error": row.get("inspection_error"),
            "identity_verified": row.get("executable") in allowed,
        }
        for row in processes
    ]
    unexpected = [row for row in observed if not row["identity_verified"]]
    failures: list[str] = []
    if any(row["inspection_error"] is not None for row in unexpected):
        failures.append("uninspectable_descendant")
    if any(row["inspection_error"] is None for row in unexpected):
        failures.append("unexpected_descendant")
    return {
        "observed": observed,
        "unexpected": unexpected,
        "failures": failures,
    }


def resolve_toolchain() -> tuple[Path, Path, set[Path]]:
    """Resolve one installed native Codex binary and its exact helper paths."""
    bwrap_raw = shutil.which("bwrap")
    codex_raw = shutil.which("codex")
    if bwrap_raw is None or codex_raw is None:
        raise RunInputError("Linux bubblewrap and Codex CLI are required")
    bwrap = Path(bwrap_raw).resolve(strict=True)
    launcher = Path(codex_raw).resolve(strict=True)
    try:
        with launcher.open("rb") as source:
            is_native = source.read(4) == b"\x7fELF"
    except OSError as exc:
        raise RunInputError("cannot inspect the installed Codex executable") from exc
    if is_native:
        codex = launcher
    else:
        package_root = launcher.parent.parent
        candidates = [
            path.resolve(strict=True)
            for path in package_root.glob("node_modules/@openai/codex-*/vendor/*/bin/codex")
            if path.is_file() and os.access(path, os.X_OK)
        ]
        candidates = list(dict.fromkeys(candidates))
        if len(candidates) != 1:
            raise RunInputError("installed Codex native executable is ambiguous")
        codex = candidates[0]
    code_mode = codex.with_name("codex-code-mode-host")
    if not code_mode.is_file() or not os.access(code_mode, os.X_OK):
        raise RunInputError("installed Codex code-mode host is unavailable")
    return bwrap, codex, {bwrap, codex, code_mode.resolve(strict=True)}


def _resolve_proc_executable(proc_dir: Path) -> str:
    return str((proc_dir / "exe").resolve(strict=True))


def _read_proc_stat(stat_path: Path) -> str:
    return stat_path.read_text(encoding="utf-8")


def _parse_proc_stat(raw: object) -> tuple[int, int, str, int] | None:
    """Return PID, parent PID, state, and starttime from one proc stat record."""
    if not isinstance(raw, str):
        return None
    first_space = raw.find(" ")
    close = raw.rfind(")")
    if first_space <= 0 or close <= first_space + 1 or raw[first_space + 1] != "(":
        return None
    fields = raw[close + 1 :].split()
    if len(fields) < 20 or len(fields[0]) != 1:
        return None
    try:
        pid = int(raw[:first_space])
        parent = int(fields[1])
        starttime = int(fields[19])
    except (IndexError, ValueError):
        return None
    if pid <= 0 or parent < 0 or starttime < 0:
        return None
    return pid, parent, fields[0], starttime


def process_snapshot(
    root_pid: int,
    *,
    proc_root: Path = Path("/proc"),
    executable_resolver: Callable[[Path], str] = _resolve_proc_executable,
    stat_reader: Callable[[Path], str] = _read_proc_stat,
    known_descendants: dict[int, int] | None = None,
) -> list[dict[str, Any]]:
    """Read only PID relationships and exact executable symlinks from procfs."""
    rows: dict[int, tuple[int, str, int]] = {}
    known = known_descendants if known_descendants is not None else {root_pid: 0}
    unreadable: dict[int, str] = {}
    for stat_path in proc_root.glob("[0-9]*/stat"):
        path_pid = int(stat_path.parent.name)
        try:
            raw = stat_reader(stat_path)
            parsed = _parse_proc_stat(raw)
            if parsed is None or parsed[0] != path_pid:
                continue
            pid, parent, state, starttime = parsed
            rows[pid] = (parent, state, starttime)
        except PermissionError:
            if path_pid in known and stat_path.parent.exists():
                unreadable[path_pid] = "stat_permission_denied"
            continue
        except (FileNotFoundError, ProcessLookupError, ValueError):
            continue
    descendants = {root_pid, *(pid for pid in known if pid in rows)}
    changed = True
    while changed:
        changed = False
        for pid, (parent, _state, _starttime) in rows.items():
            if parent in descendants and pid not in descendants:
                descendants.add(pid)
                changed = True
    for pid in descendants:
        if pid in rows:
            known[pid] = rows[pid][0]
    output: list[dict[str, Any]] = []
    for pid, inspection_error in sorted(unreadable.items()):
        output.append(
            {
                "pid": pid,
                "ppid": known[pid],
                "executable": None,
                "inspection_error": inspection_error,
            }
        )
    for pid in sorted(descendants):
        if pid not in rows:
            continue
        proc_dir = proc_root / str(pid)
        parent, state, starttime = rows[pid]
        try:
            executable = executable_resolver(proc_dir)
        except PermissionError:
            if proc_dir.exists() and state != "Z":
                output.append(
                    {
                        "pid": pid,
                        "ppid": parent,
                        "executable": None,
                        "inspection_error": "executable_permission_denied",
                    }
                )
            continue
        except (FileNotFoundError, ProcessLookupError):
            if proc_dir.exists() and state != "Z":
                try:
                    fresh = _parse_proc_stat(stat_reader(proc_dir / "stat"))
                except (OSError, UnicodeError, ValueError):
                    fresh = None
                if fresh is None or not (
                    fresh[0] == pid and fresh[2] == "Z" and fresh[3] == starttime
                ):
                    output.append(
                        {
                            "pid": pid,
                            "ppid": parent,
                            "executable": None,
                            "inspection_error": "executable_unavailable",
                        }
                    )
            continue
        output.append(
            {
                "pid": pid,
                "ppid": parent,
                "executable": executable,
                "inspection_error": None,
            }
        )
    return output


def monitor_processes(
    root_pid: int,
    allowed: set[Path],
    stop: threading.Event,
    violation: threading.Event,
    observed: dict[tuple[int, str], dict[str, Any]],
    failure_state: dict[str, str],
) -> None:
    """Continuously reject descendants whose procfs executable is not pinned."""
    known_descendants = {root_pid: 0}
    while not stop.is_set():
        checked = verify_descendants(
            process_snapshot(root_pid, known_descendants=known_descendants), allowed
        )
        for row in checked["observed"]:
            observed[(int(row["pid"]), str(row["executable"]))] = row
        if checked["failures"]:
            failure_state["reason"] = str(checked["failures"][0])
            violation.set()
        stop.wait(0.05)
