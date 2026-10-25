"""비밀 값 읽기: 환경 변수 → 앱 폴더의 .env → Streamlit Secrets 순서로 찾습니다.

로컬에서는 .env를, Streamlit Community Cloud에서는 Settings → Secrets를 쓰게 됩니다.
값은 화면이나 로그에 출력하지 않습니다. 있는지만 알려면 has_secret()을 씁니다.
"""

import os
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent


@lru_cache(maxsize=1)
def _load_dotenv() -> None:
    path = ROOT / ".env"
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        os.environ.setdefault(name.strip(), value.strip().strip('"').strip("'"))


def get_secret(name: str, default: str | None = None) -> str | None:
    _load_dotenv()
    if os.environ.get(name):
        return os.environ[name]
    try:
        import streamlit as st

        if name in st.secrets:
            return str(st.secrets[name])
    except Exception:
        # secrets.toml이 없으면 st.secrets 접근 자체가 예외를 냅니다.
        pass
    return default


def has_secret(name: str) -> bool:
    return bool(get_secret(name))
