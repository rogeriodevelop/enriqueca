"""Persistência SQLite (Fase 3): jobs, resultados e contas — sobrevive a restart.

Substitui os dicts em memória de `main._jobs` e `billing._accounts`. Mantém as mesmas
funções-chamadas para não quebrar o resto: get/save/list. Produção plena: Postgres
(PLANO.md seção 2), mas SQLite já dá durabilidade + ledger auditável com zero infra.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path

DB_FILE = os.environ.get("ENRIQUECEP_DB", str(Path(__file__).resolve().parent.parent / "data" / "enriquecep.db"))

_lock = threading.Lock()
_conn_box: dict = {}  # conexão ÚNICA e compartilhada (thread-safe via _lock).
                      # Com ":memory:" isso garante que todas as threads veem o MESMO banco.


def _conn() -> sqlite3.Connection:
    with _lock:
        if "c" not in _conn_box:
            c = sqlite3.connect(DB_FILE, check_same_thread=False)
            if DB_FILE != ":memory:":
                Path(DB_FILE).parent.mkdir(parents=True, exist_ok=True)
                c.execute("PRAGMA journal_mode=WAL")
            _create_schema(c)
            _conn_box["c"] = c
        return _conn_box["c"]


def reset_for_tests():
    """Fecha a conexão global — usada pela fixture de testes com ENRIQUECEP_DB=:memory:
    para que cada teste comece com um banco em memória totalmente vazio."""
    with _lock:
        c = _conn_box.pop("c", None)
        if c is not None:
            try:
                c.close()
            except Exception:
                pass


def _create_schema(c: sqlite3.Connection):
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(
      id TEXT PRIMARY KEY, email TEXT, plan TEXT, credits INTEGER,
      llm_hoje INTEGER DEFAULT 0, llm_dia TEXT, criado_em REAL);
    CREATE TABLE IF NOT EXISTS credit_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT, conta_id TEXT, delta INTEGER, motivo TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, conta_id TEXT, status TEXT, total INTEGER, done INTEGER,
      creditos INTEGER, criado_em REAL, removidos_dedup TEXT);
    CREATE TABLE IF NOT EXISTS job_results(
      job_id TEXT, idx INTEGER, payload TEXT, PRIMARY KEY(job_id, idx));
    """)
    c.commit()


def init_db():
    c = _conn()
    c.executescript("""
    CREATE TABLE IF NOT EXISTS accounts(
      id TEXT PRIMARY KEY, email TEXT, plan TEXT, credits INTEGER,
      llm_hoje INTEGER DEFAULT 0, llm_dia TEXT, criado_em REAL);
    CREATE TABLE IF NOT EXISTS credit_ledger(
      id INTEGER PRIMARY KEY AUTOINCREMENT, conta_id TEXT, delta INTEGER, motivo TEXT, ts REAL);
    CREATE TABLE IF NOT EXISTS jobs(
      id TEXT PRIMARY KEY, conta_id TEXT, status TEXT, total INTEGER, done INTEGER,
      creditos INTEGER, criado_em REAL, removidos_dedup TEXT);
    CREATE TABLE IF NOT EXISTS job_results(
      job_id TEXT, idx INTEGER, payload TEXT, PRIMARY KEY(job_id, idx));
    """)
    c.commit()


# ------------------------------- Jobs --------------------------------------

def save_job(job: dict):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO jobs VALUES(?,?,?,?,?,?,?,?)",
              (job["id"], job.get("conta"), job["status"], job["total"], job["done"],
               job.get("creditos", 0), job.get("criado_em", time.time()),
               json.dumps(job.get("removidos_dedup", []))))
    c.commit()


def update_job_progress(job_id: str, done: int, status: str | None = None):
    c = _conn()
    if status:
        c.execute("UPDATE jobs SET done=?, status=? WHERE id=?", (done, status, job_id))
    else:
        c.execute("UPDATE jobs SET done=? WHERE id=?", (done, job_id))
    c.commit()


def save_job_result(job_id: str, idx: int, payload: dict):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO job_results VALUES(?,?,?)",
              (job_id, idx, json.dumps(payload, ensure_ascii=False, default=str)))
    c.commit()


def get_job(job_id: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
    if not row:
        return None
    cols = [d[0] for d in c.execute("SELECT * FROM jobs LIMIT 1").description]
    job = dict(zip(cols, row))
    job["removidos_dedup"] = json.loads(job["removidos_dedup"] or "[]")
    job["results"] = [None] * job["total"]
    for r in c.execute("SELECT idx, payload FROM job_results WHERE job_id=? ORDER BY idx", (job_id,)):
        job["results"][r[0]] = json.loads(r[1])
    return job


def list_jobs(conta_id: str | None = None, limit: int = 50) -> list[dict]:
    c = _conn()
    if conta_id:
        rows = c.execute("SELECT id,status,total,done,creditos,criado_em FROM jobs WHERE conta_id=? "
                         "ORDER BY criado_em DESC LIMIT ?", (conta_id, limit)).fetchall()
    else:
        rows = c.execute("SELECT id,status,total,done,creditos,criado_em FROM jobs "
                         "ORDER BY criado_em DESC LIMIT ?", (limit,)).fetchall()
    return [dict(zip(["id", "status", "total", "done", "creditos", "criado_em"], r)) for r in rows]


# ------------------------------ Contas --------------------------------------

def upsert_account(acct: dict):
    c = _conn()
    c.execute("INSERT OR REPLACE INTO accounts(id,email,plan,credits,llm_hoje,llm_dia,criado_em) "
              "VALUES(?,?,?,?,?,?,?)",
              (acct["id"], acct.get("email", ""), str(acct.get("plano", "free")),
               acct.get("creditos", 0), acct.get("llm_hoje", 0), acct.get("llm_dia", ""),
               acct.get("criado_em", time.time())))
    c.commit()


def load_account(conta_id: str) -> dict | None:
    c = _conn()
    row = c.execute("SELECT id,email,plan,credits,llm_hoje,llm_dia,criado_em FROM accounts WHERE id=?",
                    (conta_id,)).fetchone()
    if not row:
        return None
    acct = dict(zip(["id", "email", "plano", "creditos", "llm_hoje", "llm_dia", "criado_em"], row))
    acct["historico"] = [dict(zip(["delta", "motivo", "ts"], r)) for r in c.execute(
        "SELECT delta,motivo,ts FROM credit_ledger WHERE conta_id=? ORDER BY id DESC LIMIT 20",
        (conta_id,))]
    return acct


def ledger_add(conta_id: str, delta: int, motivo: str):
    c = _conn()
    c.execute("INSERT INTO credit_ledger(conta_id,delta,motivo,ts) VALUES(?,?,?,?)",
              (conta_id, delta, motivo, time.time()))
    c.commit()
