"""Incoming Detection through the real workflow: n8n -> API -> LLM stand-in -> PostgreSQL -> notification -> Mailpit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from conftest import extraction, ingest_payload, wait_until

INGEST = "commitmentos-ingest"


def ingest(n8n, user, external_id=None, **kw):
    r = n8n.call(INGEST, ingest_payload(user, external_id, **kw))
    assert r.status_code == 200, r.text
    return r.json()


def n8n_execution(n8n_db, execution_id: str):
    return n8n_db.rows('select status, mode, "workflowId" from execution_entity where id = %s', int(execution_id))[0]


def test_a_message_becomes_a_tracked_commitment_and_the_user_is_told(n8n, n8n_db, stub, user, mail, app_db):
    stub.reset(extraction())
    out = ingest(n8n, user, "e2e-happy-1")

    # the workflow's own answer
    assert out["outcome"] == "processed" and out["disposition"] == "OBLIGATION_CREATED" and out["decision"] == "CREATE"
    assert out["obligation_status"] == "OPEN" and out["notifications"] == 2 and out["llm"]["provider"] == "openai_compat"

    # n8n really executed it
    status, mode, workflow = n8n_execution(n8n_db, out["n8n_execution_id"])
    assert (status, mode, workflow) == ("success", "webhook", "cosWfIncoming00001")

    # the API holds the structured obligation, with a deadline computed by CODE (not by the LLM)
    (ob,) = user.obligations()
    assert ob["title"] == "Submit internship documents" and ob["status"] == "OPEN" and ob["due_precision"] == "DATETIME"
    due = datetime.fromisoformat(ob["due_at"])
    assert due.utcoffset() == timedelta(0) and due.hour in (20, 21)  # 17:00 New York, in UTC (EDT or EST)
    assert ob["due_resolution"]["method"] == "RELATIVE"

    # the notification really went out through n8n's SMTP node
    msgs = wait_until(lambda: mail.messages(user.email), what="the detection email in Mailpit")
    assert [m["Subject"] for m in msgs] == ["New commitment: Submit internship documents"]
    text = mail.detail(msgs[0]["ID"])["Text"]
    assert "Mark as done:" in text and f"/obligations/{ob['id']}" in text

    # the run is visible in the automation history, cross-referenced to the n8n execution
    run = user.run(out["n8n_execution_id"])
    assert run["status"] == "SUCCESS" and run["trigger"] == "WEBHOOK"
    assert any(r["status"] == "SUCCESS" and r["trigger"] == "SUBWORKFLOW" for r in user.runs("notification-dispatcher"))

    # ...and the audit trail tells the whole story
    events = [e["event_type"] for e in user.get(f"/api/obligations/{ob['id']}/timeline").json()]
    for expected in ("COMMITMENT_DETECTED", "OBLIGATION_CREATED", "NOTIFICATION_QUEUED", "NOTIFICATION_SENT"):
        assert expected in events


def test_a_duplicate_message_is_recognised_before_the_llm_is_paid_for(n8n, stub, user):
    stub.reset(extraction())
    first = ingest(n8n, user, "e2e-dup-1")
    calls_after_first = len(stub.calls)
    again = ingest(n8n, user, "e2e-dup-1")
    assert first["outcome"] == "processed" and again["outcome"] == "duplicate"
    assert again["obligation_id"] == first["obligation_id"]
    assert len(stub.calls) == calls_after_first  # no second model call
    assert len(user.obligations()) == 1
    assert user.run(first["n8n_execution_id"])["status"] == "SUCCESS" and user.run(again["n8n_execution_id"])["status"] == "SKIPPED"


def test_the_same_content_arriving_as_a_different_message_is_merged_not_duplicated(n8n, stub, user):
    stub.reset(extraction())
    ingest(n8n, user, "e2e-merge-1", thread_id="t-1")
    second = ingest(n8n, user, "e2e-merge-2", thread_id="t-1", subject="Reminder: internship paperwork")  # a reminder mail about the same thing
    assert len(user.obligations()) == 1
    assert second["disposition"] == "DUPLICATE" and second["outcome"] == "processed"


def test_a_message_with_no_obligation_leaves_no_content_behind(n8n, stub, user, app_db):
    stub.reset(extraction(is_obligation=False, confidence=0.97, title=None, action=None, deadline_text=None, source_context=None, explanation="A newsletter."))
    out = ingest(n8n, user, "e2e-newsletter-1", body="This week's top 10 productivity tips! Unsubscribe anytime.")
    assert out["outcome"] == "processed" and out["disposition"] == "NOT_OBLIGATION" and out["decision"] == "IGNORE"
    assert user.obligations() == []
    row = app_db.rows("select subject, excerpt, sender_email from sources where external_id = 'e2e-newsletter-1'")[0]
    assert row == (None, None, None)  # privacy: nothing about a non-obligation is kept


def test_a_vague_deadline_goes_to_review_instead_of_being_created_silently(n8n, stub, user):
    stub.reset(extraction(confidence=0.9, deadline_text="sometime soon", source_context="Please send the report", ambiguity="'sometime soon' is not a concrete deadline"))
    out = ingest(n8n, user, "e2e-vague-1", body="Hi, please send the report sometime soon. Thanks!")
    assert out["decision"] == "REVIEW" and out["obligation_status"] == "NEEDS_REVIEW"
    assert user.obligations()[0]["status"] == "NEEDS_REVIEW"


def test_the_same_words_resolve_to_different_instants_in_different_timezones(n8n, stub, mail):
    from conftest import User

    ny, kolkata = User("America/New_York"), User("Asia/Kolkata")
    for u in (ny, kolkata):
        stub.reset(extraction())
        ingest(n8n, u, f"e2e-tz-{u.timezone}", received_at=datetime(2026, 10, 1, 14, 0, tzinfo=UTC))
    due = {u.timezone: datetime.fromisoformat(u.obligations()[0]["due_at"]) for u in (ny, kolkata)}
    assert due["America/New_York"] == datetime(2026, 10, 2, 21, 0, tzinfo=UTC)  # Fri 17:00 EDT
    assert due["Asia/Kolkata"] == datetime(2026, 10, 2, 11, 30, tzinfo=UTC)  # Fri 17:00 IST


def test_the_workflow_rejects_callers_without_the_shared_secret(n8n):
    assert n8n.call(INGEST, {"user_email": "x@example.com"}, secret="").status_code == 403
    assert n8n.call(INGEST, {"user_email": "x@example.com"}, secret="not-the-secret").status_code == 403
