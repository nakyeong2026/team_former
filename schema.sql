-- 팀 편성 앱의 테이블(Supabase Postgres). 이름은 모두 tf_ 로 시작합니다.
-- 앱(db.py)이 시작할 때 이 파일을 그대로 읽어 실행하므로, 앱이 쓰는 테이블·열·제약과 이 파일은 항상 같습니다.
-- 로컬 SQLite에서는 db.py가 자료형만 바꿔(BIGSERIAL → INTEGER, TIMESTAMPTZ → TEXT) 실행하고 RLS 줄은 건너뜁니다.

-- 학생과 관리자. 비밀번호는 PBKDF2-SHA256 해시로만 저장합니다(원래 비밀번호는 저장하지 않음).
CREATE TABLE IF NOT EXISTS tf_students (
    id BIGSERIAL PRIMARY KEY,
    student_no TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL,
    main_role TEXT CHECK (main_role IN ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    password_hash TEXT NOT NULL,
    is_admin BOOLEAN NOT NULL DEFAULT FALSE
);

-- 보조 역할. 학생 1명에 0~2행
CREATE TABLE IF NOT EXISTS tf_student_sub_roles (
    student_id BIGINT NOT NULL REFERENCES tf_students(id) ON DELETE CASCADE,
    role TEXT NOT NULL CHECK (role IN ('기획자', '웹 프로그래머', '프론트엔드 디자이너', '테스터', '보고서 작성자', '발표자')),
    PRIMARY KEY (student_id, role)
);

-- 팀. id가 곧 팀 번호(팀1의 id는 1)이며, 명단을 올릴 때마다 1부터 다시 만듭니다.
CREATE TABLE IF NOT EXISTS tf_teams (
    id INTEGER PRIMARY KEY,
    name TEXT NOT NULL UNIQUE
);

-- 팀원. (team_id, slot_no) 기본 키와 student_id 유일성 제약이
-- "마지막 자리에 두 명이 동시에 들어가면 먼저 도착한 학생만 기록"과 "한 학생은 한 팀에만"을 DB에서 지킵니다.
CREATE TABLE IF NOT EXISTS tf_members (
    team_id INTEGER NOT NULL REFERENCES tf_teams(id) ON DELETE CASCADE,
    slot_no INTEGER NOT NULL CHECK (slot_no BETWEEN 1 AND 4),
    student_id BIGINT NOT NULL UNIQUE REFERENCES tf_students(id) ON DELETE CASCADE,
    joined_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (team_id, slot_no)
);

-- 지원
CREATE TABLE IF NOT EXISTS tf_applications (
    id BIGSERIAL PRIMARY KEY,
    team_id INTEGER NOT NULL REFERENCES tf_teams(id) ON DELETE CASCADE,
    student_id BIGINT NOT NULL REFERENCES tf_students(id) ON DELETE CASCADE,
    status TEXT NOT NULL CHECK (status IN ('대기', '승인', '거절')),
    reason TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at TIMESTAMPTZ
);

-- 한 학생은 결과를 기다리는 지원을 하나만 가질 수 있음
CREATE UNIQUE INDEX IF NOT EXISTS tf_applications_one_pending
    ON tf_applications (student_id) WHERE status = '대기';

-- 투표. 한 팀원은 한 지원에 한 번만 투표하고 바꿀 수 없음
CREATE TABLE IF NOT EXISTS tf_votes (
    application_id BIGINT NOT NULL REFERENCES tf_applications(id) ON DELETE CASCADE,
    voter_id BIGINT NOT NULL REFERENCES tf_students(id) ON DELETE CASCADE,
    vote TEXT NOT NULL CHECK (vote IN ('찬성', '거부')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (application_id, voter_id)
);

-- 마감 여부(closed), 확정 전 배정안(proposal) 등
CREATE TABLE IF NOT EXISTS tf_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

-- AI 호출 기록(몇 차 모델, 결과, 걸린 시간). README의 실패 사례 근거로 씁니다.
CREATE TABLE IF NOT EXISTS tf_ai_logs (
    id BIGSERIAL PRIMARY KEY,
    model TEXT NOT NULL,
    attempt_no INTEGER NOT NULL,
    outcome TEXT NOT NULL,
    detail TEXT,
    elapsed_sec REAL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Supabase 데이터 API(anon 키)로 표를 읽지 못하게 막습니다. 앱은 표 소유자로 직접 연결하므로 영향이 없습니다.
ALTER TABLE tf_students ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_student_sub_roles ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_teams ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_members ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_applications ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_votes ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_settings ENABLE ROW LEVEL SECURITY;
ALTER TABLE tf_ai_logs ENABLE ROW LEVEL SECURITY;
