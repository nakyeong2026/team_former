"""부록의 연습용 시나리오 3개를 서비스 계층으로 그대로 따라 하며 결과 안내의 첫 단어를 확인합니다.

AI는 부르지 않고, 마지막 배정은 앱 자체 배정(rules.find_assignment)으로 확인합니다.
"""

from pathlib import Path

import pytest

import rules
import service
from db import Database

SAMPLES = Path(__file__).resolve().parents[1] / "samples"


@pytest.fixture
def db(tmp_path):
    database = Database(sqlite_path=tmp_path / "test.db")
    service.ensure_admin(database, "admin-pass")
    return database


class Runner:
    def __init__(self, db, csv_name, teams):
        self.db = db
        result = service.start(db, (SAMPLES / csv_name).read_bytes(), teams)
        assert result.kind == "승인", result

    def sid(self, n):
        state = service.load_state(self.db)
        return next(i for i, s in state.students.items() if s.name.startswith(f"학생{n}("))

    def apply(self, n, team):
        return service.apply(self.db, self.sid(n), team)

    def app_id(self, n):
        state = service.load_state(self.db)
        return service.load_state(self.db).pending_of(self.sid(n))["id"] if state.pending_of(self.sid(n)) else \
            [a for a in state.apps if a["student_id"] == self.sid(n)][-1]["id"]

    def vote(self, voter, applicant, yes=True):
        return service.vote(self.db, self.sid(voter), self.app_id(applicant), yes)

    def close(self):
        return service.close(self.db)

    def assign_and_confirm(self):
        state = service.load_state(self.db)
        plan = rules.find_assignment(state.teams, state.unassigned)
        assert plan is not None, rules.diagnose(state.teams, state.unassigned)
        assert rules.validate_plan(state.teams, state.unassigned, plan) == []
        service.save_proposal(self.db, plan, "test")
        result = service.confirm(self.db)
        assert result.kind == "승인", result
        final = service.load_state(self.db)
        assert final.unassigned == []
        for team in final.teams:
            assert rules.team_problems(team) == [], team
        return final


def kinds(*results):
    return [r.kind for r in results]


def test_easy(db):
    r = Runner(db, "easy.csv", 3)
    assert kinds(r.apply(1, 1), r.apply(2, 2), r.apply(3, 3)) == ["승인"] * 3
    assert r.apply(4, 1).kind == "대기"
    assert r.vote(1, 4).kind == "승인"
    assert r.apply(5, 2).kind == "대기"
    assert r.vote(2, 5).kind == "승인"
    assert r.apply(7, 1).kind == "대기"
    assert r.vote(1, 7).kind == "대기"
    assert r.vote(4, 7, yes=False).kind == "거절"
    assert r.apply(6, 3).kind == "대기"
    assert r.vote(3, 6).kind == "승인"
    assert r.close().kind == "승인"
    r.assign_and_confirm()


def test_medium(db):
    r = Runner(db, "medium.csv", 4)
    assert r.apply(1, 1).kind == "승인"
    assert r.apply(5, 1).kind == "대기"
    assert r.vote(1, 5).kind == "승인"
    blocked = r.apply(2, 1)
    assert blocked.kind == "막힘" and "주 역할" in blocked.text
    assert r.apply(6, 2).kind == "승인"
    assert r.apply(9, 2).kind == "대기"
    assert r.vote(6, 9).kind == "승인"
    assert r.apply(10, 2).kind == "대기"
    assert r.vote(6, 10).kind == "대기"
    assert r.vote(9, 10, yes=False).kind == "거절"
    assert kinds(r.apply(3, 3), r.apply(11, 4)) == ["승인", "승인"]
    assert r.apply(12, 4).kind == "대기"
    assert r.apply(12, 3).kind == "막힘"
    closed = r.close()
    assert closed.kind == "승인" and "학생3(" in closed.text and "학생11(" in closed.text
    state = service.load_state(db)
    assert {s.name.split("(")[0] for s in state.unassigned} == {
        "학생2", "학생3", "학생4", "학생7", "학생8", "학생10", "학생11", "학생12"}
    assert [a["status"] for a in state.apps if a["student_id"] == r.sid(12)] == ["거절"]
    assert r.apply(2, 3).kind == "막힘"
    r.assign_and_confirm()


