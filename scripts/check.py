#!/usr/bin/env python3
"""
Breaking News Monitor v2.

Usage: python3 check.py
Output: NO_BREAKING | SOURCE_WARN|... | BREAKING|severity|sources|time|headline|url
"""

import urllib.request
import xml.etree.ElementTree as ET
import hashlib
import json
import os
import re
import tempfile
from datetime import datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor, as_completed
from email.utils import parsedate_to_datetime

SKILL_DIR = os.path.dirname(os.path.dirname(__file__))
STATE_FILE = os.path.join(SKILL_DIR, "state.json")
MAX_KNOWN_HASHES = 300
TIME_WINDOW_MINUTES = 20

FEEDS = [
    ("Google News EN", "https://news.google.com/rss?hl=en-US&gl=US&ceid=US:en"),
    ("Google News CN", "https://news.google.com/rss?hl=zh-CN&gl=CN&ceid=CN:zh-Hans"),
    ("中新网", "https://www.chinanews.com.cn/rss/scroll-news.xml"),
    ("NPR", "https://feeds.npr.org/1001/rss.xml"),
]

WEB_SCRAPE_TARGETS = [
    ("财联社快讯", "https://www.cls.cn/telegraph",
     r'"title"\s*:\s*"([^"]{8,120})"'),
    ("澎湃新闻", "https://www.thepaper.cn/",
     r'class="[^"]*news[^"]*"[^>]*>([^<]{8,100})'),
]

# 3-layer detection: word-boundary regex → negative patterns → severity
# English keywords: regex \b word boundaries prevent substring false positives
EN_KEYWORDS = {
    "earthquake": 1, "tsunami": 1, "hurricane": 1, "typhoon": 1,
    "tornado": 1, "volcanic eruption": 1, "landslide": 1,
    "wildfire": 2, "flooding": 2, "cyclone": 1,
    "terror attack": 1, "terrorist attack": 1, "bombing": 1,
    "mass shooting": 1, "school shooting": 1,
    "coup": 1, "assassination": 1, "assassinated": 1,
    "invasion": 1, "airstrike": 1, "missile strike": 1,
    "military strike": 1, "car bomb": 1, "suicide bomb": 1,
    "hostage": 2, "ceasefire": 2, "chemical weapon": 1,
    "plane crash": 1, "aircraft crash": 1, "train derailment": 1,
    "ship sinks": 1, "capsized": 1, "midair collision": 1,
    "pandemic": 1, "outbreak": 2, "epidemic": 2, "health emergency": 1,
    "market crash": 1, "stock crash": 1, "circuit breaker": 1,
    "bank collapse": 1, "bank failure": 1, "currency crisis": 1,
    "impeach": 1, "impeached": 1, "state of emergency": 1,
    "martial law": 1, "nuclear test": 1, "nuclear strike": 1,
    "resigns": 3,  # severity 3 = lowest priority, high false positive risk
    "nuclear fusion": 1, "artificial general intelligence": 1,
    "cyberattack": 1, "cyber attack": 1, "ransomware attack": 1,
    "power grid failure": 1, "internet shutdown": 1,
    "infrastructure collapse": 1, "bridge collapse": 1,
    "dam collapse": 1, "mine collapse": 1,
    "trade war": 2, "sanctions": 3, "embargo": 3,
    "abdication": 1, "coronation": 2,
}

# Regex patterns that filter out metaphorical/non-event uses of keywords
EN_NEGATIVE_PATTERNS = [
    r"(?i)crash\s+course",
    r"(?i)crash\s+(diet|dieting|test|landing|course)",
    r"(?i)breaking\s+(down|into|the|new|ground|through|point|news\s+down)",
    r"(?i)invasion\s+of\s+privacy",
    r"(?i)outbreak\s+of\s+(creativity|joy|laughter|enthusiasm)",
    r"(?i)bombing\s+(at\s+the\s+)?(box\s+office|run|range)",
    r"(?i)hurricane\s+of\s+(emotions|feelings|protests|criticism)",
    r"(?i)tornado\s+of\s+(activity|emotion|controversy)",
    r"(?i)earthquake[-\s](proof|resistant|prone|zone|preparedness)",
    r"(?i)market\s+crash\s+(course|test|diet)",
    r"(?i)resign(s|ed|ing)\s+(after|following|over|due\s+to|amid\s+a)",
    r"(?i)bank\s+(of|account|statement|transfer|holiday)",
    r"(?i)coup\s+of\s+(the|a)",
    r"(?i)sanction(s|ed|ing)\s+(against\s+)?(a\s+)?(team|player|club|athlete)",
    r"(?i)mass\s+shooting\s+(of|for|star)",
    r"(?i)wild\s*fire\s+(season|danger|risk|warning|advisory|watch)",
    r"(?i)eruption\s+of\s+(laughter|applause|anger|violence|protests)",
    r"(?i)outbreak\s+(reported|investigated|studied|examined|analyzed)\s+(in|by|at)",
    r"(?i)circuit\s+breaker\s+(panel|box|wiring|installation|design)",
]

