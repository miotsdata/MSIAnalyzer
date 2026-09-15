"""Data-driven form schema for `msianalyzer.core.config.config.Config`.

`NewAnalysisPage.qml` needs one tab per config group, with a control per
field. Rather than hand-writing ~70 near-identical QML field blocks, this
module introspects the `Config` dataclasses themselves and produces a plain,
QML-friendly schema the page renders generically. `io` (`IOConfig`) and
`target_list` (`TargetListConfig`) are excluded — `io` gets a bespoke tab
(sample-pair pickers, output folder); `target_list` gets a bespoke tab
because its `adducts` field needs a polarity-filtered multi-select control,
not the single-control-per-field shape this generic schema produces (see
`ConfigSchemaProvider.positiveAdducts`/`negativeAdducts` below).
"""

from __future__ import annotations

import dataclasses
import inspect
import re
import types
import typing

from PySide6.QtCore import Property, QObject

from msianalyzer.core.annotation.target_list import adducts_for_polarity
from msianalyzer.core.config.config import GROUPS, GROUP_TITLES, TargetListConfig

_BESPOKE_GROUPS = ("io", "target_list")

_UNION_ORIGINS = (typing.Union, types.UnionType)

#: Kept uppercase (or as otherwise spelled here) when turning a
#: snake_case field name into a display label — plain title-casing would
#: give "Ppm"/"Mad"/"Xml" instead of the abbreviations users actually
#: recognize.
_LABEL_WORD_OVERRIDES = {
    "ppm": "PPM",
    "ms1": "MS1",
    "ms2": "MS2",
    "mad": "MAD",
    "xml": "XML",
    "db": "DB",
    "id": "ID",
    "mz": "m/z",
    "mzs": "m/z",
    "tic": "TIC",
    "da": "Da",
    "n": "N",
}

#: A handful of field names read awkwardly even with word-level overrides
#: (compound abbreviations, hidden meaning) — spelled out by hand instead.
_LABEL_NAME_OVERRIDES = {
    "filter_mad_nmads": "Number of MADs",
    "n_workers": "Number of worker processes",
}


def _prettify_label(name: str) -> str:
    """A snake_case field name as a human-readable label.

    `"peak_height_threshold"` -> `"Peak height threshold"`,
    `"align_ppm"` -> `"Align PPM"`.
    """
    if name in _LABEL_NAME_OVERRIDES:
        return _LABEL_NAME_OVERRIDES[name]
    words = name.split("_")
    parts = [_LABEL_WORD_OVERRIDES.get(w.lower(), w.lower()) for w in words]
    parts[0] = parts[0][:1].upper() + parts[0][1:] if parts[0].islower() else parts[0]
    return " ".join(parts)


_ATTR_LINE = re.compile(r"^(\w+):\s?(.*)$")


