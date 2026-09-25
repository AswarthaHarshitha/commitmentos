"""Deduplication primitives (pure functions).

The same commitment routinely arrives as: the original mail, a reminder mail, a forward, a
calendar invite. We must not create one obligation per message. Matching is deterministic:

1. exact idempotency  - (user, source_type, external_id) unique constraint on ``sources``
2. fingerprint        - hash(normalised title tokens + local due *date*)
3. fuzzy match        - token/character similarity + sender + thread + due-date proximity
                        (``score_candidate`` below; the DB lookup lives in ingestion)

No embeddings / LLM calls: a duplicate decision must be explainable and reproducible.
"""

from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher

_PREFIX_RE = re.compile(
    r"^\s*(?:(?:re|fwd?|fw|reminder|final reminder|final notice|action required|urgent|important|fyi|"
    r"gentle reminder|friendly reminder|second notice)\s*[:\-–]\s*)+",
    re.IGNORECASE,
)
_NON_WORD = re.compile(r"[^\w\s]", re.UNICODE)
_STOPWORDS = frozenset(
    [
     "a", "an", "the", "and", "or", "but", "of", "to", "for", "from", "by", "on", "in", "at", "with",
     "your", "you", "our", "we", "i", "me", "my", "please", "kindly", "just", "need", "needs",
     "needed", "must", "should", "would", "could", "will", "shall", "can", "may", "is", "are", "be",
     "been", "being", "this", "that", "these", "those", "it", "its", "as", "into", "about",
     "regarding", "re", "before", "after", "until", "till", "than", "then", "so", "if", "asap",
     "soon", "today", "tomorrow", "tonight",
    ]
)


def _stem(token: str) -> str:
    """Tiny suffix stripper so submit/submitted/submitting and document/documents collapse."""
    if len(token) <= 4:
        return token
    for suffix in ("ing", "ed", "es", "s"):
        if token.endswith(suffix) and len(token) - len(suffix) >= 3:
            stem = token[: -len(suffix)]
            if len(stem) >= 4 and stem[-1] == stem[-2] and suffix in ("ing", "ed"):
                stem = stem[:-1]  # submitt -> submit
            return stem
    return token


def normalize_title(title: str) -> str:
    text = unicodedata.normalize("NFKC", title or "").lower()
    text = _PREFIX_RE.sub("", text)
    text = _NON_WORD.sub(" ", text)
    return re.sub(r"\s+", " ", text).strip()


def title_tokens(title: str) -> list[str]:
    return sorted({_stem(t) for t in normalize_title(title).split() if t not in _STOPWORDS and len(t) > 1})


def fingerprint(title: str, due_local_date: date | None) -> str:
    basis = " ".join(title_tokens(title)) + "|" + (due_local_date.isoformat() if due_local_date else "nodate")
    return hashlib.sha256(basis.encode()).hexdigest()


def content_hash(text: str) -> str:
    return hashlib.sha256(re.sub(r"\s+", " ", text or "").strip().lower().encode()).hexdigest()


def _matched_tokens(ta: set[str], tb: set[str]) -> int:
    """Count tokens present in both sets, tolerating typos (0.85 per-token similarity), each token used once."""
    remaining = set(tb)
    matches = 0
    for x in sorted(ta):
        if x in remaining:
            remaining.discard(x)
            matches += 1
            continue
        best = max(remaining, key=lambda y: SequenceMatcher(None, x, y).ratio(), default=None)
        if best is not None and SequenceMatcher(None, x, best).ratio() >= 0.85:
            remaining.discard(best)
            matches += 1
    return matches


def title_similarity(a: str, b: str) -> float:
    """Token-set similarity of two titles in [0, 1] (Jaccard over *significant* words).

    Deliberately NOT a character-level ratio: unrelated English strings score ~0.4 on those, and strings that
    merely share a verb ("Renew passport" / "Renew car insurance") score high, either of which would merge two
    genuinely different commitments. Shared meaningful words are the evidence; typos are tolerated per word.
    """
    ta, tb = set(title_tokens(a)), set(title_tokens(b))
    if not ta or not tb:
        return 0.0
    matched = _matched_tokens(ta, tb)
    return matched / (len(ta) + len(tb) - matched)


@dataclass(frozen=True)
class MatchScore:
    score: float
    reasons: tuple[str, ...]
    deadline_conflict: bool = False


def score_candidate(
    *,
    new_title: str,
    new_due: datetime | None,
    new_sender: str | None,
    new_thread: str | None,
    existing_title: str,
    existing_due: datetime | None,
    existing_sender: str | None,
    existing_threads: set[str],
) -> MatchScore:
    """Score how likely a new detection is the *same* obligation as an existing one.

    Title similarity carries the weight; sender / thread / due-date agreement add confidence.
    Two obligations with clearly different deadlines are only merged when they share a thread
    (a "deadline moved" email), and that case is flagged as a conflict for the audit trail.
    """
    sim = title_similarity(new_title, existing_title)
    reasons = [f"title_similarity={sim:.2f}"]
    if sim < MIN_TITLE_SIMILARITY:
        # Context (same sender, same thread, same day) can corroborate a match but never create one:
        # two different commitments in one email thread must stay two obligations.
        return MatchScore(round(0.6 * sim, 4), (*reasons, "title_too_different"), False)
    score = 0.6 * sim

    same_thread = bool(new_thread and new_thread in existing_threads)
    if same_thread:
        score += 0.25
        reasons.append("same_thread")
    if new_sender and existing_sender and new_sender.lower() == existing_sender.lower():
        score += 0.10
        reasons.append("same_sender")

    conflict = False
    if new_due and existing_due:
        delta = abs(new_due - existing_due)
        if delta <= timedelta(hours=36):
            score += 0.15 if delta <= timedelta(hours=1) else 0.10
            reasons.append("due_date_matches")
        elif same_thread:
            conflict = True
            reasons.append("deadline_conflict_same_thread")
        else:
            score -= 0.35
            reasons.append("due_date_differs")
    elif new_due is None and existing_due is None:
        score += 0.05
        reasons.append("both_without_deadline")
    else:
        reasons.append("one_side_missing_deadline")

    return MatchScore(round(max(0.0, min(score, 1.0)), 4), tuple(reasons), conflict)


DUPLICATE_THRESHOLD = 0.72
MIN_TITLE_SIMILARITY = 0.6
