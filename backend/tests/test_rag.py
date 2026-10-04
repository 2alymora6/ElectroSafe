import os
import tempfile

os.environ["DB_PATH"] = os.path.join(tempfile.mkdtemp(), "test.db")

import fitz  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

os.environ.pop("ANTHROPIC_API_KEY", None)  # las pruebas no llaman al modelo
client = TestClient(main.app)

# Texto del PDF de prueba: solo sirve para probar el software, no es informacion tecnica del equipo.
FRASE = "Este documento de prueba indica que la inspeccion visual del cable es obligatoria antes de cada uso."


def pdf_bytes(paginas):
    doc = fitz.open()
    for texto in paginas:
        page = doc.new_page()
        page.insert_textbox((72, 72, 520, 770), texto)
    data = doc.tobytes()
    doc.close()
    return data


def test_flujo_completo():
    assert client.get("/salud").json()["estado"] == "ok"

    r = client.post(
        "/documentos",
        files={"archivo": ("prueba.pdf", pdf_bytes(["Pagina uno sin relacion.", FRASE]), "application/pdf")},
        data={"nombre": "Documento de prueba (v1)", "tipo": "Manual"},
    )
    assert r.status_code == 201
    doc_id = r.json()["id"]
    assert r.json()["paginas"] == 2 and r.json()["fragmentos"] == 2

    lista = client.get("/documentos").json()
    assert lista[0]["nombre"] == "Documento de prueba (v1)" and lista[0]["fragmentos"] == 2

    ok = client.post("/chat", json={"pregunta": "inspeccion visual del cable"}).json()
    assert ok["encontrado"] is True
    assert ok["fuentes"][0]["pagina"] == 2
    assert ok["fuentes"][0]["documento"] == "Documento de prueba (v1)"

    fuera = client.post("/chat", json={"pregunta": "cual es la capital de Francia"}).json()
    assert fuera["encontrado"] is False
    assert fuera["respuesta"] == main.NO_ENCONTRADO
    assert fuera["fuentes"] == []

    assert client.delete(f"/documentos/{doc_id}").status_code == 204
    assert client.delete(f"/documentos/{doc_id}").status_code == 404
    assert client.get("/documentos").json() == []
    vacio = client.post("/chat", json={"pregunta": "inspeccion visual del cable"}).json()
    assert vacio["encontrado"] is False


def test_pregunta_vacia():
    assert client.post("/chat", json={"pregunta": "   "}).status_code == 400


def test_rechaza_no_pdf():
    r = client.post("/documentos", files={"archivo": ("a.txt", b"hola", "text/plain")})
    assert r.status_code == 400


def test_verificacion_de_citas():
    hits = [{"nombre": "Manual (Servicio)", "pagina": 7, "texto": "x"}]
    texto, fuentes = main.verificar("La alarma indica falla [1].", hits)
    assert "(Manual (Servicio), p. 7)" in texto and fuentes[0]["pagina"] == 7
    assert main.verificar("Dato inventado [5].", hits) is None
    assert main.verificar("Dato sin cita.", hits) is None
