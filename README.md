# 팀 편성 웹 앱

학생들이 팀 프로젝트를 위한 팀을 스스로 편성하고, 모집을 마감한 뒤 남은 학생은 AI가 배정하는 Streamlit 앱입니다. 앱은 AI의 배정안을 규칙으로 검사해 통과한 것만 관리자에게 보여 줍니다.

> 작성자: (이름)

## 1. 배포 주소

https://teamformer-wwx6vvodyxhojsw9j4kydw.streamlit.app/

## 2. PRD와 구현 계획

- PRD: [docs/PRD.md](docs/PRD.md)
- 구현 계획: [docs/구현 계획.md](docs/구현%20계획.md)

## 3. 고른 해결 방법(공고 3장)

| 기능 | 고른 해결 | 구현 |
|---|---|---|
| 다른 사람의 변경 보기 | 어려운 해결 | `st.fragment(run_every=5)`로 팀 목록·투표·팀이 없는 학생을 5초마다 다시 그림 |
| 필수 포함 역할을 지킬 수 없는 팀 | 어려운 해결 | 지원할 때 남는 자리 수와 아직 못 채운 필수 역할 수(최대 매칭)를 비교해 막음(`rules.join_problem`) |
| 같은 팀에 대기 중인 지원이 여럿 | 어려운 해결 | 모든 팀원이 찬성한 순간 정원·주 역할 중복·필수 역할 자리를 다시 검사(`service.vote`) |
| 마지막 자리에 두 명이 동시에 들어감 | 어려운 해결 | `tf_members`의 `(team_id, slot_no)` 기본 키와 `student_id` 유일성 제약([schema.sql](schema.sql)) |
| AI 응답이 늦을 때 | 어려운 해결 | 호출을 별도 스레드에서 돌리고, 부른 때부터 120초가 지나면 기다리지 않고 다음 모델로 넘어감(`ai_assign.call_with_deadline`) |
| 3차 모델까지 실패 | 어려운 해결 | 백트래킹으로 다섯 가지 검사를 모두 통과하는 배정을 끝까지 찾음(`rules.find_assignment`) |
| AI 대기 중 화면 | 어려운 해결 | `assign_status`에 "N차 모델(…)의 응답을 기다리는 중… M초 지남 (최대 120초)"과 앞 모델의 실패 이유를 보여 줌 |
| DB 일시정지 막기 | 어려운 해결 | GitHub Actions 예약 실행([.github/workflows/keepalive.yml](.github/workflows/keepalive.yml))이 매일 09:00(한국 시각)에 DB에 질의 |

## 4. 쓴 모델 3개

1차 `nvidia/nemotron-3-ultra-550b-a55b:free`, 2차 `google/gemma-4-31b-it:free`, 3차 `poolside/laguna-s-2.1:free`

제공자(NVIDIA, Google AI Studio, Poolside)를 서로 다르게 골라, 한 제공자의 공용 한도(HTTP 429)에 셋이 함께 걸리지 않게 했습니다. 무료 자동 라우터 `openrouter/free`는 쓰지 않습니다.

## 5. AI 모델 호출 실패 사례

- **1차 모델이 HTTP 429로 거절해 2차 모델로 넘어감.** 2026-10-25 어려움 연습 시나리오 15단계(로컬 실행). 그때 1차였던 `google/gemma-4-31b-it:free`가 1초 만에 "혼잡"(HTTP 429)으로 거절했고, 앱이 2차 `nvidia/nemotron-3-ultra-550b-a55b:free`로 넘어가 108초 만에 검사를 통과한 배정안을 받음. 화면: [img/ai-429-fallback.jpg](img/ai-429-fallback.jpg). 같은 날 쉬움 시나리오 6단계에서도 같은 일이 일어남(2차 모델이 9초 만에 성공).
- **배정안이 규칙 검사에 걸림.** 같은 날 어려움 시나리오 마감 직후 상황으로 `poolside/laguna-s-2.1:free`를 불렀더니 5초 만에 답했지만 "팀1에 주 역할이 프론트엔드 디자이너인 학생이 2명", "팀3에 웹 프로그래머를 맡을 학생이 없음"으로 `validate_plan`이 걸러 냄(시험 스크립트 출력).
- **빈 응답.** `recursive/flint-v1.0:free`가 72초 뒤 빈 응답을 보냄. 응답을 열어 보니 `finish_reason=length`, 완성 토큰 8000개 중 추론 토큰이 6448개로, 생각하는 데 `max_tokens`를 다 써 버린 것이었음. 그래서 `max_tokens`를 16000으로 늘림.

## 6. 클로드가 잘못 추론한 사례

- **쓸 수 없는 모델을 3차 모델로 고름.** 클로드는 OpenRouter의 `/models/{id}/endpoints`에서 `status 0`, 가동률 100%인 것만 보고 `thinkingmachines/inkling:free`를 3차 모델로 정함. 실제로 한 번 불러 보니 HTTP 403 "only available on agentic harnesses"로 거절되어, 이 모델은 일반 API 호출로는 쓸 수 없음을 알게 됨. 3차 모델을 실제 호출 결과로 다시 골라 `poolside/laguna-s-2.1:free`로 바꿈(`ai_assign.py`의 `MODELS` 주석. 바뀐 모델 목록은 첫 커밋 `4577b89`에 들어 있음).
- **추론 모델의 토큰 한도를 너무 작게 잡음.** 클로드가 처음 정한 `max_tokens` 8000으로는 추론 모델 여러 개가 빈 응답을 냄(5항 세 번째 사례).