# CN keywords: (severity, [context_patterns]).
# Empty context_patterns = keyword alone is sufficient.
CN_KEYWORDS = {
    "地震": (1, ["发生地震", "级地震", "地震已造成", "地震预警", "强震",
                 "发生.*地震", "地震.*级", "余震"]),
    "海啸": (1, ["海啸预警", "海啸已", "发生海啸", "海啸袭击"]),
    "台风": (1, ["台风.*登陆", "台风.*级", "超强台风", "台风.*预警",
                 "台风.*袭击", "台风.*升级"]),
    "飓风": (1, ["飓风.*袭击", "飓风.*登陆", "飓风.*级"]),
    "龙卷风": (1, ["龙卷风袭击", "龙卷风造成", "发生龙卷风"]),
    "火山喷发": (1, []),
    "泥石流": (1, ["泥石流致", "泥石流造成", "发生泥石流"]),
    "山体滑坡": (1, ["山体滑坡致", "山体滑坡造成", "发生山体滑坡"]),
    "洪水致": (1, []),
    "恐怖袭击": (1, []),
    "恐袭": (1, ["恐袭致", "恐袭造成", "发生恐袭", "遭恐袭"]),
    "爆炸案": (1, []),
    "枪击案": (1, []),
    "大规模枪击": (1, []),
    "政变": (1, ["发生政变", "政变致", "军事政变"]),
    "暗杀": (1, ["遭暗杀", "暗杀事件", "暗杀致"]),
    "遇刺": (1, ["遇刺身亡", "遇刺受伤", "遭遇刺"]),
    "入侵": (1, ["军事入侵", "遭入侵", "入侵.*领土"]),
    "军事打击": (1, ["发动军事打击", "遭军事打击"]),
    "空袭": (1, ["发动空袭", "遭空袭", "空袭致", "空袭造成"]),
    "导弹袭击": (1, []),
    "导弹试射": (1, []),
    "坠机": (1, ["发生坠机", "坠机致", "坠机事故"]),
    "飞机失事": (1, []),
    "列车脱轨": (1, ["列车脱轨致", "发生列车脱轨"]),
    "沉船": (1, ["发生沉船", "沉船事故", "沉船致"]),
    "倾覆": (1, ["船.*倾覆", "发生倾覆"]),
    "疫情爆发": (1, []),
    "传染病": (2, ["传染病爆发", "传染病疫情", "新型传染病"]),
    "公共卫生紧急": (1, []),
    "股市暴跌": (1, []),
    "股市崩盘": (1, []),
    "熔断": (1, ["触发熔断", "熔断机制"]),
    "银行倒闭": (1, []),
    "货币危机": (1, []),
    "弹劾": (1, ["弹劾.*总统", "弹劾案", "弹劾.*投票"]),
    "紧急状态": (1, ["宣布紧急状态", "进入紧急状态"]),
    "戒严": (1, ["宣布戒严", "实施戒严"]),
    "核试验": (1, ["进行核试验", "核试验.*成功", "核试验.*失败"]),
    "核聚变": (2, ["核聚变.*突破", "核聚变.*实现"]),
    "通用人工智能": (2, ["通用人工智能.*实现", "AGI.*突破"]),
    "网络攻击": (1, ["遭网络攻击", "网络攻击致", "大规模网络攻击"]),
    "电网瘫痪": (1, []),
    "大坝溃堤": (1, []),
    "桥梁坍塌": (1, []),
    "矿难": (1, ["矿难致", "发生矿难", "矿难.*人"]),
    "制裁": (3, ["宣布制裁", "实施制裁", "制裁.*升级"]),
    "贸易战": (2, ["贸易战.*升级", "贸易战.*爆发"]),
    "辞职": (3, ["总统.*辞职", "总理.*辞职", "首相.*辞职",
                 "主席.*辞职", "宣布辞职"]),
}

