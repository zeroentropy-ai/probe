"""Smart chunking by file type."""

from __future__ import annotations

import re
import uuid

import tiktoken

from probe.models import Chunk

_enc = tiktoken.get_encoding("cl100k_base")


def _count_tokens(text: str) -> int:
    return len(_enc.encode(text))


def _line_range(source_text: str, char_start: int, char_end: int) -> tuple[int, int]:
    bounded_start = max(0, min(char_start, len(source_text)))
    bounded_end = max(bounded_start, min(char_end, len(source_text)))
    line_start = source_text.count("\n", 0, bounded_start) + 1
    end_index = max(bounded_start, bounded_end - 1)
    line_end = source_text.count("\n", 0, end_index) + 1
    return line_start, line_end


def _make_chunk(content, file_path, file_type, chunk_index, char_start,
                source_text=None, header_path=None, symbol_name=None,
                page_number=None) -> Chunk:
    line_start = line_end = None
    if source_text is not None:
        line_start, line_end = _line_range(source_text, char_start, char_start + len(content))
    return Chunk(
        id=str(uuid.uuid4()), file_path=file_path, file_type=file_type,
        chunk_index=chunk_index, content=content,
        char_start=char_start, char_end=char_start + len(content),
        token_count=_count_tokens(content),
        header_path=header_path, symbol_name=symbol_name, page_number=page_number,
        line_start=line_start, line_end=line_end,
    )


def chunk_markdown(content: str, file_path: str) -> list[Chunk]:
    header_pattern = re.compile(r"^(#{1,6})\s+(.+)$", re.MULTILINE)
    matches = list(header_pattern.finditer(content))

    if not matches:
        text = content.strip()
        if text:
            return [_make_chunk(text, file_path, "markdown", 0, 0, source_text=content)]
        return []

    chunks: list[Chunk] = []
    header_stack: list[tuple[int, str]] = []

    pre = content[: matches[0].start()].strip()
    if pre:
        chunks.append(_make_chunk(pre, file_path, "markdown", len(chunks), 0,
                                  source_text=content))

    for i, match in enumerate(matches):
        level = len(match.group(1))
        title = match.group(2).strip()

        while header_stack and header_stack[-1][0] >= level:
            header_stack.pop()
        header_stack.append((level, title))
        header_path = " > ".join(h[1] for h in header_stack)

        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[start:end].strip()

        if not section:
            continue

        if len(section) > 2000:
            paragraphs = re.split(r"\n\n+", section)
            current = ""
            for para in paragraphs:
                if current and len(current) + len(para) > 1800:
                    chunks.append(_make_chunk(
                        current.strip(), file_path, "markdown",
                        len(chunks), start, source_text=content, header_path=header_path,
                    ))
                    current = para
                else:
                    current = f"{current}\n\n{para}" if current else para
            if current.strip():
                chunks.append(_make_chunk(
                    current.strip(), file_path, "markdown",
                    len(chunks), start, source_text=content, header_path=header_path,
                ))
        else:
            chunks.append(_make_chunk(
                section, file_path, "markdown",
                len(chunks), start, source_text=content, header_path=header_path,
            ))

    return chunks


def chunk_code(content: str, file_path: str) -> list[Chunk]:
    pattern = re.compile(
        r"^(?:(?:export\s+)?(?:async\s+)?(?:def|function|class)\s+\w+|"
        r"(?:pub\s+)?(?:fn|struct|impl|enum)\s+\w+|"
        r"(?:func)\s+\w+)",
        re.MULTILINE,
    )
    matches = list(pattern.finditer(content))

    if not matches:
        return _sliding_window(content, file_path, "code", source_text=content)

    chunks: list[Chunk] = []

    pre = content[: matches[0].start()].strip()
    if pre and len(pre) > 20:
        chunks.append(_make_chunk(pre, file_path, "code", len(chunks), 0,
                                  source_text=content))

    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
        section = content[start:end].strip()

        if not section:
            continue

        symbol_match = re.match(
            r"(?:export\s+)?(?:async\s+)?(?:pub\s+)?(?:def|function|class|fn|struct|impl|enum|func)\s+(\w+)",
            section,
        )
        symbol_name = symbol_match.group(1) if symbol_match else None

        if len(section) > 2000:
            sub_chunks = _sliding_window(
                section, file_path, "code", symbol_name=symbol_name,
                source_text=content, base_offset=start,
            )
            for sc in sub_chunks:
                sc.chunk_index = len(chunks)
                chunks.append(sc)
        else:
            chunks.append(_make_chunk(
                section, file_path, "code", len(chunks),
                start, source_text=content, symbol_name=symbol_name,
            ))

    return chunks


def chunk_pdf(content: str, file_path: str) -> list[Chunk]:
    pages = content.split("--- PAGE BREAK ---")
    chunks = []
    char_offset = 0
    for i, page in enumerate(pages):
        text = page.strip()
        if text:
            chunks.append(_make_chunk(
                text, file_path, "pdf", len(chunks),
                char_offset, source_text=content, page_number=i + 1,
            ))
        char_offset += len(page) + len("--- PAGE BREAK ---")
    return chunks


def chunk_text(content: str, file_path: str) -> list[Chunk]:
    paragraphs = re.split(r"\n\n+", content)
    chunks: list[Chunk] = []
    current = ""
    char_offset = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current and len(current) + len(para) > 1500:
            chunks.append(_make_chunk(current, file_path, "text", len(chunks), char_offset,
                                      source_text=content))
            char_offset += len(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para

    if current.strip():
        chunks.append(_make_chunk(current, file_path, "text", len(chunks), char_offset,
                                  source_text=content))
    return chunks


def _sliding_window(content, file_path, file_type, window_size=1500, overlap=200,
                    symbol_name=None, source_text=None, base_offset=0):
    chunks: list[Chunk] = []
    start = 0
    while start < len(content):
        end = min(start + window_size, len(content))
        text = content[start:end]
        if text.strip():
            chunks.append(_make_chunk(
                text, file_path, file_type, len(chunks),
                base_offset + start, source_text=source_text or content,
                symbol_name=symbol_name,
            ))
        if end >= len(content):
            break
        start = end - overlap
    return chunks


def chunk_content(content: str, file_path: str, file_type: str) -> list[Chunk]:
    if file_type == "markdown":
        return chunk_markdown(content, file_path)
    elif file_type == "code":
        return chunk_code(content, file_path)
    elif file_type == "pdf":
        return chunk_pdf(content, file_path)
    else:
        return chunk_text(content, file_path)
