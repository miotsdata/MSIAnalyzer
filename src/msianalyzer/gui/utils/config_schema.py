"""Data-driven form schema for `msianalyzer.core.config.config.Config`.

`NewAnalysisPage.qml` needs one tab per config group, with a control per
field. Rather than hand-writing ~70 near-identical QML field blocks, this
module introspects the `Config` dataclasses themselves and produces a plain,
QML-friendly schema the page renders generically. `io` (`IOConfig`) is
excluded — it gets a bespoke tab (sample-pair pickers, output folder), not a
row-per-field rendering.
"""

from __future__ import annotations

import dataclasses
import types
import typing

from PySide6.QtCore import Property, QObject

from msianalyzer.core.config.config import GROUPS, GROUP_TITLES

_UNION_ORIGINS = (typing.Union, types.UnionType)


def _classify(annotation: object) -> str:
    """Classify a resolved field type annotation into a UI control kind.

    Args:
        annotation: A resolved (non-string) type, e.g. from
            `typing.get_type_hints`.

    Returns:
        One of `"bool"`, `"int"`, `"float"`, `"str"`, `"optional_bool"`,
        `"optional_int"`, `"optional_float"`, `"optional_str"`,
        `"optional_str_list"` (an optional `list[str]`, comma-separated in
        the UI) or `"path_list"` (the `str | list[str] | None` shape used
        only by `AnnotateConfig.library_path`).

    Raises:
        ValueError: If `annotation` doesn't match any known shape — a
            config field was added whose type this schema doesn't know how
            to render yet.
    """
    if annotation is bool:
        return "bool"
    if annotation is int:
        return "int"
    if annotation is float:
        return "float"
    if annotation is str:
        return "str"

    origin = typing.get_origin(annotation)
    args = typing.get_args(annotation)

    if origin in _UNION_ORIGINS:
        non_none = [a for a in args if a is not type(None)]

        if len(non_none) == 1:
            inner = non_none[0]
            if inner is str:
                return "optional_str"
            if inner is int:
                return "optional_int"
            if inner is float:
                return "optional_float"
            if inner is bool:
                return "optional_bool"
            if typing.get_origin(inner) is list and typing.get_args(inner) == (str,):
                return "optional_str_list"

        if str in non_none and any(
            typing.get_origin(a) is list and typing.get_args(a) == (str,)
            for a in non_none
        ):
            return "path_list"

    raise ValueError(f"config_schema: don't know how to render {annotation!r}")


def build_config_schema() -> list[dict]:
    """Build the form schema for every `Config` group except `io`.

    Returns:
        One entry per group, in `GROUPS` order: `{"key", "title", "fields"}`,
        where `fields` is a list of `{"name", "kind", "default"}`.
    """
    schema: list[dict] = []
    for group_key, group_type in GROUPS.items():
        if group_key == "io":
            continue

        hints = typing.get_type_hints(group_type)
        fields = [
            {
                "name": f.name,
                "kind": _classify(hints[f.name]),
                "default": f.default if f.default is not dataclasses.MISSING else None,
            }
            for f in dataclasses.fields(group_type)
        ]
        schema.append(
            {"key": group_key, "title": GROUP_TITLES[group_key], "fields": fields}
        )
    return schema


def coerce_config_values(config_dict: dict) -> dict:
    """Coerce a QML-sourced config dict's `int` fields to actual `int`.

    JS has one numeric type, so a value round-tripped through a QML signal
    can arrive as a Python `float` for a field the `Config` dataclasses
    declare `int` (e.g. `IOConfig`-sibling `ms1.chunk_size: int`).
    `Config.from_dict` doesn't validate or coerce dataclass field types, so
    this has to happen at the QML -> Python boundary, before the dict
    reaches `Config.from_dict`.

    Args:
        config_dict: Nested `{group: {field: value}}` mapping, as produced
            by `NewAnalysisPage.qml`'s `collectConfig()`.

    Returns:
        A new dict (shallow copy, with fresh per-group dicts) with every
        `"int"`/`"optional_int"` field cast to `int` (`None` stays `None`).
        Everything else — including `"io"`, which this schema doesn't cover
        — passes through unchanged.
    """
    result = {
        key: (dict(value) if isinstance(value, dict) else value)
        for key, value in config_dict.items()
    }
    for group in build_config_schema():
        group_dict = result.get(group["key"])
        if not isinstance(group_dict, dict):
            continue
        for field in group["fields"]:
            if field["kind"] not in ("int", "optional_int"):
                continue
            value = group_dict.get(field["name"])
            if value is not None:
                group_dict[field["name"]] = int(value)
    return result


class ConfigSchemaProvider(QObject):
    """QML-facing wrapper around `build_config_schema()`.

    Static metadata (derived from `Config`'s dataclass shape, not app
    state) — instantiated once in `main.py` and exposed as the
    `ConfigSchema` context property, alongside `Application`'s `Router` /
    `CoreBridge`.
    """

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._groups = build_config_schema()

    @Property(list, constant=True)
    def groups(self) -> list[dict]:
        return self._groups
