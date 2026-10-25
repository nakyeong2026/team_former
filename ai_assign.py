"""팀이 없는 학생의 AI 배정(공고 2.5절 3~8항).

1차 → 2차 → 3차 모델 순으로 부르고, 모델마다 부른 때부터 120초가 지나면 끊고 다음 모델로 넘어갑니다.
응답이 없거나, 빈 응답·오류이거나, 배정안이 다섯 가지 검사(rules.validate_plan)를 통과하지 못해도 다음 모델로 넘어갑니다.
3차 모델까지 실패하면 앱이 모든 경우를 따져 스스로 배정합니다(rules.find_assignment).
"""

import json
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from dataclasses import dataclass, field
from typing import Callable

import rules
from rules import Student, Team
from settings import get_secret

API_URL = "https://openrouter.ai/api/v1/chat/completions"
# 1차, 2차, 3차. 같은 제공자의 공용 한도에 함께 걸리지 않도록 제공자가 서로 다른 무료 모델로 골랐습니다.
# 2026-10-25 시험: nemotron-3-ultra는 배정안을 냈고(9초, 108초), gemma-4-31b는 자주 HTTP 429,
# laguna-s-2.1은 5초 만에 답하지만 규칙 검사에 걸리기도 함. inkling은 에이전트 도구 전용이라 HTTP 403.
MODELS = [
    "nvidia/nemotron-3-ultra-550b-a55b:free",
    "google/gemma-4-31b-it:free",
    "poolside/laguna-s-2.1:free",
]
MODEL_TIMEOUT = 120  # 모델 하나를 부른 때부터 응답을 다 받을 때까지(초)
MAX_TOKENS = 16000  # 추론 모델이 생각에 8000토큰을 다 써서 빈 응답을 낸 일이 있어 넉넉히 줌
# 키가 없거나 틀리면 다른 모델도 똑같이 실패하므로 AI를 그만 부르고 앱 자체 배정으로 넘어갑니다.
STOP_KINDS = {"config", "auth"}


class AIError(Exception):
    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind
        self.message = message


@dataclass
class Attempt:
    order: int  # 몇 차 모델인지(1~3)
    model: str
    outcome: str  # 성공, 실패
    detail: str
    elapsed: float


@dataclass
class Outcome:
    plan: list[tuple[str, int]] | None
    source: str
    attempts: list[Attempt] = field(default_factory=list)
    infeasible: list[str] = field(default_factory=list)  # 규칙을 지키는 배정이 없을 때 그 이유


# ---------- OpenRouter 호출 ----------

def _error_from_http(status: int, body: dict) -> AIError:
    message = str((body.get("error") or {}).get("message", "")) if isinstance(body, dict) else ""
    if status == 429:
        if "per-day" in message:
            return AIError("daily_limit", "오늘 쓸 수 있는 무료 호출 횟수를 모두 씀(HTTP 429)")
        return AIError("rate_limit", "모델이 혼잡하다며 거절함(HTTP 429)")
    if status in (401, 402):
        return AIError("auth", f"API 키나 크레딧 문제(HTTP {status})")
    if status == 404:
        return AIError("not_found", "모델 제공자에 배포된 모델이 없음(HTTP 404)")
    return AIError("provider", f"모델 제공자 오류(HTTP {status}) {message[:100]}")


