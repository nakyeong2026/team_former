"""팀 프로젝트를 위한 팀 편성 웹 앱(Streamlit).

화면 요소의 key는 공고 2.7절의 표를 그대로 따릅니다(자동 채점 프로그램이 key로 찾음).
다른 사람의 변경은 st.fragment(run_every=3)로 바뀐 부분만 3초마다 다시 그립니다.
(공고: 5초 이하. 채점 프로그램은 단추가 나타나기를 7초까지 기다리므로, 조회 시간까지 더해도 여유가 있게 3초로 둠)
"""

from datetime import datetime, timedelta, timezone

import streamlit as st

import ai_assign
import service
from db import Database
from rules import REQUIRED, Team
from service import State
from settings import get_secret

KST = timezone(timedelta(hours=9))
REFRESH_SECONDS = 3

st.set_page_config(page_title="팀 편성", page_icon="👥", layout="wide")


# ---------- 공용 ----------

# db.py를 고치면 이 숫자를 올립니다. 배포 서버에 캐시된 예전 Database 객체를 버리고 새로 만들게 합니다.
DB_CODE_VERSION = 2


@st.cache_resource(show_spinner=False)
def get_db(url: str | None, code_version: int) -> Database:
    """연결은 연결 문자열마다 한 번만 만들고 모든 사용자가 다시 씁니다."""
    return Database(url)


@st.cache_resource(show_spinner=False)
def _admin_ready(url: str | None, password: str, code_version: int) -> bool:
    service.ensure_admin(get_db(url, code_version), password)
    return True


def database() -> Database:
    url = get_secret("DATABASE_URL")
    password = get_secret("ADMIN_PASSWORD")
    if password:
        _admin_ready(url, password, DB_CODE_VERSION)
    return get_db(url, DB_CODE_VERSION)


def show_result(text: str | None) -> None:
    if not text:
        return
    kind = text.split(":", 1)[0]
    {"승인": st.success, "대기": st.info, "거절": st.warning}.get(kind, st.error)(text)


def set_result(result) -> None:
    st.session_state.result = str(result)


def logout() -> None:
    for key in ("user", "result", "assign_log"):
        st.session_state.pop(key, None)


def now_text() -> str:
    return datetime.now(KST).strftime("%H:%M:%S")


def required_text(team: Team) -> str:
    marks = []
    for role in REQUIRED:
        ok = any(m.can(role) for m in team.members)
        marks.append(f"{role} {'✅' if ok else '❌'}")
    return " · ".join(marks)


def render_team(state: State, team: Team, viewer_id: int | None = None) -> None:
    """팀 하나(key=team_N). 학생 화면이면 지원 단추와 투표 단추도 그립니다."""
    my_team = state.team_of.get(viewer_id) if viewer_id else None
    with st.container(key=f"team_{team.no}", border=True):
        title = f"### {team.name}  ({len(team.members)}/4명)"
        if my_team == team.no:
            title += "  :blue-background[내 팀]"
        st.markdown(title)
        if team.members:
            for m in team.members:
                subs = ", ".join(m.sub_roles) if m.sub_roles else "없음"
                st.markdown(f"- **{m.name}** — 주 역할 {m.main_role} / 보조 역할 {subs}")
        else:
            st.caption("아직 팀원이 없음")
        st.caption("필수 포함 역할: " + required_text(team))

        member_ids = state.member_ids(team.no)
        for app in state.pending_for_team(team.no):
            applicant = state.students[app["student_id"]]
            votes = state.votes.get(app["id"], {})
            yes = sum(1 for i in member_ids if votes.get(i) == "찬성")
            st.markdown(f"🙋 지원자 **{applicant.name}** ({applicant.main_role}) — 대기, "
                        f"찬성 {yes}/{len(member_ids)}")
            if viewer_id in member_ids:
                mine = votes.get(viewer_id)
                if mine:
                    st.caption(f"내 투표: {mine}")
                else:
                    c1, c2 = st.columns(2)
                    c1.button("찬성", key=f"vote_yes_{applicant.student_no}", type="primary",
                              on_click=do_vote, args=(viewer_id, app["id"], True), width="stretch")
                    c2.button("거부", key=f"vote_no_{applicant.student_no}",
                              on_click=do_vote, args=(viewer_id, app["id"], False), width="stretch")

        if viewer_id is not None:
            # 이미 팀이 있거나, 결과를 기다리는 지원이 있거나, 마감되었으면 지원 단추를 비활성화합니다(2.8절).
            disabled = bool(my_team or state.pending_of(viewer_id) or state.closed)
            st.button(f"{team.name}에 지원", key=f"apply_{team.no}", disabled=disabled,
                      on_click=do_apply, args=(viewer_id, team.no))


def render_teams(state: State, viewer_id: int | None = None) -> None:
    if not state.teams:
        st.info("아직 팀이 없음. 관리자가 명단을 올리면 팀이 만들어짐")
        return
    columns = st.columns(2)
    for index, team in enumerate(state.teams):
        with columns[index % 2]:
            render_team(state, team, viewer_id)


