"""MCP server for Google Classroom (student view; read-only plus Drive upload).

Run `classroom-mcp-auth` once beforehand to generate token.json.
Tool names and outputs are in Spanish, matching the original Spanish-language setup.
"""

import io
import mimetypes
import os
import re
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload, MediaIoBaseDownload
from mcp.server.fastmcp import FastMCP


def home_dir() -> Path:
    """Folder holding credentials.json and token.json (CLASSROOM_MCP_HOME, default: cwd)."""
    return Path(os.environ.get("CLASSROOM_MCP_HOME") or Path.cwd())


def token_path() -> Path:
    return home_dir() / "token.json"


def credentials_path() -> Path:
    return home_dir() / "credentials.json"


def download_dir() -> Path:
    env = os.environ.get("CLASSROOM_DOWNLOAD_DIR")
    return Path(env) if env else home_dir() / "downloads"


SCOPES = [
    "https://www.googleapis.com/auth/classroom.courses.readonly",
    "https://www.googleapis.com/auth/classroom.coursework.me.readonly",
    "https://www.googleapis.com/auth/classroom.courseworkmaterials.readonly",
    "https://www.googleapis.com/auth/classroom.announcements.readonly",
    "https://www.googleapis.com/auth/drive.readonly",
    # Solo los archivos que crea esta app (para subir entregas)
    "https://www.googleapis.com/auth/drive.file",
]
DRIVE_FOLDER = os.environ.get("CLASSROOM_DRIVE_FOLDER", "Entregas Classroom")

# Exportación de archivos nativos de Google al descargar
EXPORT_MIME = {
    "application/vnd.google-apps.document": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.presentation": ("application/pdf", ".pdf"),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.drawing": ("application/pdf", ".pdf"),
}

mcp = FastMCP("classroom")


def _creds() -> Credentials:
    token = token_path()
    if not token.exists():
        raise RuntimeError(
            f"Falta token.json en {token.parent}. Ejecuta primero: classroom-mcp-auth"
        )
    # Usa los scopes que Google concedió realmente (pueden venir con alias)
    creds = Credentials.from_authorized_user_file(str(token))
    if not creds.valid:
        if creds.expired and creds.refresh_token:
            creds.refresh(Request())
            token.write_text(creds.to_json(), encoding="utf-8")
        else:
            raise RuntimeError("Token no válido. Vuelve a ejecutar: classroom-mcp-auth")
    return creds


def _classroom():
    return build("classroom", "v1", credentials=_creds(), cache_discovery=False)


def _drive():
    return build("drive", "v3", credentials=_creds(), cache_discovery=False)


def _paginate(request_fn, key: str, limit: int):
    items, token = [], None
    while len(items) < limit:
        resp = request_fn(token).execute()
        items.extend(resp.get(key, []))
        token = resp.get("nextPageToken")
        if not token:
            break
    return items[:limit]


def _materials(materials: list) -> list:
    out = []
    for m in materials or []:
        if "driveFile" in m:
            f = m["driveFile"]["driveFile"]
            out.append(
                {
                    "tipo": "drive",
                    "id": f.get("id"),
                    "titulo": f.get("title"),
                    "url": f.get("alternateLink"),
                }
            )
        elif "link" in m:
            out.append(
                {"tipo": "enlace", "titulo": m["link"].get("title"), "url": m["link"].get("url")}
            )
        elif "youtubeVideo" in m:
            out.append(
                {
                    "tipo": "youtube",
                    "titulo": m["youtubeVideo"].get("title"),
                    "url": m["youtubeVideo"].get("alternateLink"),
                }
            )
        elif "form" in m:
            out.append(
                {
                    "tipo": "formulario",
                    "titulo": m["form"].get("title"),
                    "url": m["form"].get("formUrl"),
                }
            )
    return out


def _due(cw: dict):
    d = cw.get("dueDate")
    if not d:
        return None
    t = cw.get("dueTime", {})
    day = f"{d['year']:04d}-{d['month']:02d}-{d['day']:02d}"
    return f"{day} {t.get('hours', 23):02d}:{t.get('minutes', 59):02d} UTC"


