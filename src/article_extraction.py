"""
Article extraction service for DEVONzot pipeline.

Provides robust article content and metadata extraction from URLs using
multiple extraction engines with intelligent fallbacks.

Classes:
    HTMLFetcher: Handles HTTP requests with browser headers and session management
    MetadataAggregator: Merges metadata from multiple extraction sources
    ArticleExtractor: Coordinates extraction engines (newspaper3k, readability, trafilatura)
"""

import json
import logging
import random
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import difflib
import html2text
import requests
import trafilatura
from bs4 import BeautifulSoup
from newspaper import Article
from readability import Document

from exceptions import ArticleExtractionError, NetworkError


logger = logging.getLogger(__name__)


# Pool of realistic User-Agent strings for rotation
USER_AGENTS = [
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15',
    'Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:122.0) Gecko/20100101 Firefox/122.0',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:122.0) Gecko/20100101 Firefox/122.0',
]


@dataclass
class ExtractionResult:
    """Container for article extraction results."""
    title: str
    authors: List[str] = field(default_factory=list)
    publish_date: Optional[str] = None
    paragraphs: List[str] = field(default_factory=list)
    markdown: str = ""
    metadata: Dict = field(default_factory=dict)
    engine: str = "unknown"
    quality_score: float = 0.0


class HTMLFetcher:
    """Handles HTTP requests with browser headers and retry logic."""

    def __init__(self, timeout: int = 10, rotate_user_agents: bool = True):
        """
        Initialize HTMLFetcher.

        Args:
            timeout: Request timeout in seconds
            rotate_user_agents: Whether to rotate User-Agent strings
        """
        self.timeout = timeout
        self.rotate_user_agents = rotate_user_agents
        self.session = requests.Session()
        self._setup_session()

    def _setup_session(self):
        """Configure session with browser-like headers."""
        headers = {
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.9',
            'Accept-Encoding': 'gzip, deflate, br',
            'Connection': 'keep-alive',
            'DNT': '1',
            'Upgrade-Insecure-Requests': '1',
        }
        self.session.headers.update(headers)

    def _get_user_agent(self) -> str:
        """Get a User-Agent string (random if rotation enabled)."""
        if self.rotate_user_agents:
            return random.choice(USER_AGENTS)
        return USER_AGENTS[0]

    def fetch(self, url: str, max_retries: int = 3) -> str:
        """
        Fetch HTML content from URL with retry logic.

        Args:
            url: URL to fetch
            max_retries: Maximum number of retry attempts

        Returns:
            HTML content as string

        Raises:
            NetworkError: If all retry attempts fail
        """
        last_exception = None

        for attempt in range(max_retries):
            try:
                headers = {'User-Agent': self._get_user_agent()}
                response = self.session.get(
                    url,
                    headers=headers,
                    timeout=self.timeout,
                    allow_redirects=True
                )
                response.raise_for_status()
                logger.info(f"Successfully fetched {url} (attempt {attempt + 1})")
                return response.text

            except requests.exceptions.Timeout as e:
                logger.warning(f"Timeout fetching {url} (attempt {attempt + 1}/{max_retries})")
                last_exception = e
            except requests.exceptions.RequestException as e:
                logger.warning(f"Request failed for {url} (attempt {attempt + 1}/{max_retries}): {e}")
                last_exception = e

        # All retries failed
        error_msg = f"Failed to fetch {url} after {max_retries} attempts"
        logger.error(error_msg)
        raise NetworkError(error_msg, url=url) from last_exception


