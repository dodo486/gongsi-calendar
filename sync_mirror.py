# -*- coding: utf-8 -*-
"""중앙 저장소(jhts 원격 PG) → 로컬 미러(SQLite) 동기화.

"로컬 미러 + 중앙 동기화" 구성의 동기화 절반. jhts가 중앙에서 수집해 원격
PG에 쌓아두면, 이 스크립트가 그걸 공시캘린더의 로컬 SQLite로 당겨온다.
읽기(md_feed/quotes)는 로컬 SQLite만 보므로 빠르고, 데이터 최신성은 이
스크립트 실행 주기로 정해진다.

실행 위치: **원격 PG 크리덴셜이 있는 곳**(운영 러너/회사 데탑). 로컬 개발기엔
PG 접속 정보가 없으므로 여기선 돌지 않는다.

    # 소스(중앙 PG)와 대상(로컬 미러)을 env로 준다
    export SISE_DB_URL="postgresql://...supabase..."      # 소스(중앙)
    export SISE_DB_PATH="$(pwd)/data/sise.db"             # 대상(로컬 미러)
    python sync_mirror.py                                  # 전체 테이블 복제
    python sync_mirror.py --tables candles investor_flows  # 일부만
    python sync_mirror.py --dry-run                        # 행 수만 확인

의존: psycopg2(원격 PG 읽기용). `pip install "jhts-marketdata[postgres]"`.
"""
import argparse
import os
import sqlite3
import sys
from pathlib import Path

from jhts.marketdata import config, db


def _table_names(sqlite_path):
    """대상 sqlite 스키마를 만들고(멱등) 시세 테이블 이름 목록을 돌려준다."""
    # config를 로컬 sqlite로 고정하고 스키마 생성.
    config.DB_URL = ""
    config.DB_PATH = Path(sqlite_path)
    db.init()
    conn = sqlite3.connect(sqlite_path)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
    finally:
        conn.close()
    return [r[0] for r in rows]


def _read_pg(src_url, table):
    """중앙 PG에서 table 전체를 (컬럼목록, 행리스트)로 읽는다."""
    import psycopg2
    conn = psycopg2.connect(src_url)
    try:
        cur = conn.cursor()
        cur.execute(f"SELECT * FROM {table}")
        cols = [d[0] for d in cur.description]
        rows = cur.fetchall()
        return cols, rows
    finally:
        conn.close()


def _write_sqlite(sqlite_path, table, cols, rows):
    """로컬 미러 table을 통째로 갈아끼운다(멱등 재동기화)."""
    conn = sqlite3.connect(sqlite_path)
    try:
        placeholders = ",".join("?" * len(cols))
        collist = ",".join(cols)
        conn.execute(f"DELETE FROM {table}")
        if rows:
            conn.executemany(
                f"INSERT OR REPLACE INTO {table} ({collist}) VALUES ({placeholders})",
                rows,
            )
        conn.commit()
    finally:
        conn.close()


def main(argv=None):
    ap = argparse.ArgumentParser(description="중앙 PG → 로컬 미러 동기화")
    ap.add_argument("--tables", nargs="*", help="복제할 테이블(생략 시 전체)")
    ap.add_argument("--dry-run", action="store_true", help="쓰지 않고 행 수만 출력")
    args = ap.parse_args(argv)

    src_url = os.environ.get("SISE_DB_URL")
    if not src_url:
        print("ERROR: SISE_DB_URL(중앙 PG)이 없습니다. 러너에서 크리덴셜과 함께 실행하세요.")
        return 2
    dest = os.environ.get("SISE_DB_PATH") or str(
        Path(__file__).resolve().parent / "data" / "sise.db"
    )
    Path(dest).parent.mkdir(parents=True, exist_ok=True)

    tables = args.tables or _table_names(dest)
    print(f"소스(중앙 PG) → 대상 미러: {dest}")
    total = 0
    for t in tables:
        try:
            cols, rows = _read_pg(src_url, t)
        except Exception as e:
            print(f"  [skip] {t}: 읽기 실패 {e}")
            continue
        total += len(rows)
        if args.dry_run:
            print(f"  {t}: {len(rows)}행 (dry-run)")
            continue
        _write_sqlite(dest, t, cols, rows)
        print(f"  {t}: {len(rows)}행 복제")
    print(f"완료 — 총 {total}행")
    return 0


if __name__ == "__main__":
    sys.exit(main())
