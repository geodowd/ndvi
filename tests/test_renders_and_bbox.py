import pytest

from bbox import parse_bbox
from conftest import write_raster
from renders import fallback_render, remap_source_renders


def test_remap_keeps_only_renders_for_the_source_asset():
    source = {
        "tc": {"assets": ["cog"], "bidx": [3, 2, 1], "rescale": [[0, 3000]] * 3},
        "index": {"assets": ["cog"], "expression": "(cog_b7-cog_b3)/(cog_b7+cog_b3)", "rescale": [[-1, 1]]},
        "mixed": {"assets": ["cog", "thumbnail"], "rescale": [[0, 1]]},
    }
    remapped = remap_source_renders(source, "cog")

    assert set(remapped) == {"tc", "index"}
    assert remapped["tc"]["assets"] == ["data"]
    assert remapped["index"]["expression"] == "(data_b7-data_b3)/(data_b7+data_b3)"
    assert source["tc"]["assets"] == ["cog"]  # source not mutated


def test_remap_handles_missing_renders():
    assert remap_source_renders(None, "cog") == {}


def test_fallback_render_is_greyscale_without_rgb_common_names(tmp_path):
    cog = write_raster(tmp_path / "single.tif", [1000])
    render = fallback_render(str(cog), [None])

    assert list(render) == ["greyscale"]
    low, high = render["greyscale"]["rescale"][0]
    # Nodata (0) is excluded, so the stretch sits within the 1000-1100 gradient.
    assert 1000 <= low < high <= 1100


@pytest.mark.parametrize(
    "value, expected",
    [
        ("-3.6,51.5,-3.5,51.6", (-3.6, 51.5, -3.5, 51.6)),
        ("-3.6 51.5 -3.5 51.6", (-3.6, 51.5, -3.5, 51.6)),
        (
            '{"type": "Feature", "geometry": {"type": "Polygon", "coordinates": '
            "[[[-3.6, 51.5], [-3.5, 51.5], [-3.5, 51.6], [-3.6, 51.6], [-3.6, 51.5]]]}}",
            (-3.6, 51.5, -3.5, 51.6),
        ),
        ("null", None),
        ("", None),
    ],
)
def test_parse_bbox(value, expected):
    assert parse_bbox(value) == expected


def test_parse_bbox_rejects_inverted_bbox():
    with pytest.raises(ValueError, match="xmin must be less than xmax"):
        parse_bbox("-3.5,51.5,-3.6,51.6")
