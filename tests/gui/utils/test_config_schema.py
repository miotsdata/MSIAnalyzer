import pytest

from msianalyzer.core.config.config import GROUPS
from msianalyzer.gui.utils.config_schema import _classify, build_config_schema


def test_schema_covers_every_group_except_io():
    schema = build_config_schema()
    keys = [g["key"] for g in schema]

    assert "io" not in keys
    assert set(keys) == set(GROUPS) - {"io"}


def test_schema_field_covers_every_dataclass_field():
    schema = build_config_schema()
    by_key = {g["key"]: g for g in schema}

    assert {f["name"] for f in by_key["peak"]["fields"]} == {
        "peak_height_threshold",
        "filter_mad",
        "filter_mad_log",
        "filter_mad_nmads",
    }


def test_schema_group_has_title():
    schema = build_config_schema()
    io_group = next(g for g in schema if g["key"] == "align")
    assert io_group["title"] == "align all mzs"


@pytest.mark.parametrize(
    "annotation, expected_kind",
    [
        (bool, "bool"),
        (int, "int"),
        (float, "float"),
        (str, "str"),
        (int | None, "optional_int"),
        (float | None, "optional_float"),
        (str | None, "optional_str"),
        (list[str] | None, "optional_str_list"),
        (str | list[str] | None, "path_list"),
    ],
)
def test_classify_known_shapes(annotation, expected_kind):
    assert _classify(annotation) == expected_kind


def test_classify_unknown_shape_raises():
    with pytest.raises(ValueError):
        _classify(dict)


def test_purity_group_classifies_optional_fields():
    schema = build_config_schema()
    purity = next(g for g in schema if g["key"] == "purity")
    fields_by_name = {f["name"]: f for f in purity["fields"]}

    assert fields_by_name["enabled"]["kind"] == "bool"
    assert fields_by_name["max_interpixel_gap_sec"]["kind"] == "optional_float"
    assert fields_by_name["n_workers"]["kind"] == "optional_int"


def test_annotate_group_classifies_library_path_as_path_list():
    schema = build_config_schema()
    annotate = next(g for g in schema if g["key"] == "annotate")
    fields_by_name = {f["name"]: f for f in annotate["fields"]}

    assert fields_by_name["library_path"]["kind"] == "path_list"


def test_align_group_classifies_sample_names_as_optional_str_list():
    schema = build_config_schema()
    align = next(g for g in schema if g["key"] == "align")
    fields_by_name = {f["name"]: f for f in align["fields"]}

    assert fields_by_name["sample_names"]["kind"] == "optional_str_list"


def test_defaults_are_carried_over():
    schema = build_config_schema()
    peak = next(g for g in schema if g["key"] == "peak")
    fields_by_name = {f["name"]: f for f in peak["fields"]}

    assert fields_by_name["filter_mad"]["default"] is True
    assert fields_by_name["filter_mad_nmads"]["default"] == 2.5