@mcp.tool()
def listar_cursos(incluir_archivados: bool = False) -> list:
    """Lista los cursos de Classroom en los que estás matriculado."""
    states = ["ACTIVE", "ARCHIVED"] if incluir_archivados else ["ACTIVE"]
    svc = _classroom()
    cursos = _paginate(
        lambda t: svc.courses().list(studentId="me", courseStates=states, pageToken=t),
        "courses",
        200,
    )
    return [
        {
            "id": c["id"],
            "nombre": c.get("name"),
            "seccion": c.get("section"),
            "estado": c.get("courseState"),
            "url": c.get("alternateLink"),
        }
        for c in cursos
    ]


@mcp.tool()
def listar_tareas(curso_id: str, limite: int = 30) -> list:
    """Tareas (coursework) de un curso, de más reciente a más antigua, con entrega y adjuntos."""
    svc = _classroom()
    tareas = _paginate(
        lambda t: (
            svc.courses()
            .courseWork()
            .list(courseId=curso_id, orderBy="updateTime desc", pageToken=t)
        ),
        "courseWork",
        limite,
    )
    return [
        {
            "id": w["id"],
            "titulo": w.get("title"),
            "descripcion": w.get("description"),
            "tipo": w.get("workType"),
            "entrega": _due(w),
            "puntos": w.get("maxPoints"),
            "tema_id": w.get("topicId"),
            "url": w.get("alternateLink"),
            "adjuntos": _materials(w.get("materials")),
        }
        for w in tareas
    ]


@mcp.tool()
def mis_entregas(curso_id: str) -> list:
    """Estado de tus entregas en un curso (pendiente, entregada, devuelta, nota...)."""
    svc = _classroom()
    subs = _paginate(
        lambda t: (
            svc.courses()
            .courseWork()
            .studentSubmissions()
            .list(courseId=curso_id, courseWorkId="-", userId="me", pageToken=t)
        ),
        "studentSubmissions",
        500,
    )
    return [
        {
            "tarea_id": s.get("courseWorkId"),
            "estado": s.get("state"),
            "tarde": s.get("late", False),
            "nota": s.get("assignedGrade"),
            "url": s.get("alternateLink"),
        }
        for s in subs
    ]


@mcp.tool()
def listar_materiales(curso_id: str, limite: int = 50) -> list:
    """Materiales publicados en un curso (apuntes, presentaciones...) con sus archivos adjuntos."""
    svc = _classroom()
    mats = _paginate(
        lambda t: (
            svc.courses()
            .courseWorkMaterials()
            .list(courseId=curso_id, orderBy="updateTime desc", pageToken=t)
        ),
        "courseWorkMaterial",
        limite,
    )
    return [
        {
            "id": m["id"],
            "titulo": m.get("title"),
            "descripcion": m.get("description"),
            "tema_id": m.get("topicId"),
            "actualizado": m.get("updateTime"),
            "url": m.get("alternateLink"),
            "adjuntos": _materials(m.get("materials")),
        }
        for m in mats
    ]


@mcp.tool()
def listar_temas(curso_id: str) -> list:
    """Temas (topics) de un curso, para agrupar tareas y materiales."""
    svc = _classroom()
    temas = _paginate(
        lambda t: svc.courses().topics().list(courseId=curso_id, pageToken=t), "topic", 200
    )
    return [{"id": t["topicId"], "nombre": t.get("name")} for t in temas]


@mcp.tool()
def listar_anuncios(curso_id: str, limite: int = 20) -> list:
    """Anuncios del tablón de un curso, del más reciente al más antiguo."""
    svc = _classroom()
    anuncios = _paginate(
        lambda t: (
            svc.courses()
            .announcements()
            .list(courseId=curso_id, orderBy="updateTime desc", pageToken=t)
        ),
        "announcements",
        limite,
    )
    return [
        {
            "id": a["id"],
            "texto": a.get("text"),
            "fecha": a.get("updateTime"),
            "url": a.get("alternateLink"),
            "adjuntos": _materials(a.get("materials")),
        }
        for a in anuncios
    ]


