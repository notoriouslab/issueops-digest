"""
issueops-digest: AI-powered news curation with GitHub Issues as UI.
Search → AI Score → Checkbox Pick → Auto-capture.
"""
import os
import json
import re
import sys
import time
import requests
import yaml
from datetime import datetime, timedelta
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# Load config
CONFIG_PATH = Path(__file__).parent / "config.yaml"
if CONFIG_PATH.exists():
    with open(CONFIG_PATH, 'r') as f:
        CONFIG = yaml.safe_load(f)
else:
    CONFIG = {}

WEIGHT_PROFILES = {
    "default": {"W_REL": 0.6, "W_AUTH": 0.15, "W_REC": 0.15, "W_BASE": 0.1},
    "news": {"W_REL": 0.5, "W_AUTH": 0.2, "W_REC": 0.2, "W_BASE": 0.1},
    "research": {"W_REL": 0.7, "W_AUTH": 0.1, "W_REC": 0.05, "W_BASE": 0.15},
}

DEEP_DOMAINS = CONFIG.get("deep_domains", ["substack.com", "medium.com"])
SOCIAL_DOMAINS = CONFIG.get("social_domains", ["twitter.com", "x.com", "reddit.com"])
AUTHORITY_MAP = CONFIG.get("authority_domains", {})

# --- Quota Tracking (CLI mode) ---
USAGE_PATH = Path(__file__).parent / ".usage_stats.json"

