"""Shared assertions for bounded MCP response tests."""


def fence_count(value):
    if isinstance(value, dict):
        return int(value.get("kind") == "untrusted_text") + sum(
            fence_count(child) for child in value.values()
        )
    if isinstance(value, list):
        return sum(fence_count(child) for child in value)
    return 0