def _parse_docstring(cls: type) -> tuple[str, dict[str, str]]:
    """Pull a one-paragraph summary and per-attribute help text out of a
    `Config` dataclass's Google-style docstring.

    Every `Config` dataclass in `core/config/config.py` documents its
    fields Google-style — a summary paragraph, then an `Attributes:`
    section with one `name: description` entry per field. Reusing it here
    means field help text lives in exactly one place (the dataclass
    itself) instead of being duplicated for the GUI.

    Args:
        cls: A dataclass whose docstring follows that convention.

    Returns:
        `(summary, {field_name: help_text})`. `summary` is the first
        paragraph only (any further explanatory paragraphs before
        `Attributes:` are dropped — the GUI wants one descriptive
        sentence, not the full docstring). Fields the docstring doesn't
        mention are simply absent from the dict.
    """
    doc = inspect.getdoc(cls) or ""
    before, _, after = doc.partition("Attributes:")
    summary = before.strip().split("\n\n", 1)[0]
    summary = " ".join(line.strip() for line in summary.splitlines())

    attrs: dict[str, str] = {}
    current_name: str | None = None
    current_lines: list[str] = []
    for line in after.splitlines():
        if not line.strip():
            continue
        stripped = line.lstrip()
        indent = len(line) - len(stripped)
        match = _ATTR_LINE.match(stripped) if indent <= 4 else None
        if match:
            if current_name is not None:
                attrs[current_name] = " ".join(current_lines).strip()
            current_name, first_line = match.group(1), match.group(2)
            current_lines = [first_line] if first_line else []
        elif current_name is not None:
            current_lines.append(stripped)
    if current_name is not None:
        attrs[current_name] = " ".join(current_lines).strip()
    return summary, attrs


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
    """Build the form schema for every `Config` group except the bespoke ones.

    `io` and `target_list` are excluded (see module docstring) — both get
    hand-written QML tabs instead of a generic, row-per-field rendering.

    Returns:
        One entry per group, in `GROUPS` order: `{"key", "title",
        "description", "fields"}`, where `fields` is a list of `{"name",
        "label", "kind", "default", "help", "enabledWhenField",
        "enabledWhenEquals"}`. `description` is the group dataclass's
        docstring summary; `help` is that field's docstring text (empty
        string if undocumented); `label` is `name` prettified for display.
        `enabledWhenField`/`enabledWhenEquals` come from that field's
        `dataclasses.field(metadata={"enabled_when": "..."})` (a bare
        field name, or `"not "` + a field name) — `None` when the field
        isn't conditionally enabled. The referenced field is always a
        `bool` and always declared earlier in the same group, so a
        renderer creating controls in field order can look it up by the
        time it needs to.

        A field declared with `dataclasses.field(metadata={"gui_hidden":
        True})` is skipped entirely — it stays a normal `Config` field
        (settable by hand-editing or scripting a config file, and still
        round-trips through `to_dict`/`from_dict`), just not offered as a
        control in the GUI's New Analysis wizard. Use this for a field
        whose GUI-appropriate value is always the default, or that only
        makes sense when set programmatically (e.g.
        `AlignMzSamples.sample_names`).
    """
    schema: list[dict] = []
    for group_key, group_type in GROUPS.items():
        if group_key in _BESPOKE_GROUPS:
            continue

        group_summary, field_help = _parse_docstring(group_type)
        hints = typing.get_type_hints(group_type)
        fields = []
        for f in dataclasses.fields(group_type):
            if f.metadata.get("gui_hidden"):
                continue
            enabled_when = f.metadata.get("enabled_when")
            enabled_when_field = enabled_when
            enabled_when_equals = True
            if enabled_when is not None and enabled_when.startswith("not "):
                enabled_when_field = enabled_when[4:]
                enabled_when_equals = False
            fields.append(
                {
                    "name": f.name,
                    "label": _prettify_label(f.name),
                    "kind": _classify(hints[f.name]),
                    "default": f.default
                    if f.default is not dataclasses.MISSING
                    else None,
                    "help": field_help.get(f.name, ""),
                    "enabledWhenField": enabled_when_field,
                    "enabledWhenEquals": enabled_when_equals,
                }
            )
        schema.append(
            {
                "key": group_key,
                "title": GROUP_TITLES[group_key],
                "description": group_summary,
                "fields": fields,
            }
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


def build_target_list_schema() -> dict:
    """Build the bespoke form metadata for `TargetListConfig`.

    Unlike every other group, `target_list`'s tab is hand-written QML (see
    module docstring), but its labels/help text/defaults still come from
    `TargetListConfig`'s docstring and field defaults, same as the generic
    groups — only the *rendering* is bespoke, not where the text lives.

    Returns:
        `{"description", "fields": {name: {"label", "help", "default"}}}`
        for `paths`/`polarity`/`match_ppm` (`adducts` is covered separately
        by `ConfigSchemaProvider.positiveAdducts`/`negativeAdducts`, since
        it's a multi-select rather than a single control).
    """
    summary, field_help = _parse_docstring(TargetListConfig)
    fields = {}
    for f in dataclasses.fields(TargetListConfig):
        if f.name == "adducts":
            continue
        fields[f.name] = {
            "label": _prettify_label(f.name),
            "help": field_help.get(f.name, ""),
            "default": f.default if f.default is not dataclasses.MISSING else None,
        }
    return {"description": summary, "fields": fields}


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
        self._target_list_schema = build_target_list_schema()
        self._positive_adducts = [a.label for a in adducts_for_polarity("positive")]
        self._negative_adducts = [a.label for a in adducts_for_polarity("negative")]

    @Property(list, constant=True)
    def groups(self) -> list[dict]:
        return self._groups

    @Property(dict, constant=True)
    def targetListSchema(self) -> dict:
        return self._target_list_schema

    @Property(list, constant=True)
    def positiveAdducts(self) -> list[str]:
        return self._positive_adducts

    @Property(list, constant=True)
    def negativeAdducts(self) -> list[str]:
        return self._negative_adducts
