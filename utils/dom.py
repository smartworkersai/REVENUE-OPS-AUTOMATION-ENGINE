"""
HTML sanitisation and markdown conversion for LLM ingestion.

Raw DOMs cost 50k-100k tokens. This module strips them to clean markdown
and hard-caps at 12,000 chars before any LLM call.
"""

import re

from bs4 import BeautifulSoup
import markdownify

MAX_LLM_CHARS = 12_000


def html_to_markdown(html: str) -> str:
    """
    Parse HTML with BeautifulSoup, strip noise, convert to markdown.
    Returns at most MAX_LLM_CHARS characters.
    """
    soup = BeautifulSoup(html, 'html.parser')

    # Remove non-content elements
    for tag in soup(['script', 'style', 'nav', 'footer', 'header',
                     'aside', 'noscript', 'iframe', 'svg', 'img']):
        tag.decompose()

    # Extract main content area if present, else use full body
    main = soup.find('main') or soup.find('article') or soup.find('body') or soup
    clean_html = str(main)

    md = markdownify.markdownify(
        clean_html,
        heading_style=markdownify.ATX,
        strip=['a'],          # remove hyperlinks — not useful for LLM
    )

    # Collapse excessive whitespace
    md = re.sub(r'\n{3,}', '\n\n', md).strip()

    return md[:MAX_LLM_CHARS]


def extract_text(html: str) -> str:
    """Plain text extraction, no markdown formatting."""
    soup = BeautifulSoup(html, 'html.parser')
    for tag in soup(['script', 'style', 'nav', 'footer', 'header', 'aside']):
        tag.decompose()
    text = soup.get_text(separator=' ', strip=True)
    text = re.sub(r'\s{2,}', ' ', text)
    return text[:MAX_LLM_CHARS]
