#!/usr/bin/env python3
"""Markdown-aware semantic chunker.

Splits markdown files by section headers (## and ###), keeping tables and
code blocks intact. Each chunk gets a metadata prefix for better embedding.

Usage (standalone test):
    python knowledge/chunker.py path/to/file.md
"""

import re
import sys
from pathlib import Path


def estimate_tokens(text):
    """Rough token estimate: ~4 chars per token for English text."""
    return len(text) // 4


def extract_code_blocks(text):
    """Replace code blocks with placeholders, return mapping."""
    blocks = {}
    counter = [0]

    def replacer(match):
        key = f"__CODEBLOCK_{counter[0]}__"
        blocks[key] = match.group(0)
        counter[0] += 1
        return key

    cleaned = re.sub(r"```[\s\S]*?```", replacer, text)
    return cleaned, blocks


def restore_code_blocks(text, blocks):
    """Restore code blocks from placeholders."""
    for key, block in blocks.items():
        text = text.replace(key, block)
    return text


def split_by_headers(text):
    """Split markdown into sections by ## and ### headers.

    Returns list of (header_chain, content) tuples.
    The first section (before any header) gets header "Introduction".
    """
    # Protect code blocks from header splitting
    text_clean, code_blocks = extract_code_blocks(text)

    # Split on ## and ### headers (not # — that's the title)
    # Pattern matches lines starting with ## or ###
    parts = re.split(r"(?=^#{2,3}\s+)", text_clean, flags=re.MULTILINE)

    sections = []
    for part in parts:
        part = part.strip()
        if not part:
            continue

        # Extract header if present
        header_match = re.match(r"^(#{2,3})\s+(.+?)$", part, re.MULTILINE)
        if header_match:
            level = len(header_match.group(1))
            header = header_match.group(2).strip()
            content = part[header_match.end():].strip()
        else:
            header = "Introduction"
            level = 0
            content = part

        # Restore code blocks
        content = restore_code_blocks(content, code_blocks)
        header = restore_code_blocks(header, code_blocks)

        sections.append({
            "header": header,
            "level": level,
            "content": content,
        })

    return sections


def merge_small_sections(sections, min_tokens):
    """Merge sections that are too small with the next section."""
    if not sections:
        return sections

    merged = []
    buffer = None

    for section in sections:
        tokens = estimate_tokens(section["content"])

        if buffer is not None:
            # Merge into buffer
            buffer["content"] += f"\n\n### {section['header']}\n\n{section['content']}"
            buffer["header"] += f" > {section['header']}"

            if estimate_tokens(buffer["content"]) >= min_tokens:
                merged.append(buffer)
                buffer = None
        elif tokens < min_tokens:
            buffer = dict(section)  # Start buffering
        else:
            merged.append(section)

    if buffer is not None:
        if merged:
            # Merge remaining buffer into last section
            merged[-1]["content"] += f"\n\n### {buffer['header']}\n\n{buffer['content']}"
            merged[-1]["header"] += f" > {buffer['header']}"
        else:
            merged.append(buffer)

    return merged


def split_large_sections(sections, max_tokens):
    """Split sections that are too large on paragraph boundaries."""
    result = []

    for section in sections:
        tokens = estimate_tokens(section["content"])

        if tokens <= max_tokens:
            result.append(section)
            continue

        # Split on double newlines (paragraphs)
        paragraphs = re.split(r"\n\n+", section["content"])
        current_chunk = ""
        part_idx = 0

        for para in paragraphs:
            # Check if adding this paragraph would exceed limit
            if current_chunk and estimate_tokens(current_chunk + "\n\n" + para) > max_tokens:
                result.append({
                    "header": section["header"] + (f" (part {part_idx + 1})" if part_idx > 0 else ""),
                    "level": section["level"],
                    "content": current_chunk.strip(),
                })
                current_chunk = para
                part_idx += 1
            else:
                current_chunk = (current_chunk + "\n\n" + para) if current_chunk else para

        if current_chunk.strip():
            result.append({
                "header": section["header"] + (f" (part {part_idx + 1})" if part_idx > 0 else ""),
                "level": section["level"],
                "content": current_chunk.strip(),
            })

    return result


def chunk_markdown(text, source_file="", date="", max_tokens=1500, min_tokens=50):
    """Chunk a markdown file into sections with metadata prefixes.

    Returns list of dicts:
        {
            "text": str,        # Full text including metadata prefix (for embedding)
            "content": str,     # Raw content without prefix (for display)
            "section": str,     # Section header chain
            "index": int,       # Chunk index within file
        }
    """
    sections = split_by_headers(text)
    sections = merge_small_sections(sections, min_tokens)
    sections = split_large_sections(sections, max_tokens)

    chunks = []
    for i, section in enumerate(sections):
        content = section["content"]
        header = section["header"]

        # Build metadata prefix for embedding context
        prefix_parts = [f"File: {source_file}"]
        if date:
            prefix_parts.append(f"Date: {date}")
        prefix_parts.append(f"Section: {header}")
        prefix = " | ".join(prefix_parts)

        # Full text = prefix + content (this gets embedded)
        full_text = f"{prefix}\n\n{content}"

        chunks.append({
            "text": full_text,
            "content": content,
            "section": header,
            "index": i,
        })

    return chunks


def main():
    """Test chunker on a file."""
    if len(sys.argv) < 2:
        print("Usage: python knowledge/chunker.py <file.md>")
        sys.exit(1)

    filepath = sys.argv[1]
    with open(filepath, "r", encoding="utf-8") as f:
        text = f.read()

    chunks = chunk_markdown(text, source_file=filepath)

    print(f"File: {filepath}")
    print(f"Chunks: {len(chunks)}")
    print("---")
    for chunk in chunks:
        tokens = estimate_tokens(chunk["text"])
        preview = chunk["content"][:200].replace("\n", " ")
        print(f"  [{chunk['index']}] {chunk['section']} ({tokens} tokens)")
        print(f"      {preview}...")
        print()


if __name__ == "__main__":
    main()
