"""ElectroSafe - backend del asistente de IA (RAG).

Flujo: PDF -> texto por pagina -> fragmentos -> indice BM25 -> recuperacion
-> modelo de lenguaje (solo con los fragmentos) -> verificacion de citas.
"""
import json
import math
import os
import re
import sqlite3
import unicodedata
from collections import Counter
from contextlib import contextmanager
from datetime import datetime, timezone

import fitz  # PyMuPDF
from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from pydantic import BaseModel

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "electrosafe.db")
LLM_MODEL = os.getenv("LLM_MODEL", "claude-sonnet-5-5")
ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv(
        "ALLOWED_ORIGINS", "http://localhost:5173,http://localhost:4173"
    ).split(",")
    if o.strip()
]
TOP_K = int(os.getenv("TOP_K", "5"))
MIN_COVERAGE = float(os.getenv("MIN_COVERAGE", "0.4"))
MAX_UPLOAD_MB = int(os.getenv("MAX_UPLOAD_MB", "40"))
CHUNK_WORDS = 300
CHUNK_OVERLAP = 50
K1, B = 1.5, 0.75

NO_ENCONTRADO = "No encuentro esa información en la documentación cargada"
SYSTEM_PROMPT = (
    "Eres el asistente técnico de ElectroSafe para el electrobisturí Valleylab Force FX. "
    "Responde solo con los fragmentos de documentación que se te entregan. "
    "No uses conocimiento externo. Cita cada afirmación con el número del fragmento "
    "entre corchetes, por ejemplo [1] o [2]. "
    f"Si los fragmentos no contienen la respuesta, responde exactamente: '{NO_ENCONTRADO}'. "
    "No inventes valores, normas, códigos de error ni procedimientos. "
    "Si la pregunta es ambigua, pide aclaración. Responde en español, de forma breve y técnica."
)

# ---------------------------------------------------------------- base de datos
SCHEMA = """
CREATE TABLE IF NOT EXISTS documentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    nombre TEXT NOT NULL,
    tipo TEXT,
    fuente_url TEXT,
    fecha TEXT,
    paginas INTEGER,
    paginas_sin_texto INTEGER,
    creado TEXT
);
CREATE TABLE IF NOT EXISTS fragmentos (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    documento_id INTEGER NOT NULL REFERENCES documentos(id) ON DELETE CASCADE,
    pagina INTEGER NOT NULL,
    texto TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS consultas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    fecha TEXT,
    pregunta TEXT,
    respuesta TEXT,
    fuentes TEXT,
    encontrado INTEGER,
    modelo_usado INTEGER,
    error TEXT
);
"""


@contextmanager
def conn_ctx():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    with conn_ctx() as c:
        c.executescript(SCHEMA)


init_db()


def ahora():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------------ texto / BM25
STOPWORDS = set(
    """a al algo ante con contra cual cuales cuando de del desde donde el ella ellos en entre
    era es esa ese eso esta este esto estos fue ha han hay la las le les lo los me mi mis mucho
    muy no nos o para pero por porque que quien se si sin sobre su sus te tiene tienen todo tu
    un una uno unos y ya como cual cuanto cuantos hace hacer puede pueden debe deben son ser
    sea esta estan estar""".split()
)


def normalizar(texto: str) -> str:
    t = unicodedata.normalize("NFD", texto.lower())
    return "".join(c for c in t if unicodedata.category(c) != "Mn")


def tokens(texto: str) -> list[str]:
    return [
        w
        for w in re.findall(r"[a-z0-9]+", normalizar(texto))
        if len(w) > 1 and w not in STOPWORDS
    ]


class Index:
    def __init__(self, rows: list[dict]):
        self.rows = rows
        self.docs = [tokens(r["texto"]) for r in rows]
        self.tf = [Counter(d) for d in self.docs]
        self.n = len(rows)
        total = sum(len(d) for d in self.docs)
        self.avg = (total / self.n) if self.n and total else 1.0
        self.df: Counter = Counter()
        for d in self.docs:
            for w in set(d):
                self.df[w] += 1

    def search(self, query: str, k: int = TOP_K) -> list[dict]:
        q = list(dict.fromkeys(tokens(query)))
        if not q or not self.n:
            return []
        scored = []
        for i, tf in enumerate(self.tf):
            matched = [w for w in q if w in tf]
            if not matched:
                continue
            dl = len(self.docs[i])
            score = 0.0
            for w in matched:
                idf = math.log(1 + (self.n - self.df[w] + 0.5) / (self.df[w] + 0.5))
                f = tf[w]
                score += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * dl / self.avg))
            scored.append((score, len(matched) / len(q), i))
        scored.sort(reverse=True)
        return [
            {**self.rows[i], "score": round(s, 3), "cobertura": round(c, 2)}
            for s, c, i in scored[:k]
            if c >= MIN_COVERAGE
        ]


