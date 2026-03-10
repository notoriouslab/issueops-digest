"""
GitHub Issue output handler for issueops-digest.
Creates a checkbox-style Issue for manual article selection.
"""
import logging
import os
import re
import requests
from datetime import datetime
from typing import List, Dict

try:
    from dotenv import load_dotenv as _load_dotenv
except ImportError:
    _load_dotenv = None

logger = logging.getLogger(__name__)


def _escape_markdown(text: str) -> str:
    """Escape characters that could break GitHub markdown rendering."""
    # Replace pipe chars, backticks, and angle brackets that could inject markdown
    text = text.replace('|', '\\|')
    text = text.replace('`', '\\`')
    text = text.replace('<', '&lt;').replace('>', '&gt;')
    # Collapse consecutive newlines to prevent layout injection
    text = re.sub(r'\n{2,}', ' ', text)
    return text


class GitHubIssueOutput:
    def __init__(self):
        if _load_dotenv:
            _load_dotenv()
        self.token = os.getenv("GITHUB_TOKEN")
        self.repo = os.getenv("GITHUB_REPO")

        if not self.token:
            raise ValueError("GITHUB_TOKEN not set. Check your .env file.")
        if not self.repo:
            raise ValueError("GITHUB_REPO not set. Check your .env file. (e.g. your-username/issueops-digest)")

        self.api_url = f"https://api.github.com/repos/{self.repo}/issues"
        self._headers = {
            "Authorization": f"token {self.token}",
            "Accept": "application/vnd.github.v3+json"
        }

    def _ensure_labels(self) -> None:
        """Create required labels if they don't exist (idempotent)."""
        url = f"https://api.github.com/repos/{self.repo}/labels"
        for name, color in [("discovery", "0075ca"), ("pending-capture", "e4e669")]:
            try:
                resp = requests.post(url, json={"name": name, "color": color}, headers=self._headers, timeout=10)
                # 201 = created, 422 = already exists — both are fine
                if resp.status_code not in (201, 422):
                    logger.error("Failed to create label '%s': HTTP %d — check token permissions", name, resp.status_code)
                    raise RuntimeError(f"Cannot create required label '{name}': HTTP {resp.status_code}")
            except requests.RequestException as e:
                logger.error("Network error creating label '%s': %s", name, e)
                raise

    def publish(self, keywords: str, results: List[Dict]) -> str:
        """Create a discovery Issue with checkbox list."""
        self._ensure_labels()
        today = datetime.now().strftime("%Y%m%d")
        safe_keywords = _escape_markdown(keywords)
        title = f"Search Results: {keywords[:100]} - {today}"

        body = "## issueops-digest Discovery\n"
        body += f"Keywords: `{safe_keywords}`\n\n"
        body += "Check the articles you want to capture, then **Close** this Issue to trigger auto-capture.\n\n"
        body += "---\n\n"

        for res in results:
            score = res.get('score', 0)
            safe_title = _escape_markdown(res.get('title', 'Untitled'))
            safe_source = _escape_markdown(res.get('source', 'Unknown'))
            link = res.get('link', '')
            body += f"- [ ] {safe_title} (Score: {score})\n"
            body += f"  - URL: {link}\n"
            body += f"  - Source: {safe_source}\n\n"

        payload = {
            "title": title,
            "body": body,
            "labels": ["discovery", "pending-capture"]
        }

        response = requests.post(self.api_url, json=payload, headers=self._headers, timeout=15)
        if response.status_code == 201:
            url = response.json().get('html_url')
            logger.info("Issue created: %s", url)
            return url
        else:
            return f"Error: Failed to create issue. status={response.status_code}, msg={response.text[:200]}"

    @staticmethod
    def parse_selected_urls(issue_body: str) -> List[str]:
        """Parse checked checkbox URLs from Issue body."""
        pattern = r'- \[[xX]\] .*?(?:\r?\n)+\s*- URL: (https?://[^\s]+)'
        return re.findall(pattern, issue_body, re.DOTALL)


if __name__ == "__main__":
    # Self-test
    test_body = """
- [x] Test Title 1
  - URL: https://example.com/1
- [ ] Test Title 2
  - URL: https://example.com/2
- [X] Test Title 3
  - URL: https://example.com/3
    """
    urls = GitHubIssueOutput.parse_selected_urls(test_body)
    assert len(urls) == 2
    assert urls[0] == "https://example.com/1"
    assert urls[1] == "https://example.com/3"

    # Test with CRLF line endings
    test_crlf = "- [x] CRLF Title\r\n  - URL: https://example.com/crlf\r\n"
    crlf_urls = GitHubIssueOutput.parse_selected_urls(test_crlf)
    assert len(crlf_urls) == 1
    assert crlf_urls[0] == "https://example.com/crlf"

    # Test with no checked items
    test_empty = "- [ ] Unchecked\n  - URL: https://example.com/none\n"
    assert GitHubIssueOutput.parse_selected_urls(test_empty) == []

    # Test markdown escaping
    assert '&lt;' in _escape_markdown('<script>')
    assert '\\|' in _escape_markdown('pipe|char')

    print("All self-tests passed.")