def load_state():
    try:
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "known_hashes": [],
            "last_check": None,
            "source_health": {},
            "event_history": {},
        }


def save_state(state):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        dir=os.path.dirname(STATE_FILE), suffix=".tmp"
    )
    try:
        with os.fdopen(fd, 'w') as f:
            json.dump(state, f)
        os.replace(tmp, STATE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def fetch_feed(name, url, timeout=8):
    items = []
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; NewsMonitor/2.0)'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = resp.read()
        root = ET.fromstring(data)

        for item in root.findall('.//item'):
            title = (item.findtext('title') or '').strip()
            link = (item.findtext('link') or '').strip()
            pub = (item.findtext('pubDate') or '').strip()
            if title:
                items.append((title, link, pub, name))

        ns = {'a': 'http://www.w3.org/2005/Atom'}
        for entry in root.findall('.//a:entry', ns):
            title = (entry.findtext('a:title', '', ns)).strip()
            link_el = entry.find('a:link', ns)
            link = link_el.get('href', '') if link_el is not None else ''
            pub = (entry.findtext('a:updated', '', ns)).strip()
            if title:
                items.append((title, link, pub, name))
    except Exception as e:
        return items, f"{type(e).__name__}: {e}"
    return items, None


def fetch_web_scrape(name, url, pattern, timeout=8):
    items = []
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0 (compatible; NewsMonitor/2.0)'
        })
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            html = resp.read().decode('utf-8', errors='ignore')
        for match in re.finditer(pattern, html):
            title = match.group(1).strip()
            if len(title) >= 8:
                now = datetime.now(timezone.utc).strftime(
                    "%a, %d %b %Y %H:%M:%S +0000"
                )
                items.append((title, url, now, name))
    except Exception as e:
        return items, f"{type(e).__name__}: {e}"
    return items, None


