from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .errors import SpecError


def load_document(path: str | Path) -> dict[str, Any]:
    source = Path(path)
    try:
        text = source.read_text(encoding="utf-8")
    except OSError as exc:
        raise SpecError(f"cannot read {source}: {exc}") from exc

    suffix = source.suffix.lower()
    try:
        if suffix == ".json":
            value = json.loads(text)
        elif suffix in {".yaml", ".yml"}:
            try:
                import yaml  # type: ignore[import-not-found]
            except ImportError as exc:
                raise SpecError(
                    "YAML input requires the optional dependency: pip install 'real-agentic-workflows[yaml]'"
                ) from exc

            class UniqueKeyLoader(yaml.SafeLoader):
                pass

            def construct_mapping(loader: Any, node: Any, deep: bool = False) -> dict[Any, Any]:
                mapping: dict[Any, Any] = {}
                for key_node, value_node in node.value:
                    key = loader.construct_object(key_node, deep=deep)
                    if key in mapping:
                        raise SpecError(
                            f"duplicate YAML key {key!r} at line {key_node.start_mark.line + 1}, "
                            f"column {key_node.start_mark.column + 1}"
                        )
                    mapping[key] = loader.construct_object(value_node, deep=deep)
                return mapping

            UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, construct_mapping)
            try:
                value = yaml.load(text, Loader=UniqueKeyLoader)
            except yaml.YAMLError as exc:
                mark = getattr(exc, "problem_mark", None)
                location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
                raise SpecError(f"invalid YAML in {source}{location}: {exc}") from exc
        else:
            raise SpecError(f"unsupported file type {suffix!r}; use .json, .yaml, or .yml")
    except (json.JSONDecodeError, ValueError) as exc:
        location = ""
        if hasattr(exc, "lineno"):
            location = f" at line {exc.lineno}, column {exc.colno}"
        raise SpecError(f"invalid {suffix[1:].upper()} in {source}{location}: {exc}") from exc

    if not isinstance(value, dict):
        raise SpecError(f"{source} must contain an object at the document root")
    return value


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def write_json(path: str | Path, value: Any) -> None:
    destination = Path(path)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(pretty_json(value), encoding="utf-8")
