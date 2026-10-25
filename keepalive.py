"""Supabase DB가 일시정지되지 않도록 매일 한 번 질의합니다(.github/workflows/keepalive.yml에서 실행).

DATABASE_URL은 깃허브 저장소의 Actions secret에서 받습니다. 주소와 비밀번호는 출력하지 않습니다.
"""

import os
import sys

import psycopg

url = os.environ.get("DATABASE_URL")
if not url:
    sys.exit("DATABASE_URL 없음: 저장소 Settings → Secrets and variables → Actions에 넣어 주세요.")
with psycopg.connect(url, prepare_threshold=None, connect_timeout=15) as conn:
    count = conn.execute("SELECT count(*) FROM tf_students").fetchone()[0]
print(f"DB 질의 성공: tf_students {count}행")