def parse_date(s):
    if not s:
        return None
    try:
        dt = parsedate_to_datetime(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        pass
    for fmt in ("%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%dT%H:%M:%SZ",
                "%Y-%m-%d %H:%M:%S %z", "%Y-%m-%d %H:%M:%S"):
        try:
            dt = datetime.strptime(s, fmt)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.astimezone(timezone.utc)
        except ValueError:
            continue
    return None


def is_recent(pub_date_str):
    dt = parse_date(pub_date_str)
    if dt is None:
        return False
    return dt >= datetime.now(timezone.utc) - timedelta(
        minutes=TIME_WINDOW_MINUTES
    )


def headline_hash(title):
    return hashlib.md5(title.encode()).hexdigest()[:12]


def _check_negative_patterns(title):
    for pat in EN_NEGATIVE_PATTERNS:
        if re.search(pat, title):
            return True
    return False


def _check_en_keywords(title):
    t = title.lower()
    for kw, severity in EN_KEYWORDS.items():
        if re.search(r'\b' + re.escape(kw) + r'\b', t):
            if _check_negative_patterns(title):
                continue
            return severity
    return 0


def _check_cn_keywords(title):
    for kw, (severity, context_patterns) in CN_KEYWORDS.items():
        if kw not in title:
            continue
        if not context_patterns:
            return severity
        for pat in context_patterns:
            if re.search(pat, title):
                return severity
    return 0


def detect_breaking(title):
    sev = _check_en_keywords(title)
    if sev > 0:
        return True, sev, "en"
    sev = _check_cn_keywords(title)
    if sev > 0:
        return True, sev, "cn"
    return False, 0, None


def extract_event_keywords(title):
    stop = {
        'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been',
        'has', 'have', 'had', 'do', 'does', 'did', 'will', 'would',
        'could', 'should', 'may', 'might', 'shall', 'can', 'to', 'of',
        'in', 'on', 'at', 'for', 'with', 'from', 'by', 'as', 'into',
        'about', 'between', 'through', 'after', 'before', 'during',
        'and', 'but', 'or', 'not', 'no', 'its', 'it', 'he', 'she',
        'they', 'we', 'you', 'i', 'my', 'his', 'her', 'their', 'our',
        'this', 'that', 'these', 'those', 'said', 'says', 'new',
        'also', 'more', 'most', 'than', 'up', 'out', 'just',
    }
    words = re.findall(r'[a-zA-Z\u4e00-\u9fff]{2,}', title.lower())
    return set(w for w in words if w not in stop and len(w) > 2)


def find_corroboration(items_by_title):
    event_groups = []
    processed = set()

    titles = list(items_by_title.keys())
    for i, t1 in enumerate(titles):
        if i in processed:
            continue
        kw1 = extract_event_keywords(t1)
        if len(kw1) < 2:
            continue
        group = [t1]
        sources = set([items_by_title[t1][3]])
        processed.add(i)

        for j in range(i + 1, len(titles)):
            if j in processed:
                continue
            kw2 = extract_event_keywords(titles[j])
            overlap = len(kw1 & kw2)
            if overlap >= 2 or (overlap >= 1 and len(kw1) <= 3):
                group.append(titles[j])
                sources.add(items_by_title[titles[j]][3])
                processed.add(j)

        if len(sources) >= 2:
            event_groups.append((group, sources))
    return event_groups


def main():
    state = load_state()
    known = set(state.get("known_hashes", []))
    source_health = state.get("source_health", {})
    warnings = []

    all_items = []
    with ThreadPoolExecutor(
        max_workers=len(FEEDS) + len(WEB_SCRAPE_TARGETS)
    ) as pool:
        futs = {}
        for name, url in FEEDS:
            futs[pool.submit(fetch_feed, name, url)] = name
        for name, url, pattern in WEB_SCRAPE_TARGETS:
            futs[pool.submit(fetch_web_scrape, name, url, pattern)] = name

        for f in as_completed(futs):
            name = futs[f]
            try:
                items, err = f.result()
            except Exception as e:
                items, err = [], f"{type(e).__name__}: {e}"

            all_items.extend(items)

            if err:
                source_health[name] = {
                    "status": "error", "error": err,
                    "last_ok": source_health.get(name, {}).get("last_ok"),
                }
                warnings.append(f"SOURCE_WARN|{name}|{err}")
            else:
                source_health[name] = {
                    "status": "ok",
                    "last_ok": datetime.now(timezone.utc).isoformat(),
                    "items": len(items),
                }

    candidates = []
    for title, link, pub, source in all_items:
        h = headline_hash(title)
        if h in known:
            continue
        if not is_recent(pub):
            continue
        is_brk, severity, lang = detect_breaking(title)
        if not is_brk:
            continue
        dt = parse_date(pub)
        ts = dt.strftime("%H:%M UTC") if dt else "??:??"
        candidates.append({
            "title": title, "link": link, "source": source,
            "time": ts, "severity": severity, "hash": h, "lang": lang,
        })

    title_to_item = {c["title"]: (c["title"], c["link"], c["time"], c["source"])
                     for c in candidates}
    corroboration_groups = find_corroboration(title_to_item)

    corroborated_titles = set()
    for group, sources in corroboration_groups:
        for t in group:
            corroborated_titles.add(t)

    for c in candidates:
        if c["title"] in corroborated_titles and c["severity"] > 1:
            c["severity"] -= 1

    seen_events = {}
    for c in candidates:
        h = c["hash"]
        if h not in seen_events or c["severity"] < seen_events[h]["severity"]:
            seen_events[h] = c

    breaking = sorted(seen_events.values(), key=lambda x: x["severity"])

    new_hashes = [c["hash"] for c in breaking]
    updated = list(known) + new_hashes
    state["known_hashes"] = updated[-MAX_KNOWN_HASHES:]
    state["last_check"] = datetime.now(timezone.utc).isoformat()
    state["source_health"] = source_health
    save_state(state)

    for w in warnings:
        print(w)

    if not breaking:
        print("NO_BREAKING")
    else:
        for c in breaking:
            sources = c["source"]
            for group, srcs in corroboration_groups:
                if c["title"] in group:
                    sources = ",".join(sorted(srcs))
                    break
            sev_label = f"S{c['severity']}"
            print(
                f"BREAKING|{sev_label}|{sources}|"
                f"{c['time']}|{c['title']}|{c['link']}"
            )


if __name__ == "__main__":
    main()
