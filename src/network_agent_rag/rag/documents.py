"""Document loading and structure-aware chunking."""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
import re

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


_SUPPORTED_SUFFIXES = {".md", ".markdown", ".pdf", ".txt"}
_ATX_HEADING = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_SETEXT_UNDERLINE = re.compile(r"^\s*(=+|-+)\s*$")
_FENCE = re.compile(r"^\s*(`{3,}|~{3,})")
_NUMBERED_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)*)[.)]?\s+(.+?)\s*$")
_CHINESE_HEADING = re.compile(r"^\s*[一二三四五六七八九十百]+、\s*(.+?)\s*$")
_CLI_LINE = re.compile(
    r"^\s*(?:[$>]\s+\S|(?:[\w.-]+@)?[\w.-]+(?:\([^)]+\))?[#>$]\s*\S|\t\S| {4}\S)"
)
_SEPARATORS = [
    "\n\n",
    "\n",
    "。",
    "！",
    "？",
    ".",
    "!",
    "?",
    "；",
    ";",
    "，",
    ",",
    " ",
    "",
]


def load_documents(
    sources: str | Path | Sequence[str | Path],
    *,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> list[Document]:
    """Load supported files and return structure-aware chunks."""

    if chunk_size <= 0:
        raise ValueError("chunk_size must be greater than zero")
    if chunk_overlap < 0 or chunk_overlap >= chunk_size:
        raise ValueError("chunk_overlap must be between zero and chunk_size")

    paths = _discover_paths(sources)
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=_SEPARATORS,
        length_function=len,
    )
    chunks: list[Document] = []

    for path in paths:
        source_chunks: list[Document] = []
        current_title_path = (path.stem,)
        for text, extra_metadata in _read_source(path):
            metadata = {
                "source": str(path),
                "file_type": path.suffix.lower().lstrip("."),
                **extra_metadata,
            }
            page_chunks, current_title_path = _chunk_text(
                text,
                path.stem,
                metadata,
                splitter,
                current_title_path,
            )
            source_chunks.extend(page_chunks)
        for chunk_index, chunk in enumerate(source_chunks):
            chunk.metadata["chunk_index"] = chunk_index
        chunks.extend(source_chunks)

    if not chunks:
        raise ValueError("No extractable text found in the supplied sources")
    return chunks


def _discover_paths(sources: str | Path | Sequence[str | Path]) -> list[Path]:
    raw_sources = [sources] if isinstance(sources, (str, Path)) else list(sources)
    if not raw_sources:
        raise ValueError("At least one source path is required")

    discovered: set[Path] = set()
    for source in raw_sources:
        path = Path(source).expanduser().resolve()
        if not path.exists():
            raise FileNotFoundError(path)
        if path.is_file():
            if path.suffix.lower() not in _SUPPORTED_SUFFIXES:
                raise ValueError(f"Unsupported document type: {path.suffix or '<none>'}")
            discovered.add(path)
            continue
        discovered.update(
            candidate.resolve()
            for candidate in path.rglob("*")
            if candidate.is_file()
            and candidate.suffix.lower() in _SUPPORTED_SUFFIXES
        )

    if not discovered:
        raise ValueError("No supported documents found")
    return sorted(discovered, key=lambda item: item.as_posix().casefold())


def _read_source(path: Path) -> list[tuple[str, dict[str, int]]]:
    if path.suffix.lower() == ".pdf":
        import pymupdf4llm

        pages = pymupdf4llm.to_markdown(
            str(path),
            page_chunks=True,
            use_ocr=False,
            force_ocr=False,
            show_progress=False,
        )
        extracted = [
            (page["text"], {"page": page_number})
            for page_number, page in enumerate(pages, start=1)
            if page.get("text", "").strip()
        ]
        if not extracted:
            raise ValueError(
                f"No extractable text in PDF {path}; scanned PDFs require OCR"
            )
        return extracted

    try:
        text = path.read_text(encoding="utf-8-sig")
    except UnicodeDecodeError:
        text = path.read_text(encoding="gb18030")
    if path.suffix.lower() == ".txt":
        text = _normalize_txt_headings(text)
    return [(text, {})]


def _normalize_txt_headings(text: str) -> str:
    lines = text.splitlines(keepends=True)
    normalized: list[str] = []

    for index, line in enumerate(lines):
        previous_blank = index == 0 or not lines[index - 1].strip()
        next_blank = index == len(lines) - 1 or not lines[index + 1].strip()
        if previous_blank and next_blank:
            numeric = _NUMBERED_HEADING.match(line.rstrip("\r\n"))
            chinese = _CHINESE_HEADING.match(line.rstrip("\r\n"))
            if numeric:
                level = numeric.group(1).count(".") + 1
                normalized.append(f"{'#' * min(level, 6)} {numeric.group(2)}\n")
                continue
            if chinese:
                normalized.append(f"# {chinese.group(1)}\n")
                continue
        normalized.append(line)
    return "".join(normalized)


