import pytest

from msianalyzer.core.config.config import GROUPS
from msianalyzer.gui.utils.config_schema import (
    ConfigSchemaProvider,
    _classify,
    _parse_docstring,
    _prettify_label,
    build_config_schema,
    build_target_list_schema,
)


def test_schema_covers_every_group_except_io_and_target_list():
    schema = build_config_schema()
    keys = [g["key"] for g in schema]

    assert "io" not in keys
    assert "target_list" not in keys
    assert set(keys) == set(GROUPS) - {"io", "target_list"}


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
    assert fields_by_name["n_workers"]["kind"] == "optional_int"


def test_annotate_group_classifies_min_purity_score_as_optional_float():
    schema = build_config_schema()
    annotate = next(g for g in schema if g["key"] == "annotate")
    fields_by_name = {f["name"]: f for f in annotate["fields"]}

    assert fields_by_name["min_purity_score"]["kind"] == "optional_float"


def test_annotate_group_classifies_library_path_as_path_list():
    schema = build_config_schema()
    annotate = next(g for g in schema if g["key"] == "annotate")
    fields_by_name = {f["name"]: f for f in annotate["fields"]}

    assert fields_by_name["library_path"]["kind"] == "path_list"


def test_align_group_hides_sample_names_from_the_gui():
    # sample_names (metadata={"gui_hidden": True}) stays a real Config
    # field, settable by hand-editing a config file, but a GUI-started run
    # always uses the mzML file names instead.
    schema = build_config_schema()
    align = next(g for g in schema if g["key"] == "align")
    fields_by_name = {f["name"]: f for f in align["fields"]}

    assert "sample_names" not in fields_by_name


def test_defaults_are_carried_over():
    schema = build_config_schema()
    peak = next(g for g in schema if g["key"] == "peak")
    fields_by_name = {f["name"]: f for f in peak["fields"]}

    assert fields_by_name["filter_mad"]["default"] is True
    assert fields_by_name["filter_mad_nmads"]["default"] == 2.5


@pytest.mark.parametrize(
    "name, expected_label",
    [
        ("peak_height_threshold", "Peak height threshold"),
        ("align_ppm", "Align PPM"),
        ("mz_decimals", "M/z decimals"),
        ("filter_mad", "Filter MAD"),
        ("filter_mad_nmads", "Number of MADs"),
        ("n_workers", "Number of worker processes"),
        ("enabled", "Enabled"),
    ],
)
def test_prettify_label(name, expected_label):
    assert _prettify_label(name) == expected_label


def test_parse_docstring_extracts_first_paragraph_only_as_summary():
    class Dummy:
        """First sentence of the summary.

        A second explanatory paragraph that should NOT end up in the
        summary — only the first paragraph does.

        Attributes:
            foo: Help text for foo.
            bar: Help text for bar,
                continued on a second line.
        """

        foo: int
        bar: int

    summary, attrs = _parse_docstring(Dummy)

    assert summary == "First sentence of the summary."
    assert attrs["foo"] == "Help text for foo."
    assert attrs["bar"] == "Help text for bar, continued on a second line."


def test_parse_docstring_preserves_blank_line_paragraph_breaks():
    class Dummy:
        """Summary.

        Attributes:
            foo: General description of foo.

                `formula = a + b`

                Range: **0**-**1**. Near **0**: does nothing. Near **1**:
                does everything.

                **Interaction:** None.
            bar: Single-paragraph field, unaffected by the change.
        """

        foo: int
        bar: int

    _, attrs = _parse_docstring(Dummy)

    assert attrs["foo"] == (
        "General description of foo.\n\n"
        "`formula = a + b`\n\n"
        "Range: **0**-**1**. Near **0**: does nothing. Near **1**: "
        "does everything.\n\n"
        "**Interaction:** None."
    )
    assert attrs["bar"] == "Single-paragraph field, unaffected by the change."


def test_parse_docstring_handles_missing_attributes_section():
    class Dummy:
        """Just a summary, no Attributes section."""

    summary, attrs = _parse_docstring(Dummy)

    assert summary == "Just a summary, no Attributes section."
    assert attrs == {}


def test_every_group_has_a_non_empty_description():
    schema = build_config_schema()
    for group in schema:
        assert group["description"] != "", group["key"]


def test_every_field_has_help_text():
    schema = build_config_schema()
    for group in schema:
        for f in group["fields"]:
            assert f["help"] != "", f"{group['key']}.{f['name']}"


def test_peak_group_encodes_filter_mad_dependency():
    schema = build_config_schema()
    peak = next(g for g in schema if g["key"] == "peak")
    fields_by_name = {f["name"]: f for f in peak["fields"]}

    assert fields_by_name["filter_mad"]["enabledWhenField"] is None
    assert fields_by_name["filter_mad_log"]["enabledWhenField"] == "filter_mad"
    assert fields_by_name["filter_mad_log"]["enabledWhenEquals"] is True
    assert fields_by_name["filter_mad_nmads"]["enabledWhenField"] == "filter_mad"
    assert fields_by_name["filter_mad_nmads"]["enabledWhenEquals"] is True
    assert fields_by_name["peak_height_threshold"]["enabledWhenField"] == "filter_mad"
    assert fields_by_name["peak_height_threshold"]["enabledWhenEquals"] is False


def test_peak_group_field_order_has_filter_mad_options_before_height_threshold():
    schema = build_config_schema()
    peak = next(g for g in schema if g["key"] == "peak")
    names = [f["name"] for f in peak["fields"]]

    assert names.index("peak_height_threshold") == len(names) - 1
    assert names.index("filter_mad") < names.index("filter_mad_log")
    assert names.index("filter_mad") < names.index("filter_mad_nmads")


def test_build_target_list_schema_covers_paths_polarity_and_match_ppm():
    schema = build_target_list_schema()

    assert schema["description"] != ""
    assert set(schema["fields"]) == {"paths", "polarity", "match_ppm"}
    for field in schema["fields"].values():
        assert field["label"] != ""
        assert field["help"] != ""
    assert schema["fields"]["polarity"]["default"] == "positive"
    assert schema["fields"]["match_ppm"]["default"] == 10.0
    assert schema["fields"]["paths"]["default"] is None


def test_build_target_list_schema_excludes_adducts():
    # adducts is a multi-select, covered separately by
    # ConfigSchemaProvider.positiveAdducts/negativeAdducts.
    schema = build_target_list_schema()
    assert "adducts" not in schema["fields"]


def test_config_schema_provider_exposes_polarity_filtered_adduct_lists():
    provider = ConfigSchemaProvider()

    assert "[M+H]+" in provider.positiveAdducts
    assert "[M-H]-" in provider.negativeAdducts
    assert not set(provider.positiveAdducts) & set(provider.negativeAdducts)


def test_config_schema_provider_target_list_schema_matches_module_function():
    provider = ConfigSchemaProvider()
    assert provider.targetListSchema == build_target_list_schema()
