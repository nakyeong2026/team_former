"""팀 편성 규칙(공고 2.3절, 2.4절, 2.5절 4항)과 앱 자체 배정.

화면과 DB에서 분리된 순수 함수만 둡니다. 학생은 학번(student_no)으로 구별합니다.
"""

from dataclasses import dataclass, field

ROLES = ("기획자", "웹 프로그래머", "프론트엔드 디자이너", "테스터", "보고서 작성자", "발표자")
ROLE_CODES = {"기획자": "PL", "웹 프로그래머": "WP", "프론트엔드 디자이너": "FD",
              "테스터": "TT", "보고서 작성자": "RW", "발표자": "PT"}
REQUIRED = ("기획자", "웹 프로그래머")
MIN_SIZE = 2
MAX_SIZE = 4


@dataclass(frozen=True)
class Student:
    student_no: str
    name: str
    main_role: str
    sub_roles: tuple[str, ...] = ()

    def can(self, role: str) -> bool:
        """주 역할이나 보조 역할로 그 역할을 맡을 수 있는지."""
        return role == self.main_role or role in self.sub_roles

    @property
    def roles_text(self) -> str:
        subs = ", ".join(self.sub_roles) if self.sub_roles else "없음"
        return f"주 역할 {self.main_role} · 보조 역할 {subs}"


@dataclass
class Team:
    no: int
    members: list[Student] = field(default_factory=list)

    @property
    def name(self) -> str:
        return f"팀{self.no}"


# ---------- 필수 포함 역할 ----------

def filled_required(members: list[Student]) -> int:
    """기획자와 웹 프로그래머를 서로 다른 학생에게 맡길 때 채울 수 있는 필수 역할 수(0~2).

    한 학생이 둘 다 할 수 있어도 하나로만 셉니다(최대 매칭).
    """
    planners = [s for s in members if s.can("기획자")]
    programmers = [s for s in members if s.can("웹 프로그래머")]
    if any(p is not q for p in planners for q in programmers):
        return 2
    return 1 if planners or programmers else 0


def missing_required(members: list[Student]) -> int:
    return len(REQUIRED) - filled_required(members)


def join_problem(members: list[Student], applicant: Student) -> str | None:
    """applicant가 이 팀에 들어갈 때 어기는 규칙. 지원할 때와 승인 직전에 같은 검사를 씁니다."""
    if len(members) >= MAX_SIZE:
        return "팀이 꽉 참"
    if any(m.main_role == applicant.main_role for m in members):
        return "같은 주 역할의 팀원이 있음"
    after = members + [applicant]
    if MAX_SIZE - len(after) < missing_required(after):
        return "필수 포함 역할(기획자, 웹 프로그래머)을 맡을 학생의 자리가 모자람"
    return None


# ---------- 확정 전 검사(2.5절 4항) ----------

def team_problems(team: Team) -> list[str]:
    """완성된 팀 하나가 어기는 규칙들."""
    problems = []
    size = len(team.members)
    if not MIN_SIZE <= size <= MAX_SIZE:
        problems.append(f"{team.name}의 인원이 {size}명임(2~4명이어야 함)")
    mains = [m.main_role for m in team.members]
    for role in sorted(set(mains), key=mains.index):
        if mains.count(role) > 1:
            problems.append(f"{team.name}에 주 역할이 {role}인 학생이 {mains.count(role)}명임")
    if size and filled_required(team.members) < len(REQUIRED):
        lacking = [r for r in REQUIRED if not any(m.can(r) for m in team.members)]
        if lacking:
            problems.append(f"{team.name}에 {'와 '.join(lacking)}를 맡을 학생이 없음")
        else:
            problems.append(f"{team.name}에서 기획자와 웹 프로그래머를 서로 다른 학생이 맡을 수 없음")
    return problems


def apply_plan(teams: list[Team], plan: list[tuple[str, int]], students: dict[str, Student]) -> list[Team]:
    """배정안을 적용한 새 팀 목록(원래 팀 목록은 바꾸지 않음). 알 수 없는 학번·팀은 건너뜁니다."""
    result = {t.no: Team(t.no, list(t.members)) for t in teams}
    for no, team_no in plan:
        if no in students and team_no in result:
            result[team_no].members.append(students[no])
    return [result[t.no] for t in teams]