class MetadataAggregator:
    """Merges metadata from multiple extraction sources with priority fallback."""

    @staticmethod
    def join_meta(primary: Dict, *others: Dict) -> Dict:
        """
        Merge metadata dictionaries with priority fallback.

        Args:
            primary: Primary metadata dictionary (highest priority)
            others: Additional metadata dictionaries (fallback priority)

        Returns:
            Merged metadata dictionary
        """
        result = dict(primary)
        for meta in others:
            for k, v in meta.items():
                # Only use fallback value if primary doesn't have this key or value is empty
                if k not in result or not result[k]:
                    result[k] = v
        return result

    @staticmethod
    def clean_authors(authors: List[str]) -> List[str]:
        """
        Filter and clean author names.

        Removes:
        - Invalid author patterns (HTML artifacts, technical strings)
        - Duplicate entries
        - Authors shorter than 3 characters

        Args:
            authors: List of author names

        Returns:
            Cleaned and deduplicated list of authors
        """
        if isinstance(authors, str):
            authors = [authors]

        # Filter out invalid patterns
        cleaned = []
        for author in authors:
            if not author:
                continue
            # Must be 3+ chars and contain only letters, spaces, periods, apostrophes, hyphens
            if not re.match(r'^[A-Za-z .\'-]{3,}$', author):
                continue
            # Reject technical/HTML artifacts
            if re.search(r'propublica|wp-block|font-size|media|min-width|var', author, re.I):
                continue
            cleaned.append(author)

        # Remove duplicates while preserving order
        return list(dict.fromkeys(cleaned))

    @staticmethod
    def extract_publication_name(html: str, url: str) -> str:
        """
        Extract publication name from HTML meta tags or URL.

        Tries:
        1. Open Graph site_name
        2. Twitter site name
        3. Domain name as fallback

        Args:
            html: HTML content
            url: Source URL

        Returns:
            Publication name
        """
        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Try Open Graph meta tag
            og_site = soup.find('meta', property='og:site_name')
            if og_site and og_site.get('content'):
                publication = og_site['content'].strip()
                if publication:
                    return publication

            # Try Twitter meta tag
            tw_site = soup.find('meta', attrs={'name': 'twitter:site'})
            if tw_site and tw_site.get('content'):
                publication = tw_site['content'].strip().lstrip('@')
                if publication:
                    return publication

        except Exception as e:
            logger.warning(f"Could not extract publication from meta tags: {e}")

        # Fallback: extract domain from URL
        try:
            domain = url.split('//')[1].split('/')[0]
            return domain
        except (IndexError, AttributeError):
            return "Unknown"


