from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta

STOP_WORDS = {
    "a", "an", "the", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "up", "about", "into", "through", "during",
    "is", "was", "are", "were", "be", "been", "being", "have", "has", "had",
    "do", "does", "did", "will", "would", "could", "should", "may", "might",
    "i", "me", "my", "we", "our", "you", "your", "he", "she", "it", "they",
    "where", "when", "what", "which", "who", "how", "session", "that", "this",
}

KNOWN_EXTENSIONS = {
    ".py", ".yaml", ".yml", ".ts", ".tsx", ".js", ".jsx", ".go",
    ".json", ".md", ".sql", ".sh", ".toml", ".rs", ".cpp", ".c", ".h",
    ".rb", ".java", ".cs", ".php", ".swift", ".kt", ".html", ".css",
}

MONTHS = [
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
]


@dataclass
class ParsedQuery:
    clean_query: str
    date_after: int | None = None
    date_before: int | None = None
    tool_hint: str | None = None
    path_hint: str | None = None
    has_path_hint: bool = False


def extract_filters(query: str) -> ParsedQuery:
    result = ParsedQuery(clean_query=query)
    q = query.lower()
    now = datetime.now()

    # --- Date patterns ---
    if "last week" in q:
        result.date_after = int((now - timedelta(days=7)).timestamp())
        q = q.replace("last week", "")

    elif "yesterday" in q:
        yesterday = now - timedelta(days=1)
        result.date_after = int(yesterday.replace(hour=0, minute=0, second=0, microsecond=0).timestamp())
        result.date_before = int(yesterday.replace(hour=23, minute=59, second=59, microsecond=0).timestamp())
        q = q.replace("yesterday", "")

    # "before <date>" pattern
    before_m = re.search(r"\bbefore\s+(\w+(?:\s+\d+)?)\b", q)
    if before_m:
        try:
            from dateutil import parser as dateutil_parser
            result.date_before = int(dateutil_parser.parse(before_m.group(1), default=now).timestamp())
            q = q[: before_m.start()] + " " + q[before_m.end():]
        except Exception:
            pass

    # "in <month>" pattern
    month_pattern = r"\bin\s+(" + "|".join(MONTHS) + r")\b"
    month_m = re.search(month_pattern, q)
    if month_m and result.date_after is None and result.date_before is None:
        try:
            from dateutil import parser as dateutil_parser
            from dateutil.relativedelta import relativedelta
            month_name = month_m.group(1)
            month_start = dateutil_parser.parse(f"1 {month_name} {now.year}", default=now)
            if month_start > now:
                month_start = month_start.replace(year=now.year - 1)
            month_end = month_start + relativedelta(months=1) - timedelta(seconds=1)
            result.date_after = int(month_start.timestamp())
            result.date_before = int(month_end.timestamp())
            q = q[: month_m.start()] + " " + q[month_m.end():]
        except Exception:
            pass

    # --- Tool hints ---
    if re.search(r"\bcursor\b", q):
        result.tool_hint = "cursor"
        q = re.sub(r"\bcursor(?:\s+session)?\b", " ", q)
    elif re.search(r"\bclaude[\s-]code\b|\bclaude\b", q):
        result.tool_hint = "claude-code"
        q = re.sub(r"\bclaude[\s-]code(?:\s+session)?\b|\bclaude\b", " ", q)

    # --- Path hints ---
    tokens = q.split()
    path_tokens = []
    remaining = []
    for token in tokens:
        clean = token.strip(".,!?;:")
        if "/" in clean or any(clean.endswith(ext) for ext in KNOWN_EXTENSIONS):
            path_tokens.append(clean)
        else:
            remaining.append(token)

    if path_tokens:
        result.path_hint = path_tokens[0]
        result.has_path_hint = True
        q = " ".join(remaining)

    # --- Stop word removal ---
    words = [w for w in q.split() if w.lower() not in STOP_WORDS and len(w) > 1]
    result.clean_query = " ".join(words)

    return result
