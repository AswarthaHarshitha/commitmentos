"""Deduplication primitives: the same commitment phrased four ways must collapse; different ones must not."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from app.services.dedup import (
    DUPLICATE_THRESHOLD,
    fingerprint,
    normalize_title,
    score_candidate,
    title_similarity,
    title_tokens,
)

DUE = datetime(2026, 9, 25, 21, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Re: Fwd: Please submit the tax form!", "please submit the tax form"),
        ("REMINDER: Submit your tax forms", "submit your tax forms"),
        ("  Action Required -  Confirm   interview availability ", "confirm interview availability"),
        ("Fw: FW: Urgent: pay invoice #4521", "pay invoice 4521"),
    ],
)
def test_normalize_strips_reply_prefixes_punctuation_and_case(raw, expected):
    assert normalize_title(raw) == expected


def test_tokens_ignore_stopwords_and_collapse_inflections():
    assert title_tokens("Please submit the tax forms") == title_tokens("Submitted: your tax form")
    assert title_tokens("Renewing subscription") == title_tokens("Renewed subscription")


def test_original_reminder_and_forward_share_a_fingerprint():
    d = date(2026, 9, 25)
    original = fingerprint("Please submit your tax form", d)
    reminder = fingerprint("Reminder: Submit the tax form", d)
    forward = fingerprint("Fwd: Please submit your tax form", d)
    assert original == reminder == forward


def test_fingerprint_separates_different_dates_and_different_titles():
    d1, d2 = date(2026, 9, 25), date(2026, 9, 26)
    assert fingerprint("Submit tax form", d1) != fingerprint("Submit tax form", d2)
    assert fingerprint("Submit tax form", d1) != fingerprint("Pay electricity bill", d1)
    assert fingerprint("Submit tax form", None) != fingerprint("Submit tax form", d1)


def test_similarity_is_high_for_rewording_low_for_unrelated():
    assert title_similarity("Submit the signed offer letter", "Submit signed offer letter") > 0.9
    assert title_similarity("Submit tax form", "Book dentist appointment") < 0.35


def _score(**kw):
    base = dict(
        new_title="Submit tax form", new_due=DUE, new_sender="hr@example.org", new_thread="t1",
        existing_title="Please submit the tax form", existing_due=DUE, existing_sender="hr@example.org",
        existing_threads={"t1"},
    )
    base.update(kw)
    return score_candidate(**base)


def test_same_commitment_from_same_sender_and_thread_scores_as_duplicate():
    s = _score()
    assert s.score >= DUPLICATE_THRESHOLD
    assert "same_thread" in s.reasons and "due_date_matches" in s.reasons


def test_a_reminder_from_a_different_sender_with_same_title_and_deadline_still_matches():
    s = _score(new_sender="noreply@example.net", new_thread="other", existing_threads={"t1"})
    assert s.score >= DUPLICATE_THRESHOLD


def test_same_title_but_clearly_different_deadline_is_a_distinct_obligation():
    s = _score(new_thread="other", existing_threads={"t1"}, new_due=DUE + timedelta(days=30))
    assert s.score < DUPLICATE_THRESHOLD
    assert "due_date_differs" in s.reasons


def test_deadline_moved_within_the_same_thread_still_merges_but_is_flagged():
    s = _score(new_due=DUE + timedelta(days=7))
    assert s.deadline_conflict is True
    assert s.score >= DUPLICATE_THRESHOLD


def test_unrelated_titles_never_reach_the_threshold_even_with_everything_else_equal():
    s = _score(new_title="Book dentist appointment")
    assert s.score < DUPLICATE_THRESHOLD


def test_one_side_missing_a_deadline_is_neutral_not_a_match_by_itself():
    s = _score(new_due=None, new_thread=None, new_sender=None, existing_sender=None, existing_threads=set())
    assert 0 < s.score < 1
    assert "one_side_missing_deadline" in s.reasons


def test_context_can_corroborate_a_match_but_never_create_one():
    """Regression: 'Book a dentist appointment' vs 'Submit internship documents' scored 0.749 (> threshold) purely from
    same thread + same sender + same day, so two real commitments would have been silently merged into one."""
    m = _score(new_title="Book a dentist appointment", existing_title="Submit internship documents")
    assert m.score < DUPLICATE_THRESHOLD and "title_too_different" in m.reasons
    assert title_similarity("Book a dentist appointment", "Submit internship documents") == 0.0
    # ...but genuinely related titles still merge under the same context
    assert _score(new_title="Submit internship documents", existing_title="Submit signed internship documents").score >= DUPLICATE_THRESHOLD


@pytest.mark.parametrize("a,b", [
    ("Submit tax form", "Pay electricity bill"), ("Renew passport", "Renew car insurance"), ("Call the bank", "Call the dentist"),
])
def test_titles_sharing_only_a_generic_verb_or_nothing_never_merge_even_with_perfect_context(a, b):
    assert _score(new_title=a, existing_title=b).score < DUPLICATE_THRESHOLD


def test_a_shared_word_alone_is_not_enough_when_the_rest_differs():
    assert title_similarity("Renew passport", "Renew car insurance") < 0.6  # only the generic verb is shared


def test_typos_are_tolerated_per_word_without_loosening_the_whole_title():
    assert title_similarity("Submit signed documnets", "Submit signed documents") == 1.0
    assert title_similarity("Renew car insurance", "Renew car registration") < 0.6  # two different renewals


def test_containment_of_a_shorter_title_in_a_longer_one_needs_most_words_to_match():
    assert title_similarity("Pay electricity bill", "Pay the electricity bill for September") >= 0.75
    assert title_similarity("Pay bill", "Pay electricity bill for September and August") < 0.6