class ArticleExtractor:
    """Coordinates multiple extraction engines with intelligent fallbacks."""

    def __init__(self, html_fetcher: Optional[HTMLFetcher] = None):
        """
        Initialize ArticleExtractor.

        Args:
            html_fetcher: HTMLFetcher instance (creates default if None)
        """
        self.html_fetcher = html_fetcher or HTMLFetcher()
        self.metadata_aggregator = MetadataAggregator()

    def extract(self, url: str, html: Optional[str] = None) -> ExtractionResult:
        """
        Extract article content and metadata using multiple engines.

        Tries extraction engines in order:
        1. newspaper3k + readability + trafilatura (all)
        2. readability + trafilatura (if newspaper3k fails)
        3. trafilatura only (if readability fails)
        4. Minimal extraction from meta tags (last resort)

        Args:
            url: Article URL
            html: Pre-fetched HTML (fetches if None)

        Returns:
            ExtractionResult with best available content

        Raises:
            ArticleExtractionError: If all extraction attempts fail
        """
        # Fetch HTML if not provided
        if not html:
            try:
                html = self.html_fetcher.fetch(url)
            except NetworkError as e:
                raise ArticleExtractionError(f"Failed to fetch URL: {e}", url=url) from e

        # Try full extraction (all engines)
        try:
            return self._extract_with_all_engines(url, html)
        except Exception as e:
            logger.warning(f"Full extraction failed for {url}: {e}")

        # Fallback: Try without newspaper3k
        try:
            return self._extract_with_readability_trafilatura(url, html)
        except Exception as e:
            logger.warning(f"Readability+trafilatura extraction failed for {url}: {e}")

        # Fallback: Try trafilatura only
        try:
            return self._extract_with_trafilatura_only(url, html)
        except Exception as e:
            logger.warning(f"Trafilatura extraction failed for {url}: {e}")

        # Last resort: Minimal extraction from meta tags
        try:
            return self._extract_minimal(url, html)
        except Exception as e:
            error_msg = f"All extraction methods failed for {url}"
            logger.error(error_msg)
            raise ArticleExtractionError(error_msg, url=url) from e

    def _extract_with_all_engines(self, url: str, html: str) -> ExtractionResult:
        """Extract using newspaper3k, readability, and trafilatura."""
        # Newspaper3k extraction
        article = Article(url)
        article.set_html(html)
        article.parse()

        n_title = article.title
        n_authors = self.metadata_aggregator.clean_authors(article.authors)
        n_date = article.publish_date
        n_text = article.text
        n_paragraphs = [p.strip() for p in n_text.split('\n') if p.strip()]

        # Readability extraction
        doc = Document(html)
        r_title = doc.title()
        html_content = doc.summary()
        r_markdown = html2text.html2text(html_content)
        r_paragraphs = [p.strip() for p in r_markdown.split('\n') if p.strip()]

        # Trafilatura extraction
        trafilatura_meta = self._extract_trafilatura_metadata(url, html)

        # Extract publication
        publication = self.metadata_aggregator.extract_publication_name(html, url)

        # Merge metadata
        base_meta = {
            'title': n_title or r_title,
            'authors': n_authors,
            'date': str(n_date) if n_date else None,
            'url': url,
            'publication': publication
        }
        merged_meta = self.metadata_aggregator.join_meta(base_meta, trafilatura_meta)

        # Clean paragraphs
        n_paragraphs = self._clean_paragraphs(n_paragraphs)
        r_paragraphs = self._clean_paragraphs(r_paragraphs)

        # Combine paragraphs intelligently
        combined_paragraphs = self._combine_paragraphs(n_paragraphs, r_paragraphs)

        # Calculate quality score
        quality = self._calculate_quality_score(combined_paragraphs, merged_meta)

        return ExtractionResult(
            title=merged_meta.get('title', ''),
            authors=self.metadata_aggregator.clean_authors(merged_meta.get('authors', [])),
            publish_date=merged_meta.get('date'),
            paragraphs=combined_paragraphs,
            markdown='\n\n'.join(combined_paragraphs),
            metadata=merged_meta,
            engine='newspaper3k+readability+trafilatura',
            quality_score=quality
        )

    def _extract_with_readability_trafilatura(self, url: str, html: str) -> ExtractionResult:
        """Extract using readability and trafilatura (newspaper3k failed)."""
        # Readability extraction
        doc = Document(html)
        r_title = doc.title()
        html_content = doc.summary()
        r_markdown = html2text.html2text(html_content)
        r_paragraphs = [p.strip() for p in r_markdown.split('\n') if p.strip()]

        # Trafilatura extraction
        trafilatura_meta = self._extract_trafilatura_metadata(url, html)

        # Extract publication
        publication = self.metadata_aggregator.extract_publication_name(html, url)

        base_meta = {
            'title': r_title,
            'url': url,
            'publication': publication
        }
        merged_meta = self.metadata_aggregator.join_meta(base_meta, trafilatura_meta)

        r_paragraphs = self._clean_paragraphs(r_paragraphs)
        quality = self._calculate_quality_score(r_paragraphs, merged_meta)

        return ExtractionResult(
            title=merged_meta.get('title', ''),
            authors=self.metadata_aggregator.clean_authors(merged_meta.get('authors', [])),
            publish_date=merged_meta.get('date'),
            paragraphs=r_paragraphs,
            markdown='\n\n'.join(r_paragraphs),
            metadata=merged_meta,
            engine='readability+trafilatura',
            quality_score=quality
        )

    def _extract_with_trafilatura_only(self, url: str, html: str) -> ExtractionResult:
        """Extract using trafilatura only (readability failed)."""
        trafilatura_meta = self._extract_trafilatura_metadata(url, html)
        publication = self.metadata_aggregator.extract_publication_name(html, url)

        # Extract text with trafilatura
        text = trafilatura.extract(html, output_format='txt') or ''
        paragraphs = [p.strip() for p in text.split('\n') if p.strip()]
        paragraphs = self._clean_paragraphs(paragraphs)

        merged_meta = self.metadata_aggregator.join_meta(
            {'url': url, 'publication': publication},
            trafilatura_meta
        )

        quality = self._calculate_quality_score(paragraphs, merged_meta)

        return ExtractionResult(
            title=merged_meta.get('title', ''),
            authors=self.metadata_aggregator.clean_authors(merged_meta.get('authors', [])),
            publish_date=merged_meta.get('date'),
            paragraphs=paragraphs,
            markdown='\n\n'.join(paragraphs),
            metadata=merged_meta,
            engine='trafilatura',
            quality_score=quality
        )

    def _extract_minimal(self, url: str, html: str) -> ExtractionResult:
        """Minimal extraction from meta tags (last resort)."""
        soup = BeautifulSoup(html, 'html.parser')

        # Try to get title from meta tags
        title = None
        og_title = soup.find('meta', property='og:title')
        if og_title and og_title.get('content'):
            title = og_title['content']
        if not title:
            title_tag = soup.find('title')
            if title_tag:
                title = title_tag.get_text().strip()
        if not title:
            title = url

        publication = self.metadata_aggregator.extract_publication_name(html, url)

        # Create minimal markdown
        paragraphs = [f"[Content extraction failed. Original URL: {url}]"]

        return ExtractionResult(
            title=title,
            authors=[],
            publish_date=None,
            paragraphs=paragraphs,
            markdown='\n\n'.join(paragraphs),
            metadata={'title': title, 'url': url, 'publication': publication},
            engine='minimal',
            quality_score=0.1
        )

    def _extract_trafilatura_metadata(self, url: str, html: str) -> Dict:
        """Extract metadata using trafilatura."""
        try:
            trafilatura_data = trafilatura.extract(html, output_format='json')
            if trafilatura_data:
                return json.loads(trafilatura_data)
        except Exception as e:
            logger.warning(f"Trafilatura metadata extraction failed for {url}: {e}")
        return {}

    def _clean_paragraphs(self, paragraphs: List[str]) -> List[str]:
        """
        Remove newsletter signups, editorial notes, and invalid content.

        Args:
            paragraphs: List of paragraph strings

        Returns:
            Filtered list of paragraphs
        """
        filtered = []
        for p in paragraphs:
            p = p.strip()
            if not p:
                continue
            # Remove newsletter signups and boilerplate
            if re.search(r'newsletter|sign up|featured in the One Story|editorial note|implied that', p, re.I):
                continue
            # Remove very short paragraphs (likely navigation/UI elements)
            if len(p) < 20:
                continue
            filtered.append(p)
        return filtered

    def _combine_paragraphs(self, n_paragraphs: List[str], r_paragraphs: List[str]) -> List[str]:
        """
        Intelligently combine paragraphs from newspaper3k and readability.

        Uses sequence matching to identify common content and merge differences.

        Args:
            n_paragraphs: Paragraphs from newspaper3k
            r_paragraphs: Paragraphs from readability

        Returns:
            Combined list of paragraphs
        """
        matcher = difflib.SequenceMatcher(None, n_paragraphs, r_paragraphs)
        combined = []

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == 'equal':
                # Use longer version of each matching paragraph
                for n, r in zip(n_paragraphs[i1:i2], r_paragraphs[j1:j2]):
                    combined.append(r if len(r) > len(n) else n)

                # Handle length mismatches
                if (i2 - i1) < (j2 - j1):
                    combined.extend(r_paragraphs[j1 + (i2 - i1):j2])
                elif (j2 - j1) < (i2 - i1):
                    combined.extend(n_paragraphs[i1 + (j2 - j1):i2])

            elif tag == 'insert':
                combined.extend(r_paragraphs[j1:j2])
            elif tag == 'delete':
                combined.extend(n_paragraphs[i1:i2])

        return self._format_paragraphs(combined)

    def _format_paragraphs(self, paragraphs: List[str]) -> List[str]:
        """
        Format paragraphs with proper markdown headers.

        Converts short title-case or uppercase lines to headers.

        Args:
            paragraphs: List of paragraph strings

        Returns:
            Formatted paragraphs
        """
        formatted = []
        for line in paragraphs:
            # Short title-case lines become headers
            if len(line.split()) < 5 and line.isalpha() and line.istitle():
                formatted.append(f"### {line}")
            # Short uppercase lines become title-case headers
            elif len(line.split()) < 5 and line.isupper():
                formatted.append(f"### {line.title()}")
            else:
                formatted.append(line)
        return formatted

    def _calculate_quality_score(self, paragraphs: List[str], metadata: Dict) -> float:
        """
        Calculate extraction quality score (0.0 to 1.0).

        Factors:
        - Number of paragraphs
        - Total word count
        - Metadata completeness

        Args:
            paragraphs: Extracted paragraphs
            metadata: Extracted metadata

        Returns:
            Quality score between 0.0 and 1.0
        """
        score = 0.0

        # Paragraph count (max 0.4)
        para_count = len(paragraphs)
        score += min(para_count / 20.0, 0.4)

        # Word count (max 0.4)
        word_count = sum(len(p.split()) for p in paragraphs)
        score += min(word_count / 1000.0, 0.4)

        # Metadata completeness (max 0.2)
        has_title = bool(metadata.get('title'))
        has_authors = bool(metadata.get('authors'))
        has_date = bool(metadata.get('date'))
        metadata_score = (has_title + has_authors + has_date) / 3.0 * 0.2
        score += metadata_score

        return min(score, 1.0)