def _load_usage() -> dict:
    if USAGE_PATH.exists():
        with open(USAGE_PATH, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def _save_usage(data: dict):
    import fcntl
    with open(USAGE_PATH, 'w') as f:
        fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        json.dump(data, f, indent=2)
        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

def _track_api_call(api_name: str):
    quotas = CONFIG.get("quotas", {})
    if not quotas:
        return
    data = _load_usage()
    month = datetime.now().strftime("%Y-%m")
    if month not in data:
        data[month] = {}
    data[month][api_name] = data[month].get(api_name, 0) + 1

    limit_key = f"{api_name}_monthly" if api_name != "gemini" else "gemini_daily"
    limit = quotas.get(limit_key, 0)
    warn_pct = quotas.get("warn_at_percent", 80)

    count = data[month][api_name]
    if limit > 0:
        pct = count / limit * 100
        if pct >= 95:
            print(f"\033[91m⛔ {api_name}: {count}/{limit} ({pct:.0f}%) — approaching limit!\033[0m")
        elif pct >= warn_pct:
            print(f"\033[93m⚠️ {api_name}: {count}/{limit} ({pct:.0f}%)\033[0m")

    _save_usage(data)


# --- Security Check ---
def _check_env_safety():
    gitignore = Path(__file__).parent / ".gitignore"
    if gitignore.exists():
        content = gitignore.read_text()
        if ".env" not in content:
            print("\033[91m⛔ WARNING: .env is NOT in .gitignore! Your API keys may be exposed.\033[0m")
            print("\033[91m   Add '.env' to .gitignore immediately.\033[0m")

_check_env_safety()


class WebDigest:
    def __init__(self, weight_profile="default"):
        profile = weight_profile or CONFIG.get("weight_profile", "default")
        self.weights = WEIGHT_PROFILES.get(profile, WEIGHT_PROFILES["default"])
        self.brave_key = os.getenv("BRAVE_API_KEY")
        self.tavily_key = os.getenv("TAVILY_API_KEY")
        self.gemini_key = os.getenv("GEMINI_API_KEY")

        if not self.brave_key:
            print("Error: BRAVE_API_KEY not set. Check your .env file.")
            sys.exit(1)
        if not self.gemini_key:
            print("Error: GEMINI_API_KEY not set. Check your .env file.")
            sys.exit(1)

    def _extract_domain(self, url: str) -> str:
        from urllib.parse import urlparse
        return urlparse(url).netloc.replace('www.', '')

    def _calculate_recency(self, res: dict) -> float:
        pub_time = res.get('published_time')
        if pub_time:
            try:
                from dateutil.parser import parse
                dt = parse(pub_time)
                diff = datetime.now(dt.tzinfo) - dt
                if diff < timedelta(hours=6): return 10
                if diff < timedelta(days=1): return 9
                if diff < timedelta(days=3): return 8
                if diff < timedelta(days=30): return 6
                if diff < timedelta(days=365): return 4
                return 2
            except Exception:
                pass
        snippet = res.get('snippet', '')
        if "小時前" in snippet or "hours ago" in snippet: return 10
        if "昨天" in snippet or "yesterday" in snippet: return 8
        if "天前" in snippet or "days ago" in snippet: return 6
        return 2

    def _calculate_path_depth(self, url: str) -> float:
        try:
            from urllib.parse import urlparse
            path = urlparse(url).path.strip('/')
            if not path: return 0.1
            parts = [p for p in path.split('/') if p]
            if len(parts) <= 1: return 0.3
            return 1.0
        except Exception:
            return 1.0

    def _fetch_jina_content(self, url: str) -> str:
        domain = self._extract_domain(url)
        if any(d in domain for d in SOCIAL_DOMAINS):
            return ""
        try:
            print(f"  [Jina] Reading full text: {url[:50]}...")
            headers = {"Accept": "application/json"}
            resp = requests.get(f"https://r.jina.ai/{url}", headers=headers, timeout=12)
            if resp.status_code == 200:
                try:
                    return resp.json().get('data', {}).get('content', '')[:3000]
                except Exception:
                    return resp.text[:3000]
        except Exception:
            pass
        return ""

    def _search_brave(self, query: str) -> list:
        if not self.brave_key:
            return []
        results = []
        headers = {"X-Subscription-Token": self.brave_key, "Accept": "application/json"}

        if "site:" not in query:
            try:
                _track_api_call("brave")
                resp = requests.get("https://api.search.brave.com/res/v1/news/search",
                                    headers=headers, params={"q": query, "count": 25}, timeout=10)
                if resp.status_code == 200:
                    for r in resp.json().get("results", []):
                        results.append({
                            'title': f"[News] {r['title']}", 'link': r['url'],
                            'snippet': r.get('description', ''),
                            'source': self._extract_domain(r['url']),
                            'base_score': 10, 'provider': 'brave',
                            'published_time': r.get('published_time')
                        })
            except Exception:
                pass

        try:
            _track_api_call("brave")
            resp = requests.get("https://api.search.brave.com/res/v1/web/search",
                                headers=headers, params={"q": query, "count": 20}, timeout=10)
            if resp.status_code == 200:
                for r in resp.json().get("web", {}).get("results", []):
                    if "site:" not in query and r['url'].count('/') < 4:
                        continue
                    results.append({
                        'title': r['title'], 'link': r['url'],
                        'snippet': r.get('description', ''),
                        'source': self._extract_domain(r['url']),
                        'base_score': 5 if "site:" not in query else 8,
                        'provider': 'brave'
                    })
        except Exception:
            pass
        return results

    def _search_tavily(self, query: str, include_domains: list = None) -> list:
        if not self.tavily_key:
            return []
        try:
            _track_api_call("tavily")
            payload = {
                "api_key": self.tavily_key, "query": query,
                "search_depth": "advanced", "max_results": 15
            }
            if include_domains:
                payload["include_domains"] = include_domains
            resp = requests.post("https://api.tavily.com/search", json=payload, timeout=15)
            if resp.status_code == 200:
                return [{
                    'title': r['title'], 'link': r['url'], 'snippet': r['content'],
                    'source': self._extract_domain(r['url']),
                    'base_score': 8, 'provider': 'tavily',
                    'tavily_score': r.get('score', 0.5)
                } for r in resp.json().get("results", [])]
        except Exception:
            pass
        return []

    def search_wide(self, query: str, is_social=False) -> list:
        if is_social:
            print(f"  [Social] Probing: '{query}'")
            brave = self._search_brave(query)
            domains = ["twitter.com", "x.com", "reddit.com"]
            clean_q = query.replace("site:twitter.com", "").replace("site:x.com", "").replace("site:reddit.com", "").strip()
            tavily = self._search_tavily(clean_q, include_domains=domains)
            return brave + tavily

        print(f"  [Brave]  Searching: '{query}'")
        brave = self._search_brave(query)
        print(f"  [Tavily] Searching: '{query}'")
        tavily = self._search_tavily(query)
        return brave + tavily

    def score_and_dedup(self, full_query: str, results: list):
        if not results:
            return []
        from google import genai
        from google.genai import types
        import difflib

        # Physical dedup
        unique = []
        for res in results:
            if not any(difflib.SequenceMatcher(None, res['title'], u['title']).ratio() > 0.8 for u in unique):
                unique.append(res)

        target = unique[:50]

        # Fetch full text for top deep-domain articles via Jina Reader
        for i in range(min(len(target), 8)):
            res = target[i]
            if any(d in res['source'] for d in DEEP_DOMAINS):
                full_text = self._fetch_jina_content(res['link'])
                if full_text:
                    res['snippet'] = f"[Full text] {full_text[:500]}..."

        _track_api_call("gemini")
        client = genai.Client(api_key=self.gemini_key)

        prompt = f"""You are a professional intelligence analyst performing a multi-dimensional bilingual search.
Keywords: {full_query}
Tasks:
1. Rate each result's substantive relevance (0-10).
2. Social posts (Reddit/Twitter): score based on whether they contain real info or unique perspectives.
3. Long-form (Substack/Medium): score based on content depth. Quality long-form should outrank news fragments.
4. Filter out: homepages, tag listings, ads, meaningless snippets → score 0-1.

Return JSON: [{{"id": 0, "rel": 8.5, "dup": false}}, ...]
Search results:
"""
        for i, res in enumerate(target):
            prompt += f"ID {i}: {res['title']} | {res['snippet'][:200]}\n"

        try:
            resp = client.models.generate_content(
                model='gemini-2.0-flash', contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            analysis_dict = {item['id']: item for item in json.loads(resp.text)}
        except Exception:
            analysis_dict = {}

        sub_queries = [q.strip().lower() for q in full_query.split('|') if q.strip()]
        final_list = []

        print(f"\n--- Scoring ---")
        for i, res in enumerate(target):
            meta = analysis_dict.get(i, {"rel": 5})
            if meta.get('dup'):
                continue

            rel_score = float(meta.get('rel', 5))

            # Fuzzy feature filter (bilingual, order-independent)
            has_feature = False
            text_pool = (res['title'] + " " + res.get('snippet', '')).lower()
            for sq in sub_queries:
                words = []
                for part in sq.split():
                    if all(ord(c) < 128 for c in part):
                        words.extend([w for w in re.split(r'\W+', part) if len(w) > 1])
                    else:
                        words.append(part)
                if words and all(word in text_pool for word in words):
                    has_feature = True
                    break

            if not has_feature:
                rel_score *= 0.3 if rel_score < 7.0 else 0.7

            auth_score = AUTHORITY_MAP.get(res['source'], 0) / 15 * 10
            rec_score = self._calculate_recency(res)
            base_score = res.get('base_score', 5)

            q_sum = (rel_score * self.weights["W_REL"] + auth_score * self.weights["W_AUTH"] +
                     rec_score * self.weights["W_REC"] + base_score * self.weights["W_BASE"])

            path_mult = 1.0 if res.get('provider') == 'tavily' or res.get('base_score', 5) >= 10 else self._calculate_path_depth(res['link'])
            score = (rel_score / 10.0) * q_sum * path_mult

            res['score'] = round(score, 2)
            has_cjk = len(re.findall(r'[\u4e00-\u9fff]', res['title'])) > 2
            lang_tag = "ZH" if has_cjk else "EN"
            print(f"  ID {i}: [{lang_tag}] [{res['title'][:25]}...] Rel:{rel_score:.1f} | Final:{score:.2f}")
            if score >= 1.1:
                final_list.append(res)

        return sorted(final_list, key=lambda x: x['score'], reverse=True)[:25]

    def run_digest(self, query: str) -> str:
        from github_issue import GitHubIssueOutput

        print(f"\n🔍 Starting multi-dimensional search: '{query}'")
        sub_queries = [q.strip() for q in query.strip('"').split('|') if q.strip()]

        results_per_query = []
        for sq in sub_queries:
            results_per_query.append(self.search_wide(sq))

        # Auto-append social + deep content searches
        if "site:" not in query and len(query) > 2:
            social_q = f"{sub_queries[0]} (site:reddit.com OR site:x.com OR site:twitter.com)"
            results_per_query.append(self.search_wide(social_q, is_social=True))
            deep_q = f"{sub_queries[0]} (site:substack.com OR site:medium.com)"
            results_per_query.append(self.search_wide(deep_q))

        # Interleaving (round-robin merge for fair exposure)
        raw_results = []
        max_len = max(len(lst) for lst in results_per_query) if results_per_query else 0
        for i in range(max_len):
            for q_res in results_per_query:
                if i < len(q_res):
                    raw_results.append(q_res[i])

        if not raw_results:
            return "❌ No results found."
        top = self.score_and_dedup(query, raw_results)
        if not top:
            return "⚠️ No results passed AI scoring."

        return GitHubIssueOutput().publish(query, top)


if __name__ == "__main__":
    profile = next((a.split("=")[1] for a in sys.argv if a.startswith("--profile=")), None)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if not args:
        print("Usage: python web_digest.py \"search keywords\" [--profile=news|research]")
        sys.exit(1)
    print(WebDigest(weight_profile=profile).run_digest(args[0]))
