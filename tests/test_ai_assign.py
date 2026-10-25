"""AI 배정 흐름을 실제 호출 없이 시험합니다(가짜 call 함수)."""

import json
import time

import pytest

import ai_assign
import rules
from rules import Student, Team

PL, WP, FD, TT, RW, PT = rules.ROLES

TEAMS = [Team(1, [Student("1", "학생1", PL), Student("2", "학생2", WP)]),
         Team(2, [Student("3", "학생3", TT), Student("4", "학생4", PT)])]
UNASSIGNED = [Student("5", "학생5", FD, (WP,)), Student("6", "학생6", PL)]
GOOD = json.dumps({"assignments": [{"student_no": "5", "team": 2}, {"student_no": "6", "team": 2}]})
BAD_RULES = json.dumps({"assignments": [{"student_no": "5", "team": 1}, {"student_no": "6", "team": 2}]})


def fake_call(responses):
    calls = []

    def call(model, messages, timeout, on_tick):
        calls.append(model)
        item = responses[len(calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    return call, calls


def test_first_model_success():
    call, calls = fake_call([f"```json\n{GOOD}\n```"])
    out = ai_assign.run_assignment(TEAMS, UNASSIGNED, call=call)
    assert out.plan == [("5", 2), ("6", 2)] and out.source.startswith("1차")
    assert len(calls) == 1


def test_falls_through_to_third_model():
    call, calls = fake_call([ai_assign.AIError("timeout", "120초 안에 응답하지 않아 끊음"), BAD_RULES, GOOD])
    statuses = []
    out = ai_assign.run_assignment(TEAMS, UNASSIGNED, on_status=statuses.append, call=call)
    assert out.source.startswith("3차")
    assert [a.outcome for a in out.attempts] == ["실패", "실패", "성공"]
    assert "규칙 검사 불통과" in out.attempts[1].detail
    assert any(s.startswith("2차 모델") for s in statuses)


def test_all_fail_then_app_assigns():
    call, _ = fake_call([ai_assign.AIError("empty", "빈 응답"), "생각 중...", "{\"assignments\": 3}"])
    out = ai_assign.run_assignment(TEAMS, UNASSIGNED, call=call)
    assert out.source.startswith("앱 자체 배정")
    assert rules.validate_plan(TEAMS, UNASSIGNED, out.plan) == []


def test_infeasible_does_not_call_ai():
    call, calls = fake_call([GOOD])
    unassigned = [Student("5", "학생5", FD), Student("6", "학생6", RW)]
    out = ai_assign.run_assignment(TEAMS, unassigned, call=call)
    assert out.plan is None and calls == []
    assert any("웹 프로그래머" in r for r in out.infeasible)


def test_deadline_cuts_slow_model(monkeypatch):
    monkeypatch.setattr(ai_assign, "chat_once", lambda model, messages, timeout: time.sleep(10) or "늦은 답")
    start = time.monotonic()
    with pytest.raises(ai_assign.AIError) as e:
        ai_assign.call_with_deadline("m", [], timeout=2)
    assert e.value.kind == "timeout"
    assert time.monotonic() - start < 4


def test_parse_plan_accepts_team_name():
    assert ai_assign.parse_plan('답: {"assignments": [{"student_no": 7, "team": "팀3"}]}') == [("7", 3)]
    with pytest.raises(ValueError):
        ai_assign.parse_plan("")
