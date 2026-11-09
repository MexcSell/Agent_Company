"""mcp.slugify: str input becomes a short slug, falsy input falls back, other non-str input fails loudly."""

import pytest

from agent_company.mcp import slugify


def test_str_input_becomes_a_short_slug() -> None:
    assert slugify("Hello, World!") == "hello_world"
    assert slugify("__ab__") == "ab"
    assert slugify("A very long server name here") == "a_very_long_serv"
    # the cut lands on a '_', which is stripped again
    assert slugify("abcdefghijklmno x") == "abcdefghijklmno"
    for name in ("Hello, World!", "A very long server name here", "abcdefghijklmno x"):
        out = slugify(name)
        assert len(out) <= 16 and not out.startswith("_") and not out.endswith("_")
    assert slugify("!!!") == "server"


@pytest.mark.parametrize("name", [None, "", 0, False])
def test_falsy_input_falls_back_to_server(name) -> None:  # noqa: ANN001
    # Intentional fallback: save_server (mcp.py:354) relies on slugify never returning an empty slug.
    assert slugify(name) == "server"


# A guard quietly returning '' or 'server' here is a behaviour change and must update this test on purpose.
@pytest.mark.parametrize("name", [123, ["a"]])
def test_truthy_non_str_raises_attribute_error(name) -> None:  # noqa: ANN001
    with pytest.raises(AttributeError):
        slugify(name)


def test_bytes_raise_type_error() -> None:
    with pytest.raises(TypeError):
        slugify(b"x")
