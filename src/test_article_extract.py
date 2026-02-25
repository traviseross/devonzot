
from newspaper import Article
from readability import Document
import requests
import html2text


url = "https://www.theatlantic.com/technology/2026/01/liberal-arts-college-war-higher-ed/685800/"


# Newspaper3k extraction
article = Article(url)
article.download()
article.parse()

# Write full newspaper3k extraction to file
with open("/Users/travisross/DEVONzot/test_extractions/newspaper3k.md", "w", encoding="utf-8") as f:
	f.write(f"# newspaper3k\n")
	f.write(f"Title: {article.title}\n")
	f.write(f"Authors: {article.authors}\n")
	f.write(f"Date: {article.publish_date}\n\n")
	f.write(article.text)

# Readability-lxml extraction
resp = requests.get(url)
doc = Document(resp.text)
html_content = doc.summary()
markdown_content = html2text.html2text(html_content)

# Write full readability-lxml markdown extraction to file
with open("/Users/travisross/DEVONzot/test_extractions/readability_lxml.md", "w", encoding="utf-8") as f:
	f.write(f"# readability-lxml (markdown)\n")
	f.write(f"Title: {doc.title()}\n\n")
	f.write(markdown_content)
