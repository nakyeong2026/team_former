"""앱의 동작(명단 올리기, 로그인, 지원, 투표, 마감, 배정안 저장·확정).

화면(Streamlit)과 분리해서 pytest로 시험합니다. 결과는 Result로 돌려주며,
화면은 str(result)를 그대로 보여 줍니다("승인", "대기", "거절", "막힘" 가운데 하나로 시작).
"""

import csv
import io
import json
from dataclasses import dataclass, field

import auth
import rules
from db import Conflict, Database, Tx
from rules import Student, Team

STUDENT_PASSWORD = "1234"
ADMIN_NO = "admin"
CSV_HEADER = ["student_no", "name", "main_role", "sub_roles"]


@dataclass
class Result:
    kind: str  # 승인, 대기, 거절, 막힘
    text: str

    def __str__(self) -> str:
        return f"{self.kind}: {self.text}"


@dataclass
class State:
    """화면을 한 번 그릴 때 필요한 DB 내용 전체."""
    students: dict[int, Student] = field(default_factory=dict)  # DB id → 학생(관리자 제외)
    teams: list[Team] = field(default_factory=list)
    team_of: dict[int, int] = field(default_factory=dict)  # 학생 id → 팀 번호
    apps: list[dict] = field(default_factory=list)
    votes: dict[int, dict[int, str]] = field(default_factory=dict)  # 지원 id → {투표한 학생 id: 찬성/거부}
    closed: bool = False
    proposal: dict | None = None

    def id_of(self, student_no: str) -> int | None:
        return next((i for i, s in self.students.items() if s.student_no == student_no), None)

    def member_ids(self, team_no: int) -> list[int]:
        return [i for i, t in self.team_of.items() if t == team_no]

    @property
    def unassigned(self) -> list[Student]:
        return [s for i, s in self.students.items() if i not in self.team_of]

    def pending_of(self, student_id: int) -> dict | None:
        return next((a for a in self.apps if a["student_id"] == student_id and a["status"] == "대기"), None)

    def pending_for_team(self, team_no: int) -> list[dict]:
        return [a for a in self.apps if a["team_id"] == team_no and a["status"] == "대기"]


# ---------- 읽기 ----------

def _students(tx: Tx, where: str = "", params: tuple = ()) -> dict[int, Student]:
    rows = tx.rows(
        "SELECT s.id, s.student_no, s.name, s.main_role, r.role FROM tf_students s "
        f"LEFT JOIN tf_student_sub_roles r ON r.student_id = s.id WHERE s.is_admin = FALSE {where} ORDER BY s.id",
        params,
    )
    subs: dict[int, list[str]] = {}
    base: dict[int, dict] = {}
    for r in rows:
        base.setdefault(r["id"], r)
        if r["role"]:
            subs.setdefault(r["id"], []).append(r["role"])
    return {
        i: Student(r["student_no"], r["name"], r["main_role"],
                   tuple(sorted(subs.get(i, []), key=rules.ROLES.index)))
        for i, r in base.items()
    }


def _setting(tx: Tx, key: str) -> str | None:
    row = tx.one("SELECT value FROM tf_settings WHERE key = %s", (key,))
    return row["value"] if row else None


def _set_setting(tx: Tx, key: str, value: str | None) -> None:
    tx.run("DELETE FROM tf_settings WHERE key = %s", (key,))
    if value is not None:
        tx.run("INSERT INTO tf_settings (key, value) VALUES (%s, %s)", (key, value))


def _load(tx: Tx) -> State:
    state = State(students=_students(tx))
    team_rows = tx.rows("SELECT id FROM tf_teams ORDER BY id")
    teams = {r["id"]: Team(r["id"]) for r in team_rows}
    for r in tx.rows("SELECT team_id, student_id FROM tf_members ORDER BY team_id, slot_no"):
        if r["student_id"] in state.students and r["team_id"] in teams:
            teams[r["team_id"]].members.append(state.students[r["student_id"]])
            state.team_of[r["student_id"]] = r["team_id"]
    state.teams = list(teams.values())
    state.apps = tx.rows("SELECT id, team_id, student_id, status, reason FROM tf_applications ORDER BY id")
    for r in tx.rows("SELECT application_id, voter_id, vote FROM tf_votes"):
        state.votes.setdefault(r["application_id"], {})[r["voter_id"]] = r["vote"]
    settings = {r["key"]: r["value"] for r in tx.rows("SELECT key, value FROM tf_settings")}
    state.closed = settings.get("closed") == "1"
    state.proposal = json.loads(settings["proposal"]) if settings.get("proposal") else None
    return state


# 화면 갱신용 읽기를 모든 사용자가 함께 쓰는 시간(초). 이 앱에서 쓰기가 있으면 바로 새로 읽습니다.
STATE_MAX_AGE = 3


