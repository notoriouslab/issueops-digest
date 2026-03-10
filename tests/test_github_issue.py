"""Tests for github_issue module."""
import pytest

from github_issue import GitHubIssueOutput, _escape_markdown


class TestParseSelectedUrls:
    def test_basic_selection(self):
        body = """
- [x] Test Title 1
  - URL: https://example.com/1
- [ ] Test Title 2
  - URL: https://example.com/2
- [X] Test Title 3
  - URL: https://example.com/3
        """
        urls = GitHubIssueOutput.parse_selected_urls(body)
        assert len(urls) == 2
        assert urls[0] == "https://example.com/1"
        assert urls[1] == "https://example.com/3"

    def test_crlf_line_endings(self):
        body = "- [x] CRLF Title\r\n  - URL: https://example.com/crlf\r\n"
        urls = GitHubIssueOutput.parse_selected_urls(body)
        assert len(urls) == 1
        assert urls[0] == "https://example.com/crlf"

    def test_no_checked_items(self):
        body = "- [ ] Unchecked\n  - URL: https://example.com/none\n"
        assert GitHubIssueOutput.parse_selected_urls(body) == []

    def test_empty_body(self):
        assert GitHubIssueOutput.parse_selected_urls("") == []

    def test_malformed_url(self):
        body = "- [x] Bad URL\n  - URL: not-a-url\n"
        assert GitHubIssueOutput.parse_selected_urls(body) == []

    def test_multiple_newlines_between(self):
        body = "- [x] Title\n\n  - URL: https://example.com/multi\n"
        urls = GitHubIssueOutput.parse_selected_urls(body)
        assert len(urls) == 1

    def test_http_urls(self):
        body = "- [x] HTTP Title\n  - URL: http://example.com/http\n"
        urls = GitHubIssueOutput.parse_selected_urls(body)
        assert len(urls) == 1
        assert urls[0] == "http://example.com/http"

    def test_url_with_query_params(self):
        body = "- [x] Query Title\n  - URL: https://example.com/path?key=val&foo=bar\n"
        urls = GitHubIssueOutput.parse_selected_urls(body)
        assert len(urls) == 1
        assert "key=val" in urls[0]


class TestEscapeMarkdown:
    def test_pipe_escaped(self):
        assert "\\|" in _escape_markdown("table|cell")

    def test_backtick_escaped(self):
        assert "\\`" in _escape_markdown("code`injection")

    def test_html_escaped(self):
        result = _escape_markdown("<script>alert('xss')</script>")
        assert "&lt;" in result
        assert "&gt;" in result
        assert "<script>" not in result

    def test_normal_text_unchanged(self):
        text = "Normal article title about AI"
        assert _escape_markdown(text) == text

    def test_newlines_collapsed(self):
        text = "line1\n\n\nline2"
        result = _escape_markdown(text)
        assert "\n\n\n" not in result
