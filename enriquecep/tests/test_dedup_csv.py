"""Testes do dedup fuzzy e do importador CSV tolerante (Fase 1→2 do PLANO.md)."""
from app.dedup import name_similarity, match_key, find_duplicates, dedupe
from app.csv_import import guess_mapping, normalize_value, read_leads_csv


# ---------- dedup ----------

def test_name_similarity_acentos_caixa():
    assert name_similarity("Open Knowledge Brasil", "open  knowledge brasil") > 0.95
    assert name_similarity("Padaria São João", "Padaria Sao Joao") > 0.95
    assert name_similarity("Padaria São João", "Auto Peças Central") < 0.5


def test_match_key_prioridade():
    assert match_key({"cnpj": "19.131.243/0001-97", "email": "a@b.com"}).startswith("cnpj:")
    assert match_key({"email": "A@B.com"}) == "email:a@b.com"
    assert match_key({"telefone": "(61) 91234-5678"}).startswith("tel:")
    assert match_key({"nome": "só nome"}) is None


def test_find_duplicates_por_chave_e_nome():
    recs = [
        {"nome": "Maria", "cnpj": "11222333000181"},
        {"nome": "Maria Silva", "cnpj": "11.222.333/0001-81"},   # mesmo cnpj → dup
        {"nome": "Tech Solutions Ltda"},                          # sem chave
        {"nome": "Tech Solution LTDA"},                           # nome similar → dup
        {"nome": "Empresa Totalmente Diferente"},                 # único
    ]
    groups = find_duplicates(recs)
    assert [0, 1] in groups
    assert any(2 in g and 3 in g for g in groups)
    assert all(4 not in g for g in groups)


def test_dedupe_mantem_mais_completo():
    recs = [
        {"nome": "Acme", "email": "x@acme.com"},
        {"nome": "ACME", "email": "", "telefone": "61999998888"},  # mesmo nº de campos → mantém o 1º
        {"nome": "Outra Empresa XYZ"},
    ]
    uniques, removed = dedupe(recs)
    # grupo {0,1} colapsa em 1 representante; o terceiro permanece
    assert len(uniques) == 2
    assert sum(len(g) for g in removed) == 1
    assert removed[0] == [1]
    assert uniques[0]["email"] == "x@acme.com"


# ---------- csv import ----------

DIRTY_CSV = (
    "Nome do Contato;E-Mail;Whatsapp;CEP;CNPJ;Observação\r\n"
    "João Souza;JOAO@ACME.COM;(11) 98765-4321;01310-100;12.345.678/0001-95;lead quente\r\n"
    "Ana Lima;ana@gmail.com;21 3333-2222;70.040-010;;\r\n"
    "João Souza;JOAO@ACME.COM;(11) 98765-4321;01310-100;12.345.678/0001-95;duplicata exata\r\n"
    ";;;;;\r\n"
)

def test_guess_mapping_aliases_sujos():
    m = guess_mapping(["Nome do Contato", "E-Mail", "Whatsapp", "CEP", "CNPJ", "Inscrição Municipal"])
    assert m["Nome do Contato"] == "nome"      # alias exato "nome do contato"
    assert m["E-Mail"] == "email"
    assert m["Whatsapp"] == "telefone"
    assert m["CNPJ"] == "cnpj"
    assert m["Inscrição Municipal"] is None    # IE ≠ CNPJ, não mapeia


def test_normalize_values():
    assert normalize_value("cep", "70.040-010") == "70040010"
    assert normalize_value("cep", "0400-100") == "00400100"      # 7 dígitos → zero à esquerda
    assert normalize_value("cnpj", "12.345.678/0001-95") == "12345678000195"
    assert normalize_value("cnpj", "123.456") == "123.456"       # inválido → mantém original p/ revisão
    assert normalize_value("email", " JOAO@ACME.com ") == "joao@acme.com"
    assert normalize_value("telefone", "(11) 98765-4321") == "11987654321"


def test_read_leads_csv_tolerante():
    out = read_leads_csv(DIRTY_CSV.encode("utf-8-sig"))
    assert out["colunas_ignoradas"] == ["Observação"]
    rows = out["linhas"]
    assert len(rows) == 3                      # linha vazia descartada
    assert rows[0]["nome"] == "João Souza"
    assert rows[0]["email"] == "joao@acme.com"
    assert rows[0]["cep"] == "01310100"
    assert rows[0]["cnpj"] == "12345678000195"
    assert rows[1]["cep"] == "70040010"
    # dedup integrado no /import/csv: a duplicata exata (índice 2) é removida
    uniques, removed_groups = dedupe(rows)
    assert len(uniques) == 2 and removed_groups == [[2]]


def test_read_leads_csv_virgula():
    csv_txt = "name,email,zip\nBob,b@x.com,70040010\n"
    out = read_leads_csv(csv_txt)
    assert out["linhas"][0]["nome"] == "Bob"
    assert out["linhas"][0]["cep"] == "70040010"
