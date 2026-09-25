"""The Deadline Monitor workflow driving the obligation lifecycle: 24h / 6h / overdue / escalated, without spam.

Time is the demo clock (a virtual clock the API and every rule share), so a two-day ladder runs in seconds. The monitor is
triggered through its real n8n webhook; delivery goes through the real Notification Dispatcher into Mailpit.
"""

from __future__ import annotations

import time
from datetime import UTC, datetime, timedelta

from conftest import T0, extraction, ingest_payload, wait_until

MONITOR = "commitmentos-monitor"
DUE = datetime(2026, 10, 2, 21, 0, tzinfo=UTC)  # "tomorrow 5pm" from T0 (Thu 10:00 in New York) is Fri 17:00 EDT


def at(clock, when: datetime):
    clock.set_to(when)


def tick(n8n) -> dict:
    r = n8n.call(MONITOR, {}, timeout=120)
    assert r.status_code == 200, r.text
    return r.json()


def start(n8n, stub, user, clock, mail, *, external_id="e2e-ladder-1"):
    """Ingest the standard commitment at T0 and wait for its 'new commitment' email."""
    at(clock, T0)
    stub.reset(extraction())
    r = n8n.call("commitmentos-ingest", ingest_payload(user, external_id, received_at=T0), timeout=120)
    assert r.status_code == 200 and r.json()["outcome"] == "processed", r.text
    ob = user.obligations()[0]
    assert datetime.fromisoformat(ob["due_at"]) == DUE
    wait_until(lambda: len(mail.messages(user.email)) == 1, what="the detection email")
    return ob


def stays(check, seconds: float = 3.0) -> bool:
    """True if `check()` holds for the whole window: proves that nothing more arrives (no spam), not merely that nothing has yet."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not check():
            return False
        time.sleep(0.5)
    return True


def wait_for_mail(mail, user, count, prefix):
    msgs = wait_until(lambda: (m := mail.messages(user.email)) and len(m) == count and m, what=f"email #{count} ({prefix})")
    assert msgs[-1]["Subject"].startswith(prefix), [m["Subject"] for m in msgs]
    return msgs


def test_the_full_reminder_ladder_fires_each_rung_exactly_once(n8n, stub, user, clock, mail, app_db):
    ob = start(n8n, stub, user, clock, mail)

    at(clock, datetime(2026, 10, 1, 20, 30, tzinfo=UTC))  # 24.5h before the deadline: nothing is due yet
    tick(n8n)
    assert stays(lambda: len(mail.messages(user.email)) == 1)

    at(clock, datetime(2026, 10, 1, 21, 5, tzinfo=UTC))  # T-24h has passed
    tick(n8n)
    wait_for_mail(mail, user, 2, "Reminder - in")
    tick(n8n)
    tick(n8n)  # ticking again changes nothing: no spam
    assert stays(lambda: len(mail.messages(user.email)) == 2)

    at(clock, datetime(2026, 10, 2, 15, 5, tzinfo=UTC))  # T-6h
    tick(n8n)
    wait_for_mail(mail, user, 3, "Due soon - in")

    at(clock, DUE + timedelta(minutes=5))  # the deadline has passed
    tick(n8n)
    wait_for_mail(mail, user, 4, "Overdue:")
    assert user.get(f"/api/obligations/{ob['id']}").json()["obligation"]["status"] == "OVERDUE"

    at(clock, DUE + timedelta(hours=25))  # a day later and still unresolved
    tick(n8n)
    wait_for_mail(mail, user, 5, "Still unresolved:")
    assert user.get(f"/api/obligations/{ob['id']}").json()["obligation"]["status"] == "ESCALATED"

    # the person finally does it: everything stops
    assert user.post(f"/api/obligations/{ob['id']}/complete").status_code == 200
    at(clock, DUE + timedelta(days=4))
    tick(n8n)
    assert stays(lambda: len(mail.messages(user.email)) == 5)  # not one more email after completion
    pending = app_db.one("select count(*) from notifications where obligation_id = %s and status in ('PENDING', 'SENDING')", ob["id"])
    assert pending == 0

    events = [e["event_type"] for e in user.get(f"/api/obligations/{ob['id']}/timeline").json()]
    assert events.count("NOTIFICATION_SENT") == 5
    for expected in ("OVERDUE_MARKED", "ESCALATED", "COMPLETED"):
        assert expected in events, events  # the whole lifecycle is in the audit trail, in order


def test_a_long_gap_sends_one_notice_for_where_things_stand_not_a_burst_of_missed_reminders(n8n, stub, user, clock, mail):
    start(n8n, stub, user, clock, mail)
    at(clock, DUE + timedelta(hours=2))  # the monitor was "down" for a day and a half: 24h, 6h and overdue are all in the past
    tick(n8n)
    wait_for_mail(mail, user, 2, "Overdue:")
    tick(n8n)
    assert stays(lambda: len(mail.messages(user.email)) == 2)  # one notice about where things stand, not three catch-up reminders


def test_a_snoozed_commitment_stays_quiet_until_the_snooze_ends(n8n, stub, user, clock, mail):
    ob = start(n8n, stub, user, clock, mail)
    until = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)
    assert user.post(f"/api/obligations/{ob['id']}/snooze", json={"until": until.isoformat()}).status_code == 200

    at(clock, datetime(2026, 10, 1, 21, 30, tzinfo=UTC))  # T-24h passes while snoozed
    tick(n8n)
    assert len(mail.messages(user.email)) == 1

    at(clock, until + timedelta(minutes=5))  # snooze over
    tick(n8n)
    msgs = wait_until(lambda: len(mail.messages(user.email)) == 2 and mail.messages(user.email), what="the reminder after the snooze")
    assert msgs[-1]["Subject"].startswith(("Reminder - in", "Due soon - in"))


def test_a_dismissed_commitment_gets_no_reminders(n8n, stub, user, clock, mail):
    ob = start(n8n, stub, user, clock, mail)
    assert user.post(f"/api/obligations/{ob['id']}/dismiss").status_code == 200
    at(clock, DUE + timedelta(hours=1))
    tick(n8n)
    assert stays(lambda: len(mail.messages(user.email)) == 1)


def test_idle_ticks_are_not_recorded_but_ticks_with_work_are(n8n, stub, user, clock, mail):
    start(n8n, stub, user, clock, mail)
    idle = tick(n8n)  # nothing is due at T0
    assert idle.get("idle") is True and user.run(idle["n8n_execution_id"]) is None  # an empty tick is not recorded
    at(clock, DUE + timedelta(minutes=1))
    out = tick(n8n)
    run = user.run(out["n8n_execution_id"])
    assert run["status"] == "SUCCESS" and run["result"]["marked_overdue"] >= 1 and run["trigger"] == "WEBHOOK"
