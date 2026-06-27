"""Tests for prokb.chunker — header splitting, code-block safety, merge/split, prefixes."""
from prokb.chunker import (
    estimate_tokens, extract_code_blocks, restore_code_blocks,
    split_by_headers, merge_small_sections, split_large_sections, chunk_markdown,
)


def test_estimate_tokens_roughly_quarter_chars():
    assert estimate_tokens("a" * 400) == 100


def test_code_block_roundtrip():
    text = "before\n```python\nx = 1\n```\nafter"
    cleaned, blocks = extract_code_blocks(text)
    assert "```" not in cleaned
    assert len(blocks) == 1
    assert restore_code_blocks(cleaned, blocks) == text


def test_split_by_headers_intro_and_sections():
    text = "lead paragraph\n\n## First\n\nbody one\n\n### Sub\n\nbody two"
    sections = split_by_headers(text)
    headers = [s["header"] for s in sections]
    assert headers[0] == "Introduction"
    assert "First" in headers
    assert "Sub" in headers


def test_split_by_headers_keeps_hashes_inside_code_blocks():
    # A '## not-a-header' inside a fenced block must not trigger a split.
    text = "## Real Header\n\n```\n## fake header in code\n```\nmore"
    sections = split_by_headers(text)
    assert len(sections) == 1
    assert sections[0]["header"] == "Real Header"
    assert "## fake header in code" in sections[0]["content"]


def test_title_hash_is_not_a_section():
    # A single '#' is the title and should not split off its own section.
    text = "# Title\n\nintro body that is reasonably long " * 3
    sections = split_by_headers(text)
    assert all(s["header"] == "Introduction" for s in sections)


def test_merge_small_sections_combines_tiny_ones():
    sections = [
        {"header": "A", "level": 2, "content": "tiny"},
        {"header": "B", "level": 2, "content": "x " * 400},  # large enough
    ]
    merged = merge_small_sections(sections, min_tokens=50)
    # The tiny 'A' gets folded into the next chunk rather than standing alone.
    assert len(merged) == 1
    assert "A" in merged[0]["header"]
    assert "B" in merged[0]["header"]


def test_split_large_sections_breaks_on_paragraphs():
    big = "\n\n".join([f"paragraph {i} " + "word " * 60 for i in range(10)])
    sections = [{"header": "Big", "level": 2, "content": big}]
    out = split_large_sections(sections, max_tokens=100)
    assert len(out) > 1
    assert all(estimate_tokens(s["content"]) <= 200 for s in out)
    assert any("part" in s["header"] for s in out)


def test_chunk_markdown_adds_metadata_prefix_and_index():
    text = "## Section One\n\n" + ("content line " * 30)
    chunks = chunk_markdown(text, source_file="docs/x.md", date="2026-06-27")
    assert chunks
    first = chunks[0]
    assert first["index"] == 0
    assert "File: docs/x.md" in first["text"]
    assert "Date: 2026-06-27" in first["text"]
    assert "Section:" in first["text"]
    # content field is prefix-free (used for display)
    assert "File: docs/x.md" not in first["content"]


def test_chunk_markdown_empty_input():
    assert chunk_markdown("") == []