def load_state(db: Database) -> State:
    """화면에 그릴 상태. 돌려준 State는 여러 사용자가 함께 쓰므로 고치지 않습니다."""
    return db.cached_read("state", _load, STATE_MAX_AGE)


# ---------- 계정 ----------

def ensure_admin(db: Database, password: str) -> None:
    """관리자 계정을 만들거나, 비밀번호가 바뀌었으면 새 해시로 갱신합니다."""
    def work(tx: Tx) -> None:
        row = tx.one("SELECT id, password_hash FROM tf_students WHERE student_no = %s", (ADMIN_NO,))
        if row is None:
            tx.run("INSERT INTO tf_students (student_no, name, main_role, password_hash, is_admin) "
                   "VALUES (%s, %s, NULL, %s, TRUE)", (ADMIN_NO, "관리자", auth.hash_password(password)))
        elif not auth.verify_password(password, row["password_hash"]):
            tx.run("UPDATE tf_students SET password_hash = %s, is_admin = TRUE WHERE id = %s",
                   (auth.hash_password(password), row["id"]))
    db.write(work)


def login(db: Database, student_no: str, password: str) -> dict | None:
    row = db.read(lambda tx: tx.one(
        "SELECT id, student_no, name, main_role, is_admin, password_hash FROM tf_students WHERE student_no = %s",
        (student_no.strip(),)))
    if row and password and auth.verify_password(password, row.pop("password_hash")):
        row["is_admin"] = bool(row["is_admin"])
        return row
    return None


# ---------- 명단 올리기 ----------

def parse_roster(data: bytes) -> list[Student]:
    """명단 CSV(BOM이 있어도 됨)를 읽습니다. 틀린 곳이 있으면 ValueError."""
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ValueError("CSV가 UTF-8이 아님") from None
    rows = [r for r in csv.reader(io.StringIO(text)) if any(c.strip() for c in r)]
    if not rows or [c.strip() for c in rows[0]] != CSV_HEADER:
        raise ValueError("첫 줄이 student_no,name,main_role,sub_roles 이어야 함")
    students, seen = [], set()
    for line_no, row in enumerate(rows[1:], start=2):
        if len(row) != 4:
            raise ValueError(f"{line_no}번째 줄의 열이 {len(row)}개임(4개여야 함)")
        no, name, main, subs_text = (c.strip() for c in row)
        if not no or not name:
            raise ValueError(f"{line_no}번째 줄에 학번이나 이름이 없음")
        if no == ADMIN_NO or no in seen:
            raise ValueError(f"{line_no}번째 줄의 학번 {no}가 중복되거나 쓸 수 없는 값임")
        if main not in rules.ROLES:
            raise ValueError(f"{line_no}번째 줄의 주 역할 '{main}'은 6개 역할이 아님")
        subs = [s.strip() for s in subs_text.split(";") if s.strip()]
        bad = [s for s in subs if s not in rules.ROLES]
        if bad or len(subs) > 2 or main in subs or len(set(subs)) != len(subs):
            raise ValueError(f"{line_no}번째 줄의 보조 역할 '{subs_text}'이 올바르지 않음")
        seen.add(no)
        students.append(Student(no, name, main, tuple(subs)))
    if not students:
        raise ValueError("명단에 학생이 없음")
    return students


def start(db: Database, data: bytes | None, team_count: int) -> Result:
    """명단과 팀 수로 처음 상태를 만듭니다. 관리자 계정과 AI 호출 기록은 남깁니다."""
    if not data:
        return Result("막힘", "명단 CSV 파일을 먼저 올려야 함")
    try:
        students = parse_roster(data)
    except ValueError as e:
        return Result("막힘", f"명단 CSV를 읽을 수 없음: {e}")
    if not 1 <= team_count <= 20:
        return Result("막힘", "팀 수는 1~20이어야 함")
    hashes = [auth.hash_password(STUDENT_PASSWORD) for _ in students]  # 학생마다 솔트가 다름

    def work(tx: Tx) -> None:
        for table in ("tf_votes", "tf_applications", "tf_members", "tf_teams", "tf_settings"):
            tx.run(f"DELETE FROM {table}")
        tx.run("DELETE FROM tf_student_sub_roles WHERE student_id IN "
               "(SELECT id FROM tf_students WHERE is_admin = FALSE)")
        tx.run("DELETE FROM tf_students WHERE is_admin = FALSE")
        tx.many("INSERT INTO tf_students (student_no, name, main_role, password_hash, is_admin) "
                "VALUES (%s, %s, %s, %s, FALSE)",
                [(s.student_no, s.name, s.main_role, h) for s, h in zip(students, hashes)])
        ids = {r["student_no"]: r["id"] for r in tx.rows("SELECT id, student_no FROM tf_students")}
        tx.many("INSERT INTO tf_student_sub_roles (student_id, role) VALUES (%s, %s)",
                [(ids[s.student_no], role) for s in students for role in s.sub_roles])
        tx.many("INSERT INTO tf_teams (id, name) VALUES (%s, %s)",
                [(n, f"팀{n}") for n in range(1, team_count + 1)])
        _set_setting(tx, "closed", "0")

    db.write(work)
    return Result("승인", f"학생 {len(students)}명과 빈 팀 {team_count}개로 처음 상태를 만듦")


