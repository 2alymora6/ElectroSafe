# ElectroSafe

Plataforma de gestión, mantenimiento y asistencia técnica del electrobisturí Valleylab Force FX-C.

- `src/` – página web (React + Vite). Se publica gratis en **GitHub Pages**.
- `backend/` – asistente de IA (Python + FastAPI). Se publica gratis en **Render**.

La página sola funciona, pero el **Asistente IA y los Documentos indexados necesitan el backend encendido**.

---

## A. Probarlo en tu computador (primero haz esto)

Necesitas instalado: Node.js 22, pnpm y Python 3.11 o superior.

**Terminal 1 – backend**
```
cd backend
python -m venv .venv
.venv\Scripts\activate          (Mac/Linux: source .venv/bin/activate)
pip install -r requirements.txt
copy .env.example .env          (Mac/Linux: cp .env.example .env)
```
Abre `backend/.env` y pega tu clave en `ANTHROPIC_API_KEY=`. Luego:
```
uvicorn main:app --reload --port 8000
```
Comprueba: abre http://localhost:8000/salud → debe decir `"estado":"ok"`.

**Terminal 2 – página web** (en la carpeta principal)
```
pnpm install
copy .env.example .env          (Mac/Linux: cp .env.example .env)
pnpm dev
```
Abre la dirección que aparece (normalmente http://localhost:5173).

**Prueba:** Documentos → «Documentos indexados» → sube un PDF con texto → ve a Asistente IA y pregunta algo que esté en ese PDF. Si preguntas algo que no está, debe responder «No encuentro esa información en la documentación cargada».

---

## B. Publicarlo en internet

1. **Repositorio nuevo en GitHub** (no uses el anterior). Sube todo el contenido de esta carpeta, incluida `.github/`.
   Con Git:
   ```
   git init
   git add .
   git commit -m "ElectroSafe"
   git branch -M main
   git remote add origin https://github.com/<usuario>/<repositorio>.git
   git push -u origin main
   ```
2. **Backend en Render**: Render → New → Blueprint → elige el repositorio (lee `render.yaml`). Cuando pida variables:
   - `ANTHROPIC_API_KEY` = tu clave
   - `ALLOWED_ORIGINS` = `https://<usuario>.github.io` (solo eso: sin barra final ni nombre del repositorio)
   Al terminar, Render te da una URL tipo `https://electrosafe-backend.onrender.com`. Comprueba que `/salud` responde.
3. **GitHub → Settings → Pages → Source: GitHub Actions.**
4. **GitHub → Settings → Secrets and variables → Actions → pestaña Variables → New repository variable**: nombre `VITE_API_URL`, valor = la URL de Render (sin barra final).
5. En **Actions → Deploy a GitHub Pages → Run workflow** (o haz un push). La página queda en `https://<usuario>.github.io/<repositorio>/`.

---

## Limitaciones actuales
- Hoja de vida, inventario, mantenimientos, checklists y resultados se guardan en el navegador de cada dispositivo (`localStorage`). Un QR escaneado en otro celular mostrará esos datos vacíos. Compartirlos requiere llevar esas tablas al backend.
- Render gratis se duerme tras unos minutos sin uso y, al reiniciar, borra la base de datos y los PDF subidos. Antes de una demostración abre `/salud` para despertarlo y vuelve a subir los PDF.
- Los roles son solo vistas de demostración, no hay login real.