# ---------- 단추 동작 ----------

def do_apply(student_id: int, team_no: int) -> None:
    set_result(service.apply(database(), student_id, team_no))


def do_vote(voter_id: int, app_id: int, approve: bool) -> None:
    set_result(service.vote(database(), voter_id, app_id, approve))


def do_start() -> None:
    file = st.session_state.get("admin_roster_file")
    count = int(st.session_state.get("admin_team_count") or 0)
    set_result(service.start(database(), file.getvalue() if file else None, count))
    st.session_state.pop("assign_log", None)


def do_close() -> None:
    set_result(service.close(database()))
    st.session_state.pop("assign_log", None)


def do_confirm() -> None:
    set_result(service.confirm(database()))


# ---------- 로그인 ----------

def login_page() -> None:
    st.title("팀 편성")
    st.caption("학번과 비밀번호로 로그인합니다. 관리자의 학번은 admin입니다.")
    with st.form("login_form"):
        student_no = st.text_input("학번", key="login_student_no")
        password = st.text_input("비밀번호", type="password", key="login_password")
        submitted = st.form_submit_button("로그인", key="login_submit", type="primary")
    if submitted:
        user = service.login(database(), student_no, password)
        if user:
            st.session_state.user = user
            st.session_state.pop("result", None)
            st.rerun()
        st.session_state.result = "막힘: 학번이나 비밀번호가 틀림"
    with st.container(key="result"):
        show_result(st.session_state.get("result"))
    if not get_secret("ADMIN_PASSWORD"):
        st.warning("ADMIN_PASSWORD가 설정되지 않아 관리자 계정을 만들 수 없음(.env 또는 Streamlit Secrets)")


# ---------- 학생 화면 ----------

def student_page(user: dict) -> None:
    st.title("팀 편성")
    with st.container(key="whoami"):
        c1, c2 = st.columns([5, 1])
        c1.markdown(f"로그인: **{user['name']}** ({user['student_no']})")
        c2.button("로그아웃", key="logout", on_click=logout)
    student_live(user["id"])


@st.fragment(run_every=REFRESH_SECONDS)
def student_live(student_id: int) -> None:
    state = service.load_state(database())
    # 결과 안내는 맨 위에 자리만 잡아 두고 맨 마지막에 채웁니다.
    # 안내가 바뀌었을 때는 아래 팀 목록도 이미 새 상태로 그려져 있게 하려는 것입니다.
    result_box = st.container(key="result")
    me = state.students.get(student_id)
    if me is None:
        st.warning("명단이 새로 올라와 내 계정이 없어짐. 로그아웃한 뒤 다시 로그인해 주세요.")
        return
    my_team = state.team_of.get(student_id)
    pending = state.pending_of(student_id)
    line = f"내 역할: {me.roles_text} · "
    line += f"내 팀: **팀{my_team}**" if my_team else "**아직 팀이 없음**"
    if pending:
        line += f" · 팀{pending['team_id']}에 낸 지원의 결과를 기다리는 중"
    if state.closed:
        line += " · **모집이 마감됨**"
    st.markdown(line)
    st.caption(f"화면은 {REFRESH_SECONDS}초마다 저절로 바뀜 (마지막 갱신 {now_text()}, 한국 시각)")
    render_teams(state, student_id)
    mine = [a for a in state.apps if a["student_id"] == student_id]
    if mine:
        with st.expander("내 지원 기록"):
            for app in reversed(mine):
                st.markdown(f"- 팀{app['team_id']}: **{app['status']}** {('— ' + app['reason']) if app['reason'] else ''}")
    with result_box:
        show_result(st.session_state.get("result"))


# ---------- 관리자 화면 ----------