# ---------- 지원과 투표 ----------

def _members(tx: Tx, team_no: int) -> list[tuple[int, Student]]:
    ids = [r["student_id"] for r in tx.rows(
        "SELECT student_id FROM tf_members WHERE team_id = %s ORDER BY slot_no", (team_no,))]
    if not ids:
        return []
    marks = ", ".join(["%s"] * len(ids))
    students = _students(tx, f"AND s.id IN ({marks})", tuple(ids))
    return [(i, students[i]) for i in ids]


def _add_member(tx: Tx, team_no: int, student_id: int) -> None:
    used = {r["slot_no"] for r in tx.rows("SELECT slot_no FROM tf_members WHERE team_id = %s", (team_no,))}
    free = [n for n in range(1, rules.MAX_SIZE + 1) if n not in used]
    if not free:
        raise Conflict("팀이 꽉 참")
    # (team_id, slot_no) 기본 키와 student_id 유일성 제약이 동시에 들어온 두 번째 학생을 막습니다.
    tx.run("INSERT INTO tf_members (team_id, slot_no, student_id) VALUES (%s, %s, %s)",
           (team_no, free[0], student_id))


def _decide(tx: Tx, app_id: int, status: str, reason: str) -> None:
    tx.run("UPDATE tf_applications SET status = %s, reason = %s, decided_at = now() WHERE id = %s",
           (status, reason, app_id))


def apply(db: Database, student_id: int, team_no: int) -> Result:
    def work(tx: Tx) -> Result:
        if _setting(tx, "closed") == "1":
            return Result("막힘", "모집이 마감됨")
        if not tx.one("SELECT id FROM tf_teams WHERE id = %s FOR UPDATE", (team_no,)):
            return Result("막힘", f"팀{team_no}이 없음")
        if tx.one("SELECT team_id FROM tf_members WHERE student_id = %s", (student_id,)):
            return Result("막힘", "이미 팀이 있음")
        if tx.one("SELECT id FROM tf_applications WHERE student_id = %s AND status = '대기'", (student_id,)):
            return Result("막힘", "결과를 기다리는 지원이 있음")
        applicant = _students(tx, "AND s.id = %s", (student_id,)).get(student_id)
        if applicant is None:
            return Result("막힘", "학생 정보를 찾을 수 없음")
        members = _members(tx, team_no)
        problem = rules.join_problem([s for _, s in members], applicant)
        if problem:
            return Result("막힘", problem)
        if not members:
            _add_member(tx, team_no, student_id)
            tx.run("INSERT INTO tf_applications (team_id, student_id, status, reason, decided_at) "
                   "VALUES (%s, %s, '승인', %s, now())", (team_no, student_id, "빈 팀이라 바로 승인"))
            return Result("승인", f"빈 팀이라 바로 팀{team_no}의 팀원이 됨")
        tx.run("INSERT INTO tf_applications (team_id, student_id, status) VALUES (%s, %s, '대기')",
               (team_no, student_id))
        return Result("대기", f"팀{team_no}의 팀원 {len(members)}명의 투표를 기다림(모두 찬성하면 승인)")

    try:
        return db.write(work)
    except Conflict:
        return Result("막힘", "다른 학생이 먼저 그 자리에 들어감")


