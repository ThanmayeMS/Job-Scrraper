"""
core/helpers.py — Shared parsing and text utilities used across scrapers.
"""

import re
from bs4 import BeautifulSoup


def clean_text(text: str) -> str:
    """Collapse whitespace."""
    return re.sub(r"\s+", " ", text or "").strip()


def clean_html(html_text: str) -> str:
    """Strip HTML tags. If content is a bullet list, prefix each item with •."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    lines = [f"• {li.get_text().strip()}" for li in soup.find_all("li") if li.get_text().strip()]
    return "\n".join(lines) if lines else soup.get_text(separator="\n").strip()


def parse_sections_by_headings(html: str, heading_map: dict) -> dict:
    """
    Generic section parser for HTML blobs delimited by heading tags.

    heading_map: { "heading text (lowercase)": "field_name" }
    Returns: { "field_name": "extracted text" }

    Walks the soup, accumulates text under each heading until the next one.
    Used by JPMC, Microsoft, and any future company with similar structure.
    """
    result = {v: "" for v in heading_map.values()}
    if not html:
        return result

    soup = BeautifulSoup(html, "html.parser")
    current_field = None
    lines: dict   = {v: [] for v in heading_map.values()}

    for tag in soup.find_all(["h1", "h2", "h3", "h4", "strong", "b", "p", "li", "ul"]):
        # Heading detection
        if tag.name in {"h1", "h2", "h3", "h4", "strong", "b"}:
            heading = clean_text(tag.get_text()).lower()
            for key, field in heading_map.items():
                if key in heading:
                    current_field = field
                    break
            continue

        if current_field is None:
            continue

        if tag.name == "li":
            text = clean_text(tag.get_text())
            if text:
                lines[current_field].append("• " + text)
        elif tag.name == "p":
            text = clean_text(tag.get_text())
            if text:
                lines[current_field].append(text)

    for field, field_lines in lines.items():
        result[field] = "\n".join(field_lines)

    return result