## 7. 발견한 문제점과 개선 필요 사항

2026-10-25에 배포한 앱으로 연습용 시나리오 3개를 처음부터 끝까지 다시 해 보며 찾은 것입니다(세 시나리오 모두 단계마다 기대 결과와 같았음).

- **AI 배정이 120초 제한에 거의 닿음(고치지 못함).** 부록 2(중간) 11단계에서 1차 모델 `nemotron-3-ultra`가 **117초** 만에 배정안을 냄. 3초만 늦었어도 끊고 2차로 넘어갔을 것임. 기다리는 동안 화면: [img/medium-step11-ai-waiting.jpg](img/medium-step11-ai-waiting.jpg), 결과: [img/medium-step11-ai-117s.jpg](img/medium-step11-ai-117s.jpg). 부록 3(어려움) 15단계에서도 86초가 걸림([img/hard-step15-ai-waiting.jpg](img/hard-step15-ai-waiting.jpg), [img/hard-step15-confirmed.jpg](img/hard-step15-confirmed.jpg)). 1차가 늦게 실패하고 2차·3차도 오래 걸리면 관리자가 최대 6분 기다려야 함. 같은 날 시험한 무료 모델 7개 가운데 규칙을 지키는 배정안을 낸 것은 이 모델 하나뿐이라 빠른 모델로 바꿀 수도 없었음. 3차까지 실패해도 앱 자체 배정으로 끝나지만, 프롬프트를 줄이거나 AI에 학생별로 갈 수 있는 팀 목록을 미리 계산해 주는 개선이 필요함.
- **막힐 지원의 단추도 눌러 봐야 이유를 앎(고치지 못함).** 부록 3(어려움) 11단계에서 학생14(RW;TT)의 화면에 "팀3에 지원" 단추가 활성 상태로 보이지만, 누르면 "막힘: 필수 포함 역할…자리가 모자람"이 나옴([img/hard-step11-required-blocked.jpg](img/hard-step11-required-blocked.jpg)). 부록 2(중간) 3단계의 "같은 주 역할" 막힘도 같음([img/medium-step3-blocked.jpg](img/medium-step3-blocked.jpg)). 공고상 허용되지만, 단추 옆에 막히는 이유를 미리 보여 주면 더 좋음.
- **단추 반응이 2~3초 걸림(일부 고침).** 배포 앱에서 지원·투표 단추를 누른 뒤 결과 안내가 보이기까지 2.2~2.9초가 걸림(배포 서버와 서울 Supabase 사이의 왕복). 게다가 화면마다 5초마다 DB 질의를 6번 해서, 채점처럼 학생 창이 여러 개 열리면 DB 작업이 밀릴 수 있었음. 갱신용 조회를 모든 사용자가 최대 3초 동안 함께 쓰고 쓰기가 있으면 바로 새로 읽게 고침(커밋 `78560af`). 단추 하나의 왕복 시간은 그대로임.
- **마감 직후 "팀이 없는 학생"이 잠깐 예전 값으로 보임(고침).** 부록 2(중간) 9단계에서 모집 마감을 누르자 결과 안내는 바로 바뀌었는데 `unassigned`는 잠깐 6명(마감 전 값)으로 보였다가 8명으로 바뀜. 결과 안내를 화면 맨 마지막에 채우도록 고쳐(커밋 `2e01c28`), 부록 3(어려움) 14단계에서는 안내가 바뀐 순간 이미 8명으로 보이는 것을 확인함.

배포 앱 시험 화면: 중간 [8단계 대기](img/medium-step8-pending.jpg), [10단계 마감 뒤 막힘](img/medium-step10-closed.jpg), 어려움 [8단계 승인 직전 검사로 거절](img/hard-step8-recheck.jpg)

---

## 실행 방법(로컬)

```bash
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pytest
cp .env.example .env    # 값 채우기. DATABASE_URL을 비우면 data/app.db(SQLite)를 씀
.venv/Scripts/python -m streamlit run streamlit_app.py
.venv/Scripts/python -m pytest -q   # 규칙, 연습 시나리오 3개, AI 전환 시험
```

## 파일

| 파일 | 내용 |
|---|---|
| `streamlit_app.py` | 화면(로그인, 학생, 관리자). key는 공고 2.7절 표를 따름 |
| `rules.py` | 팀 규칙, 배정안 검사 다섯 가지, 앱 자체 배정 |
| `service.py` | 명단 올리기, 로그인, 지원, 투표, 마감, 배정안 확정 |
| `ai_assign.py` | OpenRouter 호출, 120초 제한, 1→2→3차 모델 |
| `db.py`, `schema.sql` | DB 연결(연결 하나를 다시 씀)과 `tf_` 테이블 |
| `auth.py` | 비밀번호 해시(PBKDF2-SHA256) |
| `keepalive.py`, `.github/workflows/keepalive.yml` | 매일 DB 질의 |
| `samples/` | 연습용 명단 CSV(가상 학생) |
