import pytest
from pydantic import ValidationError

from dailytimes.blocks import (
    MAX_BLOCKS,
    Brief,
    canonical_url,
    fingerprint,
    parse_plaintext,
    block_texts,
    short_link,
    source_name,
)


def brief(*blocks, **kw):
    return Brief.model_validate({"blocks": list(blocks), **kw})


def test_sample_is_valid(sample_brief):
    assert sample_brief.title == "Sample edition"
    assert {b.type for b in sample_brief.blocks} == {"heading", "row", "text", "divider", "bullet", "qr", "small"}


@pytest.mark.parametrize(
    "block",
    [
        {"type": "heading", "text": "x", "colour": "red"},  # unknown field
        {"type": "banner", "text": "x"},  # unknown type
        {"type": "heading", "text": ""},
        {"type": "heading", "text": "   "},
        {"type": "heading", "text": "two\nlines"},
        {"type": "heading", "text": "x" * 81},
        {"type": "heading", "text": "x", "level": 3},
        {"type": "text", "text": "bell\x07"},
        {"type": "text", "text": "x", "url": "ftp://example.com/file"},
        {"type": "text", "text": "x", "url": "example.com/no-scheme"},
        {"type": "text", "text": "x", "url": "https://localhost/x"},
        {"type": "text", "text": "x", "url": "https://ex.com/a b"},
        {"type": "bullet", "text": "x" * 301},
        {"type": "row", "left": "x"},
        {"type": "row", "left": "x", "right": "y" * 41},
        {"type": "qr"},
        {"type": "qr", "url": "https://ex.com/" + "a" * 2100},
        {"type": "divider", "text": "x"},
        {"type": "small", "text": ""},
    ],
)
def test_rejects_bad_blocks(block):
    with pytest.raises(ValidationError):
        brief({"type": "text", "text": "ok"}, block)


def test_rejects_empty_and_content_free_briefs():
    with pytest.raises(ValidationError):
        Brief.model_validate({"blocks": []})
    with pytest.raises(ValidationError, match="no content"):
        brief({"type": "heading", "text": "Only a heading"}, {"type": "divider"})


def test_rejects_too_many_blocks_and_too_long():
    with pytest.raises(ValidationError):
        brief(*[{"type": "bullet", "text": "x"}] * (MAX_BLOCKS + 1))
    with pytest.raises(ValidationError, match="keep it under"):
        brief(*[{"type": "text", "text": "y" * 600}] * 11)


def test_rejects_unknown_top_level_and_bad_date():
    with pytest.raises(ValidationError):
        brief({"type": "text", "text": "x"}, author="me")
    with pytest.raises(ValidationError):
        brief({"type": "text", "text": "x"}, date="tomorrow")


def test_whitespace_is_collapsed():
    b = brief({"type": "text", "text": "  two\n lines   here "})
    assert b.blocks[0].text == "two lines here"


def test_content_hash_stable_and_sensitive():
    a = brief({"type": "text", "text": "x"})
    assert a.content_hash() == brief({"type": "text", "text": "x"}).content_hash()
    assert a.content_hash() != brief({"type": "text", "text": "y"}).content_hash()


def test_canonical_url_and_fingerprint():
    assert canonical_url("http://WWW.Example.com/a/?utm_source=x&b=2#frag") == "https://example.com/a?b=2"
    assert canonical_url("https://old.reddit.com/r/x/") == canonical_url("https://reddit.com/r/x")
    b = brief({"type": "bullet", "text": "x", "url": "https://ex.com/p?utm_medium=m"})
    assert fingerprint(b.blocks[0]) == "https://ex.com/p"
    assert fingerprint(brief({"type": "text", "text": "no link"}).blocks[0]) is None


def test_short_link():
    assert short_link("https://www.example.com/path/") == "example.com/path"
    s = short_link("https://example.com/" + "x" * 100, limit=30)
    assert len(s) == 30 and s.endswith("…")


def test_parse_plaintext():
    b = parse_plaintext(
        "# Today\n09:30 Standup | Zoom\n\n- Ship relay https://ex.com/relay\n- Plain item\n---\n"
        "Paragraph one\ncontinues.\n\nqr: https://ex.com/q Scan me\nsmall: fine print",
        title="Mail",
    )
    types = [x.type for x in b.blocks]
    assert types == ["heading", "row", "bullet", "bullet", "divider", "text", "qr", "small"]
    assert b.blocks[2].url == "https://ex.com/relay" and b.blocks[2].text == "Ship relay"
    assert b.blocks[5].text == "Paragraph one continues."
    assert b.blocks[6].caption == "Scan me"
    assert b.title == "Mail"


def test_lead_is_optional_single_line_and_counted():
    b = Brief.model_validate({"blocks": [{"type": "bullet", "lead": "  Meta  Q3 ", "text": "Revenue down."}]})
    assert b.blocks[0].lead == "Meta Q3"
    for bad in ("two\nlines", "x" * 41):
        with pytest.raises(ValidationError):
            Brief.model_validate({"blocks": [{"type": "text", "lead": bad, "text": "Body."}]})
    assert block_texts(b.blocks[0]) == ["Meta Q3", "Revenue down."]


def test_source_name_is_the_domain():
    assert source_name("https://www.roadtovr.com/meta-declining-quest-sales/") == "roadtovr.com"
    assert source_name("https://news.google.com/rss/articles/CBMiABC?oc=5") == "via Google News"