def vote(db: Database, voter_id: int, app_id: int, approve: bool) -> Result:
    def work(tx: Tx) -> Result:
        app = tx.one("SELECT id, team_id, student_id, status, reason FROM tf_applications WHERE id = %s",
                     (app_id,))
        if app is None:
            return Result("막힘", "없는 지원임")
        team_no = app["team_id"]
        tx.one("SELECT id FROM tf_teams WHERE id = %s FOR UPDATE", (team_no,))
        applicant = _students(tx, "AND s.id = %s", (app["student_id"],))[app["student_id"]]
        if app["status"] != "대기":
            return Result(app["status"], f"{applicant.name}의 지원은 이미 {app['status']}됨 ({app['reason'] or ''})")
        if _setting(tx, "closed") == "1":
            return Result("막힘", "모집이 마감됨")
        members = _members(tx, team_no)
        ids = [i for i, _ in members]
        if voter_id not in ids:
            return Result("막힘", f"팀{team_no}의 팀원만 이 지원에 투표할 수 있음")
        votes = {r["voter_id"]: r["vote"] for r in tx.rows(
            "SELECT voter_id, vote FROM tf_votes WHERE application_id = %s", (app_id,))}
        if voter_id in votes:
            return Result("대기", f"이미 {votes[voter_id]}에 투표함(투표는 바꿀 수 없음)")
        choice = "찬성" if approve else "거부"
        tx.run("INSERT INTO tf_votes (application_id, voter_id, vote) VALUES (%s, %s, %s)",
               (app_id, voter_id, choice))
        votes[voter_id] = choice
        voter = dict(members)[voter_id]
        if not approve:
            _decide(tx, app_id, "거절", f"{voter.name}이(가) 거부함")
            return Result("거절", f"{voter.name}이(가) 거부해서 {applicant.name}의 지원이 거절됨")
        waiting = [s.name for i, s in members if votes.get(i) != "찬성"]
        if waiting:
            return Result("대기", f"아직 투표하지 않은 팀원: {', '.join(waiting)}")
        # 승인 직전 재검사: 앞의 지원이 먼저 승인되어 팀이 바뀌었을 수 있습니다.
        problem = rules.join_problem([s for _, s in members], applicant)
        if problem:
            _decide(tx, app_id, "거절", f"승인 직전 검사: {problem}")
            return Result("거절", f"승인 직전 검사에서 걸림 — {problem}")
        _add_member(tx, team_no, app["student_id"])
        _decide(tx, app_id, "승인", "팀원 모두 찬성")
        return Result("승인", f"팀원 모두 찬성해서 {applicant.name}이(가) 팀{team_no}의 팀원이 됨")

    try:
        return db.write(work)
    except Conflict:
        return Result("막힘", "같은 순간에 다른 요청이 먼저 처리됨. 다시 시도해 주세요")


# ---------- 마감과 배정 ----------

def close(db: Database) -> Result:
    def work(tx: Tx) -> Result:
        if _setting(tx, "closed") == "1":
            return Result("막힘", "이미 모집이 마감됨")
        if not tx.one("SELECT id FROM tf_teams"):
            return Result("막힘", "명단을 먼저 올려야 함")
        _set_setting(tx, "closed", "1")
        _set_setting(tx, "proposal", None)
        pending = tx.rows("SELECT id FROM tf_applications WHERE status = '대기'")
        for app in pending:
            _decide(tx, app["id"], "거절", "모집 마감")
        singles = tx.rows("SELECT team_id FROM tf_members GROUP BY team_id HAVING COUNT(*) = 1")
        removed = []
        for row in singles:
            for student_id, student in _members(tx, row["team_id"]):
                removed.append(student.name)
                tx.run("DELETE FROM tf_members WHERE student_id = %s", (student_id,))
        left = "없음" if not removed else ", ".join(removed)
        return Result("승인", f"모집을 마감함 (대기 중이던 지원 {len(pending)}건 거절, 혼자인 팀에서 빠진 학생: {left})")

    return db.write(work)


def save_proposal(db: Database, plan: list[tuple[str, int]], source: str) -> None:
    value = json.dumps({"plan": plan, "source": source}, ensure_ascii=False)
    db.write(lambda tx: _set_setting(tx, "proposal", value))


def confirm(db: Database) -> Result:
    def work(tx: Tx) -> Result:
        state = _load(tx)
        if not state.closed:
            return Result("막힘", "모집을 먼저 마감해야 함")
        if not state.proposal:
            return Result("막힘", "확정할 배정안이 없음(AI 배정을 먼저 누름)")
        plan = [(no, int(team)) for no, team in state.proposal["plan"]]
        errors = rules.validate_plan(state.teams, state.unassigned, plan)
        if errors:
            return Result("막힘", "배정안이 지금 팀 구성과 맞지 않음: " + "; ".join(errors))
        for no, team_no in plan:
            _add_member(tx, team_no, state.id_of(no))
        _set_setting(tx, "proposal", None)
        return Result("승인", f"배정안을 확정함 ({len(plan)}명 배정)")

    try:
        return db.write(work)
    except Conflict:
        return Result("막힘", "배정안을 저장하는 중 팀 자리가 겹침")


def log_ai(db: Database, attempts: list) -> None:
    db.write(lambda tx: tx.many(
        "INSERT INTO tf_ai_logs (model, attempt_no, outcome, detail, elapsed_sec) VALUES (%s, %s, %s, %s, %s)",
        [(a.model, a.order, a.outcome, a.detail[:1000], round(a.elapsed, 1)) for a in attempts]))


def recent_ai_logs(db: Database, limit: int = 15) -> list[dict]:
    return db.read(lambda tx: tx.rows(
        "SELECT model, attempt_no, outcome, detail, elapsed_sec, created_at FROM tf_ai_logs "
        "ORDER BY id DESC LIMIT %s", (limit,)))
