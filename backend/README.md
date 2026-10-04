# ElectroSafe – backend del asistente de IA (RAG)

## Correr en local
```
cd backend
python -m venv .venv
.venv\Scripts\activate        # Windows  (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env        # Mac/Linux: cp .env.example .env
# editar .env y poner ANTHROPIC_API_KEY
uvicorn main:app --reload --port 8000
```
Comprobar: abrir http://localhost:8000/salud  → debe responder `{"estado":"ok", ...}`.

## Pruebas
```
pip install -r requirements-dev.txt
pytest
```

## Endpoints
- `GET /salud`
- `POST /documentos` (multipart: `archivo` PDF, `nombre`, `tipo`, `fuente_url`, `fecha`)
- `GET /documentos`
- `DELETE /documentos/{id}` → 204
- `POST /chat` `{ "pregunta": "..." }` → `{respuesta, fuentes[{documento, pagina, fragmento}], encontrado, modelo_usado, aviso}`

## Cómo funciona (para la sustentación)
1. **Extracción**: PyMuPDF lee el texto de cada página del PDF. Si una página no tiene texto (escaneada), se avisa: hay que aplicar OCR.
2. **Fragmentación**: cada página se divide en fragmentos de ~300 palabras con solapamiento de 50. Cada fragmento guarda documento y página.
3. **Recuperación**: índice BM25 (puntaje por palabras, sin acentos ni palabras vacías). Un fragmento solo cuenta si contiene al menos el 40 % de las palabras de la pregunta (`MIN_COVERAGE`).
4. **Generación**: los 5 mejores fragmentos se envían al modelo con una instrucción que lo obliga a responder solo con ellos y citar `[n]`. Si no hay fragmentos relevantes, el modelo ni se llama.
5. **Verificación de citas**: se acepta la respuesta solo si cita fragmentos que realmente se recuperaron; cada `[n]` se reemplaza por `(Documento, p. N)`. Si no cumple, responde "No encuentro esa información en la documentación cargada".
6. **Registro**: cada consulta queda en la tabla `consultas` (pregunta, respuesta, fuentes, error).
Sin `ANTHROPIC_API_KEY` o si el modelo falla, el sistema muestra el extracto literal más relevante (sin generación) y lo avisa.

## Despliegue (Render / Railway)
- Build: `pip install -r requirements.txt`
- Start: `uvicorn main:app --host 0.0.0.0 --port $PORT`
- Variables: `ANTHROPIC_API_KEY`, `LLM_MODEL`, `ALLOWED_ORIGINS` (poner la URL del frontend publicado).
- En planes gratuitos el disco se borra al reiniciar: se pierden SQLite y los documentos. Use un disco persistente (`DB_PATH` apuntando a él) o vuelva a subir los PDFs.
