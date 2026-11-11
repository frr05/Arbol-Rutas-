"""Detección de huecos en la numeración de las Bases (revisor de estructura, sin LLM)."""
from mt.bases import _sospechas


def _numeros(sospechas):
    return {s["numero"]: s["motivo"] for s in sospechas}


def test_sin_huecos():
    assert _sospechas(["1", "1.1", "1.2", "1.2.1", "2", "2.1"], []) == []


def test_salto_entre_hermanos():
    assert _numeros(_sospechas(["1", "1.1", "1.1.1", "1.1.2", "1.1.4", "1.2"], [])) == {"1.1.3": "salto"}


def test_hijo_sin_padre_y_primer_hermano():
    # caso RITP: 2.3 → 2.3.2.1 (faltan 2.3.2, el padre, y 2.3.1, su hermano anterior)
    r = _numeros(_sospechas(["2", "2.1", "2.2", "2.3", "2.3.2.1", "2.3.2.2"], []))
    assert r == {"2.3.2": "padre", "2.3.1": "salto"}


def test_entrada_del_indice_que_falta():
    indice = [{"clave": "1.1", "titulo": "Introducción"}, {"clave": "1.2", "titulo": "Antecedentes"}]
    r = _sospechas(["1", "1.1"], indice)
    assert r == [{"numero": "1.2", "motivo": "indice", "titulo": "Antecedentes"}]


def test_salto_demasiado_grande_se_ignora():
    # 1.1 → 1.40: más probable un numeral mal leído que 38 artículos perdidos
    assert "1.20" not in _numeros(_sospechas(["1", "1.1", "1.40"], []))


def test_partes_de_primer_nivel_no_empiezan_en_uno():
    assert _sospechas(["3", "3.1", "4", "4.1"], []) == []