_index: Index | None = None


def invalidar_indice():
    global _index
    _index = None


def get_index() -> Index:
    global _index
    if _index is None:
        with conn_ctx() as c:
            rows = [
                dict(r)
                for r in c.execute(
                    "SELECT f.id, f.pagina, f.texto, d.nombre "
                    "FROM fragmentos f JOIN documentos d ON d.id = f.documento_id"
                )
            ]
        _index = Index(rows)
    return _index


# --------------------------------------------------------------------------- PDF
def extraer_paginas(data: bytes) -> list[tuple[int, str]]:
    paginas = []
    with fitz.open(stream=data, filetype="pdf") as pdf:
        for n, page in enumerate(pdf, start=1):
            paginas.append((n, page.get_text("text").strip()))
    return paginas


def dividir(texto: str, size: int = CHUNK_WORDS, overlap: int = CHUNK_OVERLAP) -> list[str]:
    words = texto.split()
    out, i = [], 0
    while i < len(words):
        out.append(" ".join(words[i : i + size]))
        if i + size >= len(words):
            break
        i += size - overlap
    return out


# --------------------------------------------------------------------------- API
app = FastAPI(title="ElectroSafe RAG")
app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/salud")
def salud():
    with conn_ctx() as c:
        docs = c.execute("SELECT COUNT(*) FROM documentos").fetchone()[0]
        frags = c.execute("SELECT COUNT(*) FROM fragmentos").fetchone()[0]
    return {
        "estado": "ok",
        "documentos": docs,
        "fragmentos": frags,
        "modelo_configurado": bool(os.getenv("ANTHROPIC_API_KEY")),
    }


@app.post("/documentos", status_code=201)
async def subir_documento(
    archivo: UploadFile = File(...),
    nombre: str = Form(""),
    tipo: str = Form("Manual"),
    fuente_url: str = Form(""),
    fecha: str = Form(""),
):
    if not (archivo.filename or "").lower().endswith(".pdf"):
        raise HTTPException(400, "Solo se aceptan archivos PDF.")
    data = await archivo.read()
    if len(data) > MAX_UPLOAD_MB * 1024 * 1024:
        raise HTTPException(413, f"El PDF supera {MAX_UPLOAD_MB} MB.")
    try:
        paginas = extraer_paginas(data)
    except Exception as e:  # PDF corrupto o protegido
        raise HTTPException(422, f"No se pudo leer el PDF: {e}")

    nombre = nombre.strip() or (archivo.filename or "documento")
    sin_texto = sum(1 for _, t in paginas if not t)
    fragmentos = [(n, ch) for n, t in paginas if t for ch in dividir(t)]

    with conn_ctx() as c:
        cur = c.execute(
            "INSERT INTO documentos (nombre, tipo, fuente_url, fecha, paginas, paginas_sin_texto, creado) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (nombre, tipo, fuente_url, fecha, len(paginas), sin_texto, ahora()),
        )
        doc_id = cur.lastrowid
        c.executemany(
            "INSERT INTO fragmentos (documento_id, pagina, texto) VALUES (?, ?, ?)",
            [(doc_id, n, t) for n, t in fragmentos],
        )
    invalidar_indice()

    advertencia = None
    if not fragmentos:
        advertencia = "El PDF no tiene texto extraíble (probablemente escaneado). Aplique OCR antes de subirlo."
    elif sin_texto:
        advertencia = f"{sin_texto} página(s) sin texto extraíble (posible escaneo). Aplique OCR para incluirlas."
    return {
        "id": doc_id,
        "nombre": nombre,
        "paginas": len(paginas),
        "paginas_sin_texto": sin_texto,
        "fragmentos": len(fragmentos),
        "advertencia": advertencia,
    }


@app.get("/documentos")
def listar_documentos():
    with conn_ctx() as c:
        rows = c.execute(
            "SELECT d.id, d.nombre, d.tipo, d.fuente_url, d.fecha, d.paginas, d.paginas_sin_texto, "
            "(SELECT COUNT(*) FROM fragmentos f WHERE f.documento_id = d.id) AS fragmentos "
            "FROM documentos d ORDER BY d.id DESC"
        ).fetchall()
    return [dict(r) for r in rows]