@mcp.tool()
def descargar_archivo(drive_file_id: str, carpeta_destino: str = "") -> str:
    """Descarga un adjunto de Drive a tu PC. Los Docs/Slides de Google se exportan a PDF.

    carpeta_destino: ruta absoluta de una carpeta existente o nueva en tu equipo.
    Si se deja vacía, usa CLASSROOM_DOWNLOAD_DIR (o ./downloads).
    """
    drive = _drive()
    meta = drive.files().get(fileId=drive_file_id, fields="name,mimeType").execute()
    name, mime = meta["name"], meta["mimeType"]
    if mime in EXPORT_MIME:
        export_mime, ext = EXPORT_MIME[mime]
        request = drive.files().export_media(fileId=drive_file_id, mimeType=export_mime)
        if not name.lower().endswith(ext):
            name += ext
    elif mime.startswith("application/vnd.google-apps"):
        return f"'{name}' es de tipo {mime} y no se puede descargar."
    else:
        request = drive.files().get_media(fileId=drive_file_id)

    dest_dir = Path(carpeta_destino) if carpeta_destino else download_dir()
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / re.sub(r'[<>:"/\\|?*]', "_", name)

    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    dest.write_bytes(buf.getvalue())
    return f"Guardado en {dest}"


def _carpeta_entregas(drive) -> str:
    q = (
        f"name = '{DRIVE_FOLDER}' and mimeType = 'application/vnd.google-apps.folder' "
        "and trashed = false"
    )
    found = drive.files().list(q=q, fields="files(id)", spaces="drive").execute().get("files", [])
    if found:
        return found[0]["id"]
    return (
        drive.files()
        .create(
            body={"name": DRIVE_FOLDER, "mimeType": "application/vnd.google-apps.folder"},
            fields="id",
        )
        .execute()["id"]
    )


@mcp.tool()
def preparar_entrega(ruta_archivo: str, curso_id: str = "", tarea_id: str = "") -> dict:
    """Sube un archivo local a tu Drive (carpeta "Entregas Classroom") para entregarlo en una tarea.

    La API de Google NO permite adjuntar ni entregar en tareas creadas por el profesor,
    así que la entrega final se hace a mano: abrir la tarea → Añadir → Google Drive → Entregar.
    Si pasas curso_id y tarea_id, devuelve también el enlace directo a la tarea y su estado.
    """
    path = Path(ruta_archivo)
    if not path.is_file():
        raise RuntimeError(f"No existe el archivo: {path}")
    drive = _drive()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    f = (
        drive.files()
        .create(
            body={"name": path.name, "parents": [_carpeta_entregas(drive)]},
            media_body=MediaFileUpload(str(path), mimetype=mime, resumable=True),
            fields="id,name,webViewLink",
        )
        .execute()
    )
    out = {
        "archivo": f["name"],
        "drive_id": f["id"],
        "drive_url": f["webViewLink"],
        "siguiente_paso": "Abre la tarea → Añadir → Google Drive → carpeta "
        f"'{DRIVE_FOLDER}' → elige el archivo → Entregar",
    }
    if curso_id and tarea_id:
        svc = _classroom()
        tarea = svc.courses().courseWork().get(courseId=curso_id, id=tarea_id).execute()
        subs = (
            svc.courses()
            .courseWork()
            .studentSubmissions()
            .list(courseId=curso_id, courseWorkId=tarea_id, userId="me")
            .execute()
            .get("studentSubmissions", [])
        )
        out.update(
            {
                "tarea": tarea.get("title"),
                "entrega_limite": _due(tarea),
                "tarea_url": tarea.get("alternateLink"),
                "estado_actual": subs[0].get("state") if subs else None,
            }
        )
    return out


def main() -> None:
    mcp.run()  # stdio


if __name__ == "__main__":
    main()