def chat_once(model: str, messages: list, timeout: float) -> str:
    """모델을 한 번 불러 응답 본문을 돌려줍니다."""
    api_key = get_secret("OPENROUTER_API_KEY")
    if not api_key:
        raise AIError("config", "OPENROUTER_API_KEY가 설정되지 않음")
    payload = {
        "model": model,
        "messages": messages,
        "max_tokens": MAX_TOKENS,
        # 추론 모델이 생각하는 데 토큰을 다 써서 빈 응답을 내는 일을 줄입니다.
        "reasoning": {"effort": "low"},
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    # OpenRouter는 처리 중에 연결 유지용 공백을 보내므로 소켓 timeout만으로는 끝나지 않습니다.
    # 조금씩 읽으면서 전체 경과 시간으로도 끊습니다.
    deadline = time.monotonic() + timeout
    try:
        with urllib.request.urlopen(req, timeout=min(30, timeout)) as resp:
            chunks = []
            while chunk := resp.read1(65536):
                chunks.append(chunk)
                if time.monotonic() > deadline:
                    raise AIError("timeout", f"{timeout:.0f}초 안에 응답이 끝나지 않음")
            data = json.loads(b"".join(chunks))
    except urllib.error.HTTPError as e:
        try:
            body = json.loads(e.read().decode("utf-8", errors="replace"))
        except ValueError:
            body = {}
        raise _error_from_http(e.code, body) from None
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise AIError("network", f"네트워크 오류나 응답 시간 초과: {e}") from None
    except ValueError:
        raise AIError("bad_response", "응답 본문이 JSON이 아님") from None
    # OpenRouter는 HTTP 200 안에 오류를 담아 보내기도 합니다.
    if isinstance(data.get("error"), dict):
        code = data["error"].get("code")
        raise _error_from_http(code if isinstance(code, int) else 502, data)
    choices = data.get("choices") or []
    content = (choices[0].get("message") or {}).get("content") if choices else None
    if not content or not content.strip():
        raise AIError("empty", "모델이 빈 응답을 보냄")
    return content


def call_with_deadline(model: str, messages: list, timeout: float,
                       on_tick: Callable[[float], None] = lambda _: None) -> str:
    """chat_once를 별도 스레드에서 돌리고, 부른 때부터 timeout초가 지나면 기다리지 않고 끊습니다.

    응답이 천천히 조금씩 와도 전체 대기 시간이 timeout을 넘지 않습니다.
    """
    start = time.monotonic()
    pool = ThreadPoolExecutor(max_workers=1)
    future = pool.submit(chat_once, model, messages, timeout)
    try:
        while True:
            try:
                return future.result(timeout=1)
            except FutureTimeout:
                elapsed = time.monotonic() - start
                if elapsed >= timeout:
                    raise AIError("timeout", f"{timeout:.0f}초 안에 응답하지 않아 끊음") from None
                on_tick(elapsed)
    finally:
        # 끊은 호출은 기다리지 않습니다(그 스레드는 chat_once 안의 deadline에서 스스로 끝남).
        pool.shutdown(wait=False, cancel_futures=True)


# ---------- 프롬프트와 응답 해석 ----------

def _student_json(s: Student) -> dict:
    return {"student_no": s.student_no, "name": s.name, "main_role": s.main_role, "sub_roles": list(s.sub_roles)}


def build_messages(teams: list[Team], unassigned: list[Student]) -> list[dict]:
    situation = {
        "teams": [{"team": t.no, "free_slots": rules.MAX_SIZE - len(t.members),
                   "members": [_student_json(m) for m in t.members]} for t in teams],
        "unassigned_students": [_student_json(s) for s in unassigned],
    }
    system = (
        "너는 팀 프로젝트의 팀 편성을 돕는 도우미다. 팀이 없는 학생을 기존 팀에 배정한다.\n"
        "규칙:\n"
        "1. 팀이 없는 학생은 모두 정확히 한 팀에 배정한다. 이미 팀에 있는 학생은 옮기지 않는다.\n"
        "2. 배정 후 모든 팀의 인원은 2명 이상 4명 이하다(빈 팀이 남으면 안 됨).\n"
        "3. 한 팀 안에 주 역할(main_role)이 같은 학생이 2명 이상이면 안 된다. 보조 역할(sub_roles)은 이 규칙에 쓰지 않는다.\n"
        "4. 모든 팀에는 기획자를 맡을 학생과 웹 프로그래머를 맡을 학생이 서로 다른 사람으로 1명씩 있어야 한다. "
        "주 역할이나 보조 역할 가운데 하나로 그 역할을 가진 학생이 맡을 수 있다.\n"
        "답은 설명 없이 아래 꼴의 JSON 하나만 쓴다.\n"
        '{"assignments": [{"student_no": "학번", "team": 팀번호}]}'
    )
    user = "지금 상황(JSON):\n" + json.dumps(situation, ensure_ascii=False, indent=1)
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_plan(content: str) -> list[tuple[str, int]]:
    """응답에서 배정안을 꺼냅니다. 형식이 틀리면 ValueError."""
    text = re.sub(r"<think>.*?</think>", "", content, flags=re.S)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("응답에 JSON이 없음")
    try:
        data = json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise ValueError(f"JSON이 깨짐({e.msg})") from None
    items = data.get("assignments") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("assignments 목록이 없음")
    plan = []
    for item in items:
        try:
            team = item["team"]
            team_no = int(str(team).replace("팀", "").strip())
            plan.append((str(item["student_no"]).strip(), team_no))
        except (KeyError, TypeError, ValueError):
            raise ValueError(f"배정 항목의 형식이 틀림: {item}") from None
    return plan


# ---------- 배정 전체 흐름 ----------

def run_assignment(teams: list[Team], unassigned: list[Student],
                   on_status: Callable[[str], None] = lambda _: None,
                   on_attempt: Callable[[Attempt], None] = lambda _: None,
                   use_ai: bool = True,
                   call: Callable = call_with_deadline,
                   models: list[str] = MODELS) -> Outcome:
    # 규칙을 지키는 배정이 아예 없으면 AI를 부르지 않습니다(2.5절 8항).
    if rules.find_assignment(teams, unassigned) is None:
        return Outcome(None, "", infeasible=rules.diagnose(teams, unassigned))
    if not unassigned:
        return Outcome([], "배정할 학생이 없음")

    attempts: list[Attempt] = []

    def record(attempt: Attempt) -> None:
        attempts.append(attempt)
        on_attempt(attempt)

    messages = build_messages(teams, unassigned)
    for order, model in enumerate(models if use_ai else [], start=1):
        label = f"{order}차 모델({model})"
        on_status(f"{label}의 응답을 기다리는 중… (최대 {MODEL_TIMEOUT}초)")
        start = time.monotonic()
        try:
            content = call(model, messages, MODEL_TIMEOUT,
                           lambda el, label=label: on_status(
                               f"{label}의 응답을 기다리는 중… {el:.0f}초 지남 (최대 {MODEL_TIMEOUT}초)"))
            plan = parse_plan(content)
        except AIError as e:
            record(Attempt(order, model, "실패", e.message, time.monotonic() - start))
            if e.kind in STOP_KINDS:
                break
            continue
        except ValueError as e:
            record(Attempt(order, model, "실패", f"응답 형식 오류: {e}", time.monotonic() - start))
            continue
        errors = rules.validate_plan(teams, unassigned, plan)
        elapsed = time.monotonic() - start
        if errors:
            record(Attempt(order, model, "실패", "규칙 검사 불통과: " + "; ".join(errors), elapsed))
            continue
        record(Attempt(order, model, "성공", "배정안이 규칙 검사를 통과함", elapsed))
        return Outcome(plan, f"{order}차 모델 {model}", attempts)

    on_status("앱이 모든 경우를 따져 직접 배정하는 중…")
    plan = rules.find_assignment(teams, unassigned)
    reason = "AI를 부르지 않음(시험용 설정)" if not use_ai else "AI가 3차 모델까지 실패"
    return Outcome(plan, f"앱 자체 배정 — {reason}", attempts)
