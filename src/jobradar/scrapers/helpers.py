"""Shared parsing / text utilities used across scrapers (ported verbatim)."""

import re

from bs4 import BeautifulSoup


def clean_text(text: str | None) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


def clean_html(html_text: str | None) -> str:
    """Strip HTML tags. If content is a bullet list, prefix each item with a bullet."""
    if not html_text:
        return ""
    soup = BeautifulSoup(html_text, "html.parser")
    lines = [f"• {li.get_text().strip()}" for li in soup.find_all("li") if li.get_text().strip()]
    return "\n".join(lines) if lines else soup.get_text(separator="\n").strip()
