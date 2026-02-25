"""
Article extraction and combination for DEVONzot pipeline.

Refactored to use ArticleExtractor service for clean separation of concerns.
"""

import logging
from pathlib import Path
from typing import Optional

from article_extraction import ArticleExtractor, ExtractionResult, HTMLFetcher
from exceptions import ArticleExtractionError


logger = logging.getLogger(__name__)


def combine_articles(url: str, out_path: str, html: Optional[str] = None) -> ExtractionResult:
    """
    Extract article content from URL and write to markdown file with YAML frontmatter.

    Uses multiple extraction engines (newspaper3k, readability, trafilatura) with
    intelligent fallbacks to maximize extraction success rate.

    Args:
        url: Article URL to extract
        out_path: Output file path for markdown file
        html: Optional pre-fetched HTML content (fetches if None)

    Returns:
        ExtractionResult with extracted content and metadata

    Raises:
        ArticleExtractionError: If extraction fails completely
    """
    try:
        # Initialize extractor
        html_fetcher = HTMLFetcher(timeout=10, rotate_user_agents=True)
        extractor = ArticleExtractor(html_fetcher=html_fetcher)

        # Extract article
        logger.info(f"Extracting article from {url}")
        result = extractor.extract(url, html=html)
        logger.info(f"Extraction successful using {result.engine} (quality: {result.quality_score:.2f})")

        # Generate YAML frontmatter
        frontmatter = _generate_frontmatter(result)

        # Format content
        content = frontmatter + '\n\n' + result.markdown

        # Write to file
        out_file = Path(out_path)
        out_file.parent.mkdir(parents=True, exist_ok=True)
        out_file.write_text(content, encoding='utf-8')
        logger.info(f"Article written to {out_path}")

        return result

    except ArticleExtractionError:
        # Re-raise extraction errors as-is
        raise
    except Exception as e:
        error_msg = f"Unexpected error processing {url}"
        logger.error(f"{error_msg}: {e}", exc_info=True)
        raise ArticleExtractionError(error_msg, url=url) from e


def _generate_frontmatter(result: ExtractionResult) -> str:
    """
    Generate YAML frontmatter from extraction result.

    Args:
        result: ExtractionResult with metadata

    Returns:
        YAML frontmatter string with --- delimiters
    """
    metadata = result.metadata
    fields = ['title', 'authors', 'date', 'url', 'publication']

    lines = ['---']
    for field in fields:
        value = metadata.get(field, '')
        # Format value appropriately
        if isinstance(value, list):
            if value:  # Non-empty list
                lines.append(f'{field}:')
                for item in value:
                    lines.append(f'  - {item}')
            else:  # Empty list
                lines.append(f'{field}: []')
        elif value is None:
            lines.append(f'{field}: ')
        else:
            # Escape special characters in YAML
            value_str = str(value).replace(':', '\\:')
            lines.append(f'{field}: {value_str}')

    lines.append('---')
    return '\n'.join(lines)


if __name__ == "__main__":
    import re
    from newspaper import Article

    # Configure logging for standalone execution
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )

    # Test URLs
    links_to_test = [
        "https://www.theatlantic.com/technology/2026/01/liberal-arts-college-war-higher-ed/685800/",
        "https://www.vox.com/world/2018/4/5/17172754/russia-fake-news-trump-america-timothy-snyder",
        "https://www.vox.com/podcasts/478326/epstein-files-latest-misogyny-elite",
        "https://www.theverge.com/ai-artificial-intelligence/876558/tech-workers-ice-resistance-google-microsoft-clear-abbott",
        "https://www.propublica.org/article/michael-reinstein-chicago-clozapine",
        "https://www.wired.com/story/gadget-lab-podcast-455/"
    ]

    for url in links_to_test:
        # Try to get a safe filename from the article title
        try:
            article = Article(url)
            article.download()
            article.parse()
            title = article.title or url
        except Exception:
            title = url

        # Clean title for filename
        safe_title = re.sub(r'[^\w\-_\. ]', '_', title)[:80]
        if not safe_title.strip('_'):
            safe_title = url.split('//')[1].replace('/', '_').replace('?', '_').replace('&', '_')

        out_path = f"test_extractions/combined_{safe_title}.md"

        try:
            result = combine_articles(url, out_path)
            print(f"✓ Combined article written to {out_path} (engine: {result.engine}, quality: {result.quality_score:.2f})")
        except Exception as e:
            print(f"✗ Failed to process {url}: {e}")
