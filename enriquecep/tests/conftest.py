"""Isolamento de estado entre testes.

Os módulos da app mantêm caches em processo (contas/créditos, jobs, rate limit).
Sem reset, um teste herda saldo/gastos do anterior (flaky por ordem de execução).
A fixture autouse abaixo zera tudo antes de cada teste; com ENRIQUECEP_DB=":memory:"
(setado no pytest.ini) o SQLite também nasce vazio por conexão/thread.
"""
import os

import pytest

os.environ.setdefault("ENRIQUECEP_DB", ":memory:")


@pytest.fixture(autouse=True)
def _isolated_state():
    from app import billing, ratelimit, storage
    from app import main as main_mod

    billing._accounts.clear()
    ratelimit._hits.clear()
    main_mod._jobs.clear()
    # com ENRIQUECEP_DB=:memory:, fechar a conexão global recria o banco vazio p/ cada teste
    storage.reset_for_tests()
    storage.init_db()
    yield