def admin_page() -> None:
    st.title("팀 편성 · 관리자")
    with st.container(key="whoami"):
        c1, c2 = st.columns([5, 1])
        c1.markdown("로그인: **관리자** (admin)")
        c2.button("로그아웃", key="logout", on_click=logout)
    # 연결 주소는 보여 주지 않고 종류만 알립니다.
    if database().kind == "postgres":
        st.caption("저장소: Supabase(Postgres)")
    else:
        st.warning("저장소: 앱 서버 안의 임시 SQLite 파일. DATABASE_URL이 없어서 앱이 다시 시작되면 내용이 사라짐")
    # 결과 안내는 자리만 잡아 두고 화면 맨 마지막에 채웁니다(팀이 없는 학생 표시가 먼저 새로 그려지게).
    with st.container(key="result"):
        result_slot = st.empty()

    st.subheader("명단 올리기")
    st.caption("명단 CSV(student_no,name,main_role,sub_roles)와 팀 수를 넣으면 지원·투표·팀원·마감 표시를 모두 지우고 "
               "처음 상태를 만듭니다. 학생의 비밀번호는 모두 1234입니다.")
    st.file_uploader("명단 CSV", type=["csv"], key="admin_roster_file")
    st.number_input("팀 수", min_value=1, max_value=20, value=3, step=1, key="admin_team_count")
    st.button("명단으로 처음 상태 만들기", key="admin_start", on_click=do_start)

    st.divider()
    st.subheader("마감과 배정")
    admin_live()
    st.button("모집 마감", key="admin_close", on_click=do_close, type="primary")
    skip_ai = st.checkbox("시험용: AI를 부르지 않고 앱 자체 배정만 함(무료 호출 아끼기, 채점 때는 끔)",
                          key="admin_skip_ai")
    clicked = st.button("AI 배정", key="admin_assign", type="primary")
    status_box = st.container(key="assign_status")
    if clicked:
        result_slot.empty()
        run_assign(status_box, skip_ai)
    render_assign_log(status_box)
    render_proposal()
    st.button("배정안 확정", key="admin_confirm", on_click=do_confirm, type="primary")

    with st.expander("최근 AI 호출 기록"):
        logs = service.recent_ai_logs(database())
        if logs:
            st.dataframe(
                [{"몇 차": f"{r['attempt_no']}차", "모델": r["model"], "결과": r["outcome"],
                  "걸린 시간(초)": r["elapsed_sec"], "내용": r["detail"]} for r in logs],
                hide_index=True)
        else:
            st.caption("아직 기록이 없음")
    with result_slot:
        show_result(st.session_state.get("result"))


@st.fragment(run_every=REFRESH_SECONDS)
def admin_live() -> None:
    state = service.load_state(database())
    st.markdown(f"모집 상태: **{'마감' if state.closed else '모집 중'}** · 학생 {len(state.students)}명 · "
                f"팀 {len(state.teams)}개")
    with st.container(key="unassigned"):
        names = ", ".join(s.name for s in state.unassigned) or "없음"
        st.markdown(f"**팀이 없는 학생 ({len(state.unassigned)}명)**: {names}")
    st.caption(f"화면은 {REFRESH_SECONDS}초마다 저절로 바뀜 (마지막 갱신 {now_text()}, 한국 시각)")
    with st.expander("현황: 팀 구성", expanded=True):
        render_teams(state)


def run_assign(box, skip_ai: bool) -> None:
    """AI 배정을 실행하면서 몇 차 모델을 기다리는지 assign_status에 보여 줍니다."""
    db = database()
    state = service.load_state(db)
    if not state.closed:
        set_result("막힘: 모집을 먼저 마감해야 함")
        st.session_state.pop("assign_log", None)
        st.rerun()
    lines: list[str] = []
    with box:
        live = st.empty()

    def on_status(text: str) -> None:
        live.info("\n\n".join(lines + [f"⏳ {text}"]))

    def on_attempt(a: ai_assign.Attempt) -> None:
        lines.append(f"{a.order}차 모델 `{a.model}`: **{a.outcome}** — {a.detail} ({a.elapsed:.0f}초)")

    outcome = ai_assign.run_assignment(state.teams, state.unassigned, on_status=on_status,
                                       on_attempt=on_attempt, use_ai=not skip_ai)
    if outcome.attempts:
        service.log_ai(db, outcome.attempts)
    if outcome.plan is None:
        reason = "; ".join(outcome.infeasible)
        st.session_state.assign_log = {"ok": False, "lines": ["**규칙을 지키는 배정이 없음**"] + outcome.infeasible}
        set_result(f"막힘: 규칙을 지키는 배정이 없음 — {reason}")
    else:
        service.save_proposal(db, outcome.plan, outcome.source)
        st.session_state.assign_log = {"ok": True, "lines": lines + [f"배정안을 만든 곳: **{outcome.source}**"]}
        set_result(f"승인: 배정안을 만듦({outcome.source}). 확인한 뒤 확정을 누름")
    st.rerun()


def render_assign_log(box) -> None:
    log = st.session_state.get("assign_log")
    if not log:
        return
    with box:
        (st.success if log["ok"] else st.error)("\n\n".join(log["lines"]))


def render_proposal() -> None:
    state = service.load_state(database())
    if not state.proposal:
        return
    by_no = {s.student_no: s for s in state.students.values()}
    st.markdown(f"**확정 전 배정안** ({state.proposal['source']}) — 규칙 검사 다섯 가지를 통과함")
    st.dataframe(
        [{"학생": by_no[no].name, "주 역할": by_no[no].main_role,
          "보조 역할": ", ".join(by_no[no].sub_roles) or "없음", "배정된 팀": f"팀{team}"}
         for no, team in state.proposal["plan"] if no in by_no],
        hide_index=True)


# ---------- 시작 ----------

user = st.session_state.get("user")
if not user:
    login_page()
elif user["is_admin"]:
    admin_page()
else:
    student_page(user)