@app.delete("/documentos/{doc_id}", status_code=204)
def eliminar_documento(doc_id: int):
    with conn_ctx() as c:
        if not c.execute("SELECT 1 FROM documentos WHERE id = ?", (doc_id,)).fetchone():
            raise HTTPException(404, "Documento no encontrado.")
        c.execute("DELETE FROM documentos WHERE id = ?", (doc_id,))
    invalidar_indice()
    return Response(status_code=204)


class Pregunta(BaseModel):
    pregunta: str


def fuente(h: dict) -> dict:
    return {"documento": h["nombre"], "pagina": h["pagina"], "fragmento": h["texto"][:600]}


def registrar(pregunta, respuesta, fuentes, encontrado, modelo_usado, aviso=None, error=None):
    with conn_ctx() as c:
        c.execute(
            "INSERT INTO consultas (fecha, pregunta, respuesta, fuentes, encontrado, modelo_usado, error) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                ahora(),
                pregunta,
                respuesta,
                json.dumps(fuentes, ensure_ascii=False),
                int(encontrado),
                int(modelo_usado),
                error,
            ),
        )
    return {
        "respuesta": respuesta,
        "fuentes": fuentes,
        "encontrado": encontrado,
        "modelo_usado": modelo_usado,
        "aviso": aviso,
    }


def preguntar_modelo(pregunta: str, hits: list[dict]) -> str:
    import anthropic

    client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    contexto = "\n\n".join(
        f"[{i}] Documento: {h['nombre']} | Página: {h['pagina']}\n{h['texto']}"
        for i, h in enumerate(hits, 1)
    )
    msg = client.messages.create(
        model=LLM_MODEL,
        max_tokens=700,
        system=SYSTEM_PROMPT,
        messages=[
            {"role": "user", "content": f"FRAGMENTOS:\n\n{contexto}\n\nPREGUNTA: {pregunta}"}
        ],
    )
    return "".join(b.text for b in msg.content if getattr(b, "type", "") == "text").strip()


def verificar(texto: str, hits: list[dict]):
    """Acepta la respuesta solo si cita fragmentos que realmente se recuperaron."""
    if not texto or NO_ENCONTRADO.lower() in texto.lower():
        return None
    citados = list(dict.fromkeys(int(n) for n in re.findall(r"\[(\d+)\]", texto)))
    validos = [n for n in citados if 1 <= n <= len(hits)]
    if not validos:
        return None

    def reemplazar(m):
        n = int(m.group(1))
        if 1 <= n <= len(hits):
            h = hits[n - 1]
            return f"({h['nombre']}, p. {h['pagina']})"
        return ""

    return re.sub(r"\[(\d+)\]", reemplazar, texto), [fuente(hits[n - 1]) for n in validos]


def respuesta_extractiva(hits: list[dict]) -> str:
    h = hits[0]
    extracto = h["texto"] if len(h["texto"]) <= 700 else h["texto"][:700] + "…"
    return f"Extracto más relevante ({h['nombre']}, p. {h['pagina']}): {extracto}"


@app.post("/chat")
def chat(body: Pregunta):
    pregunta = body.pregunta.strip()
    if not pregunta:
        raise HTTPException(400, "La pregunta está vacía.")
    hits = get_index().search(pregunta, TOP_K)
    if not hits:
        return registrar(pregunta, NO_ENCONTRADO, [], False, False)

    error = None
    if os.getenv("ANTHROPIC_API_KEY"):
        try:
            texto = preguntar_modelo(pregunta, hits)
            ver = verificar(texto, hits)
            if ver is None:
                return registrar(pregunta, NO_ENCONTRADO, [], False, True)
            return registrar(pregunta, ver[0], ver[1], True, True)
        except Exception as e:
            error = f"{type(e).__name__}: {e}"
            aviso = "El modelo de lenguaje no está disponible; se muestra el extracto más relevante sin generación."
    else:
        aviso = "ANTHROPIC_API_KEY no está configurada; se muestra el extracto más relevante sin generación."

    return registrar(
        pregunta,
        respuesta_extractiva(hits),
        [fuente(h) for h in hits[:3]],
        True,
        False,
        aviso,
        error,
    )
