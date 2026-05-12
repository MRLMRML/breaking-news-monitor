# Breaking News Monitor

[![收录于 JerryKing's Trove](https://img.shields.io/badge/收录于-JerryKing's%20Trove-blue)](https://github.com/MRLMRML/JerryKing-s-Trove)

A lightweight, zero-cost breaking news detection skill for AI agents. Designed for high-frequency polling (every 5 minutes) with minimal token consumption.

## How It Works

```
Agent triggers skill (every ~5 min)
        ↓
Fetch 6 sources in parallel (~1.3s)
├── Google News EN  (aggregates global English media)
├── Google News CN  (aggregates Chinese media)
├── 中新网          (China News Service)
├── NPR             (US public radio)
├── 财联社快讯       (CLS Telegraph, web scrape)
└── 澎湃新闻        (The Paper, web scrape)
        ↓
3-layer detection engine
├── Layer 1: Word-boundary regex matching (EN) + context patterns (CN)
├── Layer 2: Negative pattern filtering (removes metaphorical uses)
└── Layer 3: Multi-source corroboration (2+ sources = higher confidence)
        ↓
Output
├── No breaking  → "NO_BREAKING" (~0 tokens)
└── Breaking     → "BREAKING|S1|source,source|10:25 UTC|headline|url"
```

## Quick Start

```bash
# Run the monitor
python3 scripts/check.py

# Output when nothing detected
NO_BREAKING

# Output when breaking news found
BREAKING|S1|中新网|08:39 UTC|台湾台东县海域发生5.1级地震 福建多地震感明显|https://www.chinanews.com.cn/...
```

## Architecture

### Why This Approach?

| Requirement | Solution |
|---|---|
| **Low token cost** | Pure script — zero LLM calls, zero API fees |
| **Fast response** | Parallel HTTP fetch + lightweight XML parsing (~1.3s) |
| **Source authority** | Google News (aggregates Reuters, AP, BBC, etc.), 中新网, NPR, 财联社 |
| **No rate limiting** | RSS feeds are designed for polling; web scrape targets have generous limits |
| **China accessible** | All 6 sources verified accessible from mainland China |

### 3-Layer Detection Engine

**Layer 1 — Keyword Matching**

English keywords use regex word boundaries (`\b`) to prevent substring false positives:

```python
# ❌ v1: "Tech stocks crash amid..." matches "crash" (substring)
if "crash" in title

# ✅ v2: Only matches "crash" as a whole word
if re.search(r'\bcrash\b', title.lower())
```

Chinese keywords use context patterns — the keyword must appear in an event-describing context:

```python
# ❌ "地震技术获突破" — "地震" in a technology context → not breaking
# ✅ "台湾发生5.1级地震" — "地震" + "级地震" pattern → breaking
```

**Layer 2 — Negative Pattern Filtering**

Metaphorical and non-event uses are filtered out:

| False Positive | Pattern | Result |
|---|---|---|
| "Tech stocks crash amid..." | `crash` not preceded by event context | Filtered |
| "Market crash course for beginners" | `crash\s+course` | Filtered |
| "Coach resigns after losing season" | `resigns\s+after` | Filtered |
| "Film festival bombing at the box office" | `bombing\s+(at\s+the\s+)?box\s+office` | Filtered |
| "Invasion of privacy lawsuit" | `invasion\s+of\s+privacy` | Filtered |
| "Earthquake-proof building technology" | `earthquake[-\s]proof` | Filtered |

**Layer 3 — Multi-Source Corroboration**

When the same event is reported by 2+ independent sources within the time window, confidence increases. Items from corroborated sources get a severity boost (S2 → S1).

### Severity Levels

| Level | Meaning | Examples |
|---|---|---|
| **S1** | Highest priority — immediate attention | Earthquake 7+, terror attack, market crash, nuclear test |
| **S2** | Significant event | Wildfire, hostage situation, cyberattack, epidemic |
| **S3** | Notable but lower urgency | Political resignation, sanctions, trade war |

## Integration

### As an OpenCode Skill

The skill auto-triggers when the agent detects keywords like "breaking news", "news check", "突发新闻", etc. The `SKILL.md` file contains the triggering description.

### As a Standalone Script

```bash
# One-shot check
python3 scripts/check.py

# Cron job (every 5 minutes)
*/5 * * * * python3 /path/to/scripts/check.py >> /var/log/news-monitor.log 2>&1

# Shell loop
while true; do python3 scripts/check.py; sleep 300; done
```

### Agent Integration Pattern

```python
import subprocess

result = subprocess.run(
    ["python3", "scripts/check.py"],
    capture_output=True, text=True, timeout=30
)

for line in result.stdout.strip().split('\n'):
    if line.startswith('BREAKING|'):
        _, severity, sources, time, headline, url = line.split('|', 5)
        alert_user(f"🚨 [{severity}] {headline}")
    elif line.startswith('SOURCE_WARN|'):
        _, source, error = line.split('|', 2)
        log_warning(f"Source {source} is down: {error}")
```

## Configuration

### Adding Sources

Edit `FEEDS` or `WEB_SCRAPE_TARGETS` in `scripts/check.py`:

```python
FEEDS = [
    ("My Source", "https://example.com/rss.xml"),
]

WEB_SCRAPE_TARGETS = [
    ("My Scraper", "https://example.com/news", r'class="headline"[^>]*>([^<]+)'),
]
```

### Adding Keywords

Add to `EN_KEYWORDS` (severity 1-3) or `CN_KEYWORDS` (with context patterns):

```python
EN_KEYWORDS["new_keyword"] = 1  # 1=highest, 3=lowest

CN_KEYWORDS["新关键词"] = (1, ["发生新关键词", "新关键词致"])
```

### Tuning Sensitivity

- `TIME_WINDOW_MINUTES` (default: 20) — how far back to look for news
- `MAX_KNOWN_HASHES` (default: 300) — dedup memory size
- Severity thresholds in the corroboration logic

## State Management

Runtime state is stored in `state.json`:

```json
{
  "known_hashes": ["a33bcfd63bf9", "..."],
  "last_check": "2026-05-12T08:57:25Z",
  "source_health": {
    "中新网": {"status": "ok", "last_ok": "...", "items": 25},
    "NPR": {"status": "error", "error": "timeout", "last_ok": "..."}
  }
}
```

- Atomic writes (temp file + rename) prevent corruption
- Safe to delete to reset all state
- Source health tracked for monitoring

## Limitations

- **RSS latency**: 5-15 minute delay vs real-time wire services
- **Keyword coverage**: Novel breaking events may not match any keyword
- **Google News dependency**: Uses undocumented RSS endpoint (could change)
- **No verification**: Cannot distinguish real news from rumors/headline errors
- **Chinese NLP**: Context patterns are regex-based, not true NLP understanding

## License

MIT