def test_hard(db):
    r = Runner(db, "hard.csv", 4)
    assert r.apply(1, 1).kind == "승인"
    assert r.apply(4, 1).kind == "대기"
    assert r.vote(1, 4).kind == "승인"
    assert r.apply(7, 1).kind == "대기"
    assert kinds(r.vote(1, 7), r.vote(4, 7)) == ["대기", "승인"]
    assert r.apply(2, 2).kind == "승인"
    assert r.apply(8, 2).kind == "대기"
    assert r.vote(2, 8).kind == "승인"
    assert kinds(r.apply(5, 2), r.apply(6, 2)) == ["대기", "대기"]
    assert kinds(r.vote(2, 5), r.vote(8, 5)) == ["대기", "승인"]
    last = [r.vote(2, 6), r.vote(8, 6), r.vote(5, 6)]
    assert kinds(*last) == ["대기", "대기", "거절"]
    assert "승인 직전" in last[-1].text
    assert r.apply(11, 3).kind == "승인"
    assert r.apply(15, 3).kind == "대기"
    assert r.vote(11, 15).kind == "승인"
    blocked = r.apply(14, 3)
    assert blocked.kind == "막힘" and "필수 포함 역할" in blocked.text
    assert r.apply(10, 4).kind == "승인"
    assert r.apply(13, 1).kind == "대기"
    assert r.vote(1, 13).kind == "대기"
    closed = r.close()
    assert "학생10(" in closed.text
    state = service.load_state(db)
    assert len(state.unassigned) == 8
    final = r.assign_and_confirm()
    # 팀3(테스터·발표자)에는 보조 역할까지 따져야 웹 프로그래머가 채워집니다.
    assert all(rules.filled_required(t.members) == 2 for t in final.teams)


def test_rules_after_close(db):
    r = Runner(db, "easy.csv", 3)
    assert r.apply(1, 1).kind == "승인"
    r.close()
    assert r.apply(2, 2).kind == "막힘"
    assert r.close().kind == "막힘"


def test_login_and_admin(db):
    service.start(db, (SAMPLES / "easy.csv").read_bytes(), 3)
    assert service.login(db, "2026101", "1234")["name"].startswith("학생1(")
    assert service.login(db, "2026101", "wrong") is None
    assert service.login(db, "2026101", "") is None
    admin = service.login(db, "admin", "admin-pass")
    assert admin and admin["is_admin"]
    # 명단을 다시 올려도 관리자 계정은 남습니다.
    service.start(db, (SAMPLES / "hard.csv").read_bytes(), 4)
    assert service.login(db, "admin", "admin-pass")
    service.ensure_admin(db, "new-pass")
    assert service.login(db, "admin", "admin-pass") is None
    assert service.login(db, "admin", "new-pass")


def test_passwords_are_hashed(db):
    service.start(db, (SAMPLES / "easy.csv").read_bytes(), 3)
    rows = db.read(lambda tx: tx.rows("SELECT password_hash FROM tf_students"))
    assert rows and all(r["password_hash"].startswith("pbkdf2_sha256$") for r in rows)
    assert len({r["password_hash"] for r in rows}) == len(rows)  # 학생마다 솔트가 다름


def test_bad_csv(db):
    assert service.start(db, b"a,b\n1,2\n", 3).kind == "막힘"
    bad_role = "student_no,name,main_role,sub_roles\n1,x,요리사,\n".encode()
    assert "주 역할" in service.start(db, bad_role, 3).text
    three_cols = "student_no,name,main_role,sub_roles\n1,x,기획자\n".encode()
    assert "열이 3개" in service.start(db, three_cols, 3).text


def test_unique_constraints(db):
    """마지막 자리에 두 명이 동시에 들어가는 경우 DB 유일성 제약이 두 번째를 막는지."""
    from db import Conflict

    service.start(db, (SAMPLES / "easy.csv").read_bytes(), 3)
    state = service.load_state(db)
    ids = list(state.students)
    db.write(lambda tx: tx.run("INSERT INTO tf_members (team_id, slot_no, student_id) VALUES (1, 1, %s)", (ids[0],)))
    with pytest.raises(Conflict):
        db.write(lambda tx: tx.run("INSERT INTO tf_members (team_id, slot_no, student_id) VALUES (1, 1, %s)",
                                   (ids[1],)))
    with pytest.raises(Conflict):
        db.write(lambda tx: tx.run("INSERT INTO tf_members (team_id, slot_no, student_id) VALUES (2, 1, %s)",
                                   (ids[0],)))