def validate_plan(teams: list[Team], unassigned: list[Student], plan: list[tuple[str, int]]) -> list[str]:
    """배정안이 다섯 가지 검사를 통과하면 빈 목록, 아니면 어긴 내용을 돌려줍니다.

    1) 팀이 없는 학생이 빠짐없이 배정됨 2) 한 학생은 한 팀에만 3) 모든 팀 2~4명
    4) 팀 안에서 주 역할 중복 없음 5) 필수 포함 역할 충족
    """
    errors = []
    by_no = {s.student_no: s for s in unassigned}
    team_nos = {t.no for t in teams}
    seen: dict[str, int] = {}
    for no, team_no in plan:
        if no not in by_no:
            errors.append(f"팀이 없는 학생이 아닌 학번 {no}를 배정함")
        elif no in seen:
            errors.append(f"{by_no[no].name}을(를) 두 번 이상 배정함")
        if team_no not in team_nos:
            errors.append(f"없는 팀 번호 {team_no}에 배정함")
        seen.setdefault(no, team_no)
    missing = [s.name for s in unassigned if s.student_no not in seen]
    if missing:
        errors.append("배정되지 않은 학생: " + ", ".join(missing))
    unique_plan = [(no, t) for no, t in seen.items() if no in by_no]
    for team in apply_plan(teams, unique_plan, by_no):
        errors.extend(team_problems(team))
    return errors


# ---------- 앱 자체 배정(모든 경우를 따져 끝까지 찾음) ----------

def find_assignment(teams: list[Team], unassigned: list[Student]) -> list[tuple[str, int]] | None:
    """다섯 가지 검사를 모두 통과하는 배정을 백트래킹으로 찾습니다. 없으면 None."""
    members = {t.no: list(t.members) for t in teams}

    def options(student: Student) -> list[int]:
        return [t.no for t in teams
                if len(members[t.no]) < MAX_SIZE
                and all(m.main_role != student.main_role for m in members[t.no])]

    # 갈 수 있는 팀이 적은 학생부터 정하면 빨리 막다른 길을 알아챕니다.
    order = sorted(unassigned, key=lambda s: len(options(s)))
    plan: list[tuple[str, int]] = []

    def feasible_rest(index: int) -> bool:
        # 남은 학생 수로 채울 수 없는 팀(2명 미만)이나 필수 역할이 있으면 일찍 포기합니다.
        left = len(order) - index
        need = sum(max(MIN_SIZE - len(m), 0) for m in members.values())
        if need > left:
            return False
        for t in teams:
            free = MAX_SIZE - len(members[t.no])
            if missing_required(members[t.no]) > min(free, left):
                return False
        return True

    def search(index: int) -> bool:
        if index == len(order):
            return all(not team_problems(Team(no, m)) for no, m in members.items())
        if not feasible_rest(index):
            return False
        student = order[index]
        for team_no in options(student):
            members[team_no].append(student)
            plan.append((student.student_no, team_no))
            if search(index + 1):
                return True
            members[team_no].pop()
            plan.pop()
        return False

    return list(plan) if search(0) else None


def diagnose(teams: list[Team], unassigned: list[Student]) -> list[str]:
    """규칙을 지키는 배정이 없을 때 그 이유를 찾습니다."""
    reasons = []
    free = sum(MAX_SIZE - len(t.members) for t in teams)
    if len(unassigned) > free:
        reasons.append(f"팀의 남은 자리 {free}개보다 팀이 없는 학생 {len(unassigned)}명이 많음")
    need = sum(max(MIN_SIZE - len(t.members), 0) for t in teams)
    if need > len(unassigned):
        reasons.append(f"모든 팀을 2명 이상으로 만들려면 {need}명이 더 필요하지만 팀이 없는 학생은 {len(unassigned)}명임")
    for team in teams:
        room = MAX_SIZE - len(team.members)
        if room == 0:
            # 꽉 찬 팀은 기존 팀원을 옮기지 않으므로 지금 모습 그대로 규칙을 지켜야 합니다.
            reasons.extend(team_problems(team))
            continue
        mains = {m.main_role for m in team.members}
        fits = [s for s in unassigned if s.main_role not in mains]
        lacking = [r for r in REQUIRED
                   if not any(m.can(r) for m in team.members) and not any(s.can(r) for s in fits)]
        for role in lacking:
            reasons.append(f"{team.name}에 {role}를 맡을 학생이 없음")
        if not lacking and missing_required(team.members) > room:
            reasons.append(f"{team.name}은 자리가 {room}개 남았지만 채워야 할 필수 포함 역할이 "
                           f"{missing_required(team.members)}개임")
    if not reasons:
        for role in REQUIRED:
            lacking = [t for t in teams if not any(m.can(role) for m in t.members)]
            able = [s for s in unassigned if s.can(role)]
            if len(able) < len(lacking):
                names = ", ".join(t.name for t in lacking)
                reasons.append(f"{role}가 필요한 팀({names})은 {len(lacking)}개인데, 맡을 수 있는 학생은 {len(able)}명임")
    if not reasons:
        reasons.append("팀마다 따로는 채울 수 있지만, 주 역할 중복 없이 모든 팀에 학생을 동시에 나눌 방법이 없음")
    return reasons
