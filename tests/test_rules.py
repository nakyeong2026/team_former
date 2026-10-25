import rules
from rules import Student, Team


def s(no, main, *subs):
    return Student(no, f"학생{no}", main, subs)


PL, WP, FD, TT, RW, PT = rules.ROLES[0], rules.ROLES[1], rules.ROLES[2], rules.ROLES[3], rules.ROLES[4], rules.ROLES[5]


def test_filled_required_counts_one_student_once():
    assert rules.filled_required([s("1", PL, WP)]) == 1
    assert rules.filled_required([s("1", PL, WP), s("2", TT, WP)]) == 2
    assert rules.filled_required([s("1", TT), s("2", PT)]) == 0


def test_join_problem_required_slots():
    # 테스터와 발표자만 있는 팀(2명)에 보고서 작성자가 들어가면 남는 1자리로 필수 역할 2개를 못 채움
    members = [s("1", TT), s("2", PT)]
    assert "필수 포함 역할" in rules.join_problem(members, s("3", RW))
    assert rules.join_problem(members, s("3", PL)) is None
    assert rules.join_problem([s("1", PL, WP)], s("2", RW)) is None  # 남는 2자리 ≥ 모자란 1개


def test_join_problem_order():
    full = [s("1", PL), s("2", WP), s("3", FD), s("4", TT)]
    assert rules.join_problem(full, s("5", RW)) == "팀이 꽉 참"
    assert rules.join_problem([s("1", PL)], s("2", PL)) == "같은 주 역할의 팀원이 있음"


def test_validate_plan_reports_each_check():
    teams = [Team(1, [s("1", PL), s("2", WP)]), Team(2, [])]
    unassigned = [s("3", PL), s("4", WP, PL)]
    assert rules.validate_plan(teams, unassigned, [("3", 2), ("4", 2)]) == []
    assert any("배정되지 않은" in e for e in rules.validate_plan(teams, unassigned, [("3", 2)]))
    assert any("두 번" in e for e in rules.validate_plan(teams, unassigned, [("3", 2), ("3", 1), ("4", 2)]))
    errors = rules.validate_plan(teams, unassigned, [("3", 1), ("4", 2)])
    assert any("주 역할이 기획자" in e for e in errors) and any("팀2의 인원이 1명" in e for e in errors)


def test_find_assignment_needs_sub_role():
    teams = [Team(1, [s("1", TT), s("2", PT)]), Team(2, [s("3", WP), s("4", FD)])]
    unassigned = [s("5", PL), s("6", FD, WP), s("7", TT, PL)]
    plan = rules.find_assignment(teams, unassigned)
    assert plan is not None
    assert rules.validate_plan(teams, unassigned, plan) == []


def test_infeasible_gives_reason():
    teams = [Team(1, [s("1", TT), s("2", PT)]), Team(2, [])]
    unassigned = [s("3", RW), s("4", FD), s("5", PL)]
    assert rules.find_assignment(teams, unassigned) is None
    reasons = rules.diagnose(teams, unassigned)
    assert any("웹 프로그래머" in r for r in reasons)