def _chunk_text(
    text: str,
    fallback_title: str,
    base_metadata: dict[str, str | int],
    splitter: RecursiveCharacterTextSplitter,
    initial_title_path: tuple[str, ...],
) -> tuple[list[Document], tuple[str, ...]]:
    chunks: list[Document] = []
    sections, final_title_path = _split_sections(
        text, fallback_title, initial_title_path
    )

    for title_path, body in sections:
        if not body.strip():
            continue
        heading_prefix = "\n".join(
            f"{'#' * level} {title}"
            for level, title in enumerate(title_path, start=1)
        )
        for content_type, content in _split_cli_blocks(body):
            if not content.strip():
                continue
            pieces = [content] if content_type == "cli" else splitter.split_text(content)
            for piece in pieces:
                page_content = f"{heading_prefix}\n\n{piece}" if heading_prefix else piece
                metadata = {
                    **base_metadata,
                    "title_path": " > ".join(title_path),
                    "section_title": title_path[-1],
                    "content_type": content_type,
                    "chunk_index": len(chunks),
                }
                chunks.append(Document(page_content=page_content, metadata=metadata))
    return chunks, final_title_path


def _split_sections(
    text: str,
    fallback_title: str,
    initial_title_path: tuple[str, ...],
) -> tuple[list[tuple[tuple[str, ...], str]], tuple[str, ...]]:
    sections: list[tuple[tuple[str, ...], str]] = []
    title_path: list[str] = list(initial_title_path) or [fallback_title]
    body: list[str] = []
    fence_marker: str | None = None

    def flush() -> None:
        if "".join(body).strip():
            sections.append((tuple(title_path), "".join(body)))
        body.clear()

    for line in _normalize_setext_headings(text).splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = None
            body.append(line)
            continue

        heading = _ATX_HEADING.match(line.rstrip("\r\n")) if fence_marker is None else None
        if heading:
            flush()
            level = len(heading.group(1))
            title = heading.group(2).strip()
            title_path[level - 1 :] = [title]
            continue
        body.append(line)

    flush()
    return sections, tuple(title_path)


def _normalize_setext_headings(text: str) -> str:
    lines = text.splitlines(keepends=True)
    normalized: list[str] = []
    fence_marker: str | None = None
    index = 0

    while index < len(lines):
        line = lines[index]
        fence = _FENCE.match(line)
        if fence:
            marker = fence.group(1)
            if fence_marker is None:
                fence_marker = marker
            elif marker[0] == fence_marker[0] and len(marker) >= len(fence_marker):
                fence_marker = None

        if fence_marker is None and index + 1 < len(lines) and line.strip():
            underline = _SETEXT_UNDERLINE.match(lines[index + 1].rstrip("\r\n"))
            if underline:
                level = 1 if underline.group(1).startswith("=") else 2
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                normalized.append(f"{'#' * level} {line.strip()}{newline}")
                index += 2
                continue

        normalized.append(line)
        index += 1

    return "".join(normalized)


def _split_cli_blocks(text: str) -> list[tuple[str, str]]:
    segments: list[tuple[str, str]] = []
    prose: list[str] = []
    cli: list[str] = []
    fence_marker: str | None = None
    plain_cli = False

    def flush_prose() -> None:
        if prose:
            segments.append(("text", "".join(prose)))
            prose.clear()

    def flush_cli() -> None:
        if cli:
            segments.append(("cli", "".join(cli)))
            cli.clear()

    for line in text.splitlines(keepends=True):
        fence = _FENCE.match(line)
        if fence_marker is None and fence:
            flush_cli()
            plain_cli = False
            flush_prose()
            fence_marker = fence.group(1)
            cli.append(line)
            continue
        if fence_marker is not None:
            cli.append(line)
            if (
                fence
                and fence.group(1)[0] == fence_marker[0]
                and len(fence.group(1)) >= len(fence_marker)
            ):
                flush_cli()
                fence_marker = None
            continue
        if _CLI_LINE.match(line):
            flush_prose()
            plain_cli = True
            cli.append(line)
            continue
        if plain_cli:
            flush_cli()
            plain_cli = False
        prose.append(line)

    flush_cli()
    flush_prose()
    return segments
