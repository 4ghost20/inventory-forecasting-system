#!/usr/bin/env python3
"""Clean Word documents by fixing encoding and formatting artifacts.

This script does not attempt to bypass AI or plagiarism detection. It only
performs ordinary document hygiene: mojibake repair, invalid control-character
removal, repeated-space cleanup, and safe DOCX repackaging.
"""

from __future__ import annotations

import argparse
import re
import shutil
import zipfile
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent
DEFAULT_DOCS_DIR = BASE_DIR / "app"
DEFAULT_SUFFIX = "_FORMATTED"

INVALID_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
REPEATED_SPACES = re.compile(r" {2,}")

MOJIBAKE_REPLACEMENTS = {
    "â€™": "'",
    "â€˜": "'",
    "â€œ": '"',
    "â€": '"',
    "â€": '"',
    "â€“": "-",
    "â€”": "-",
    "â€¦": "...",
    "Â ": " ",
    "Â": "",
    "â€¢": "-",
    "â„¢": "(TM)",
    "â€‹": "",
    "ï»¿": "",
    "ðŸ“¦": "",
    "ðŸ”": "",
    "ðŸ‘¤": "",
    "ðŸš€": "",
    "ðŸ“Š": "",
    "ðŸ“ˆ": "",
    "ðŸ“¥": "",
    "ðŸ”„": "",
    "ðŸ›’": "",
    "ðŸ—„ï¸": "",
    "ðŸ› ï¸": "",
    "âœ…": "",
    "âŒ": "",
    "âœ¨": "",
    "âš ï¸": "Warning:",
}


def clean_text(text: str) -> str:
    """Return text with common encoding and spacing artifacts cleaned."""
    if not text:
        return text

    cleaned = text
    for bad, replacement in MOJIBAKE_REPLACEMENTS.items():
        cleaned = cleaned.replace(bad, replacement)

    cleaned = INVALID_CONTROL_CHARS.sub("", cleaned)
    cleaned = REPEATED_SPACES.sub(" ", cleaned)
    return cleaned


def output_path_for(input_path: Path, suffix: str) -> Path:
    """Build a cleaned output path without overwriting the source file."""
    return input_path.with_name(f"{input_path.stem}{suffix}{input_path.suffix}")


def backup_path_for(input_path: Path) -> Path:
    """Build a one-time backup path for the original document."""
    return input_path.with_name(f"{input_path.stem}_ORIGINAL{input_path.suffix}")


def should_skip_docx(path: Path, suffix: str) -> bool:
    """Skip backup and generated output documents."""
    upper_name = path.stem.upper()
    return (
        upper_name.endswith(suffix.upper())
        or upper_name.endswith("_ORIGINAL")
        or "BACKUP" in upper_name
        or "CLEANED" in upper_name
        or "FORMATTED" in upper_name
    )


def clean_docx_package(input_path: Path, output_path: Path) -> None:
    """Write a cleaned copy of a DOCX file."""
    with zipfile.ZipFile(input_path, "r") as source:
        with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as target:
            for item in source.infolist():
                data = source.read(item.filename)
                if item.filename.lower().endswith((".xml", ".rels")):
                    try:
                        text = data.decode("utf-8")
                    except UnicodeDecodeError:
                        text = data.decode("utf-8", errors="replace")
                    data = clean_text(text).encode("utf-8")
                target.writestr(item, data)


def clean_html_file(input_path: Path, output_path: Path) -> None:
    """Write a cleaned copy of an HTML export."""
    data = input_path.read_text(encoding="utf-8", errors="replace")
    output_path.write_text(clean_text(data), encoding="utf-8")


def clean_file(path: Path, suffix: str, create_backup: bool) -> Path:
    """Clean one supported document and return the generated output path."""
    output_path = output_path_for(path, suffix)

    if create_backup:
        backup_path = backup_path_for(path)
        if not backup_path.exists():
            shutil.copy2(path, backup_path)

    if path.suffix.lower() == ".docx":
        clean_docx_package(path, output_path)
    elif path.suffix.lower() in {".htm", ".html"}:
        clean_html_file(path, output_path)
    else:
        raise ValueError(f"Unsupported file type: {path}")

    return output_path


def find_documents(folder: Path, suffix: str) -> list[Path]:
    """Find original Word and HTML documents in a folder."""
    docs: list[Path] = []
    for pattern in ("*.docx", "*.htm", "*.html"):
        for path in folder.glob(pattern):
            if path.is_file() and not should_skip_docx(path, suffix):
                docs.append(path)
    return sorted(docs)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clean DOCX/HTML documents in the app folder."
    )
    parser.add_argument(
        "--folder",
        type=Path,
        default=DEFAULT_DOCS_DIR,
        help="Folder containing documents to clean. Defaults to ./app.",
    )
    parser.add_argument(
        "--suffix",
        default=DEFAULT_SUFFIX,
        help="Suffix for generated output files.",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Do not create *_ORIGINAL backups before writing cleaned copies.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    folder = args.folder.resolve()

    if not folder.exists():
        raise FileNotFoundError(f"Folder not found: {folder}")

    documents = find_documents(folder, args.suffix)
    if not documents:
        print(f"No original DOCX/HTML documents found in {folder}")
        return

    print(f"Cleaning {len(documents)} document(s) in {folder}")
    for document in documents:
        output_path = clean_file(document, args.suffix, not args.no_backup)
        print(f"Created {output_path.name}")


if __name__ == "__main__":
    main()
