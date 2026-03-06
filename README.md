# issueops-digest

AI-powered news curation using GitHub Issues as your UI. No frontend, no server, no monthly fees.

**Search → AI Score → Checkbox Pick → Auto-capture**

## How it works

1. **Search**: Brave + Tavily (optional) fetch news and web results for your keywords
2. **Score**: Gemini AI filters out noise, scores articles by relevance and depth
3. **Pick**: Results appear as a GitHub Issue with checkboxes — open the GitHub app on your phone, tick what you want
4. **Capture**: Close the Issue → GitHub Actions auto-fetches full articles as Markdown

```
You: python web_digest.py "TSMC supply chain risk"
 ↓
GitHub Issue created with 10 scored articles
 ↓
You check 3 articles on your phone, close the Issue
 ↓
GitHub Actions saves full Markdown to captures/
```

## Quick Start

### Option A: CLI Mode (run on your machine)

```bash
git clone https://github.com/notoriouslab/issueops-digest.git
cd issueops-digest
pip install -r requirements.txt
cp .env.example .env
# Fill in your API keys in .env
python web_digest.py "your search keywords"
```

### Option B: Zero-Server Mode (GitHub Actions only)

1. Fork this repo (keep it **private** — your Issues contain personal search data)
2. Go to **Settings → Secrets and variables → Actions**
3. Add these secrets: `BRAVE_API_KEY`, `GEMINI_API_KEY`, `GITHUB_TOKEN`
4. Optional: `TAVILY_API_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
5. Edit `config.yaml` to set your topics
6. The scheduled search runs daily, or trigger manually via **Actions → Scheduled Search → Run workflow**

## API Keys

| Service | Required | Free Tier | Get it |
|---------|----------|-----------|--------|
| **Brave Search** | Yes | ~1000 calls/mo ($5 credit, requires card) | [brave.com/search/api](https://brave.com/search/api/) |
| **Gemini** | Yes | 1500 req/day | [aistudio.google.com/apikey](https://aistudio.google.com/apikey) |
| **GitHub Token** | Yes | Free | [github.com/settings/tokens](https://github.com/settings/tokens) (needs `repo` scope) |
| **Tavily** | No | 1000 calls/mo | [tavily.com](https://tavily.com/) |
| **Jina Reader** | No | Free (rate-limited) | No signup needed |
| **Telegram Bot** | No | Free | Talk to [@BotFather](https://t.me/BotFather) |

## Telegram Notifications (optional)

If configured, you'll get a ping when:
- **New Issues are ready** — "go check and pick your articles"
- **Capture is complete** — "your Markdown files are saved"

Setup: create a bot via [@BotFather](https://t.me/BotFather), get your Chat ID, add both as GitHub Secrets. That's it — no server, no polling, just two one-line `curl` calls in the Actions workflow.

Not configured? Everything still works, you just won't get push notifications.

## Configuration

Edit `config.yaml`:

```yaml
# Topics for scheduled search
topics:
  - "AI agent framework"
  - "台積電 供應鏈"

# Weight profile: default, news, research
weight_profile: default

# Your trusted sources (higher = more trusted, max 15)
authority_domains:
  bloomberg.com: 15
  reuters.com: 12
```

### Weight Profiles

| Profile | Best for | Behavior |
|---------|----------|----------|
| `default` | General use | Balanced scoring |
| `news` | Breaking news | Recency weighted higher |
| `research` | Deep research | Evergreen content protected from time decay |

```bash
python web_digest.py "quantum computing" --profile=research
```

## Security

> ⚠️ **Use a private repo.** Your Issues contain search keywords, selected articles, and personal notes.

- API keys: stored in `.env` (local) or GitHub Secrets (Actions) — never committed
- `.env` is pre-configured in `.gitignore`
- The app checks on startup that `.env` is gitignored and warns you if not

## Quota Tracking

In CLI mode, the app tracks your API usage in `.usage_stats.json` (gitignored) and warns you:
- **80%**: yellow warning
- **95%**: red alert

Limits are configurable in `config.yaml` under `quotas`.

## How the scoring works

1. **Brave Search** fetches news + web results; **Tavily** (optional) adds advanced search
2. Results are merged via **round-robin interleaving** so no single source dominates
3. **Jina Reader** fetches full text from quality platforms (Substack, Medium, etc.)
4. **Gemini AI** scores each result for relevance (0-10), flags duplicates
5. A **fuzzy keyword filter** (bilingual, order-independent) penalizes off-topic results
6. **Authority bonus** from your configured trusted domains
7. **Recency scoring** with evergreen protection in `research` mode

## License

MIT

## Credits

Built with the help of AI. Inspired by the belief that anyone with an idea can build useful tools — no CS degree required.
