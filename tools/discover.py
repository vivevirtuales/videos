#!/usr/bin/env python3
"""
Buscador de canales de mitología en inglés para clonar con Viral Clone.
Solo YouTube y Facebook.

Funciona en dos pasos:

  python3 tools/discover.py escanear
      Busca en YouTube, mide cada canal (seguidores, nº de vídeos, fecha del
      último, visitas medias, títulos recientes) y guarda los que pasan los
      filtros en data/candidatos.json. NO toca creators.csv.

  python3 tools/discover.py volcar [--veredictos data/veredictos.json]
      Escribe data/creators.csv con los candidatos aprobados, las páginas de
      Facebook enlazadas y las semillas de data/seeds_*.csv, agrupando
      duplicados. Con --veredictos usa la revisión título a título hecha por
      Claude; sin él, decide por mayoría de palabras clave en los títulos.

Filtros de calidad (ajustables por parámetro):
  --min-subs     20000   seguidores mínimos
  --min-videos   30      vídeos mínimos en el canal
  --max-dias     45      días máximos desde el último vídeo
  --min-visitas  5000    visitas medias mínimas en los últimos vídeos

Requisitos: pip install yt-dlp
"""

import argparse
import csv
import json
import re
import statistics
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
KEYWORDS = RAIZ / "tools" / "keywords_en.txt"
DATOS = RAIZ / "data"
SALIDA = DATOS / "creators.csv"
CANDIDATOS = DATOS / "candidatos.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Términos que señalan mitología en un título (dioses, héroes, criaturas...).
TERMINOS_MITO = [
    "mytholog", "myth", "folklore", "legend", "god of", "gods", "goddess",
    "deity", "deities", "pantheon", "underworld", "olympus", "titan",
    "zeus", "hades", "poseidon", "athena", "apollo", "artemis", "hermes",
    "aphrodite", "hera", "kronos", "cronus", "gaia", "medusa", "minotaur",
    "cerberus", "hercules", "heracles", "perseus", "achilles", "odysseus",
    "odin", "thor ", "loki", "freya", "fenrir", "jormungandr", "valkyrie",
    "ragnarok", "norse", "yggdrasil", "baldur",
    "anubis", "osiris", "horus", "isis ", "thoth", "ra the", "egyptian",
    "yokai", "kitsune", "amaterasu", "izanagi", "sun wukong", "jade emperor",
    "shiva", "vishnu", "krishna", "hanuman", "ramayana", "mahabharata",
    "quetzalcoatl", "aztec", "mayan", "celtic", "slavic", "banshee",
    "kraken", "phoenix", "griffin", "chimera", "hydra", "sphinx", "cyclops",
]

PALABRAS_EN = {"the", "and", "of", "to", "in", "is", "you", "that", "for",
               "with", "about", "from", "this", "will", "are", "we", "our",
               "how", "why", "what", "story", "history"}
PALABRAS_NO_EN = {"el", "la", "los", "las", "para", "una", "historia", "dios",
                  "dioses", "mitologia", "mitología", "canal", "vídeos",
                  "der", "die", "und", "les", "des", "мифы", "मिथक"}


def leer_keywords():
    lineas = KEYWORDS.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lineas if l.strip() and not l.startswith("#")]


def correr_ytdlp(args, timeout=120):
    cmd = ["yt-dlp", "-J", "--no-warnings"] + args
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        if r.returncode != 0 or not r.stdout.strip():
            return None
        return json.loads(r.stdout)
    except Exception:
        return None


def descargar(url, timeout=20):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except Exception:
        return ""


def parece_ingles(texto):
    t = " " + re.sub(r"[^a-záéíóúüñа-яa-z]+", " ", texto.lower()) + " "
    en = sum(1 for p in PALABRAS_EN if f" {p} " in t)
    no = sum(1 for p in PALABRAS_NO_EN if f" {p} " in t)
    if en == 0 and no == 0:
        return True
    return en >= no


def titulos_mito(titulos):
    """Cuántos títulos contienen al menos un término de mitología."""
    n = 0
    for t in titulos:
        tl = " " + t.lower() + " "
        if any(k in tl for k in TERMINOS_MITO):
            n += 1
    return n


def normalizar_nombre(nombre):
    return re.sub(r"[^a-z0-9]+", "", (nombre or "").lower())


# ---------------------------------------------------------------- Escanear --

def buscar_youtube(keywords, por_keyword):
    """{channel_id: nombre} con los canales que salen en las búsquedas."""
    canales = {}
    for i, kw in enumerate(keywords, 1):
        print(f"[busqueda] ({i}/{len(keywords)}) {kw}", flush=True)
        d = correr_ytdlp(["--flat-playlist", f"ytsearch{por_keyword}:{kw}"])
        if not d:
            continue
        for e in d.get("entries") or []:
            cid = e.get("channel_id")
            if cid and cid not in canales:
                canales[cid] = (e.get("channel") or e.get("uploader") or "").strip()
    return canales


def canales_ya_conocidos():
    """IDs de canal de YouTube que ya están en creators.csv (para re-auditarlos)."""
    ids = {}
    if not SALIDA.exists():
        return ids
    with open(SALIDA, encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r.get("plataforma") != "youtube":
                continue
            m = re.search(r"/channel/(UC[\w-]{22})", r.get("url") or "")
            if m:
                ids[m.group(1)] = r.get("nombre") or ""
    return ids


def medir_canal(cid, nombre, cfg):
    """Mide un canal y devuelve (dict, motivo_descarte). Uno de los dos es None."""
    url = f"https://www.youtube.com/channel/{cid}"
    d = correr_ytdlp(["--flat-playlist", "-I", ":31", url + "/videos"])
    if not d:
        return None, "sin_datos"
    subs = d.get("channel_follower_count") or 0
    desc = d.get("description") or ""
    entradas = d.get("entries") or []
    titulos = [(e.get("title") or "") for e in entradas]
    vistas = [e.get("view_count") for e in entradas if e.get("view_count")]

    if subs < cfg.min_subs:
        return None, f"pocos_seguidores:{subs}"
    if len(entradas) < cfg.min_videos:
        return None, f"pocos_videos:{len(entradas)}"
    visitas_media = int(statistics.median(vistas)) if vistas else 0
    if visitas_media < cfg.min_visitas:
        return None, f"pocas_visitas:{visitas_media}"
    texto = nombre + " " + desc + " " + " ".join(titulos)
    if not parece_ingles(texto):
        return None, "no_ingles"
    mito = titulos_mito(titulos)
    if mito < max(3, len(titulos) // 6):
        return None, f"sin_tema:{mito}/{len(titulos)}"

    # Fecha del último vídeo: hay que abrir el vídeo más reciente.
    ultimo = ""
    if entradas:
        v = correr_ytdlp(["--no-playlist", "--skip-download", "--ignore-no-formats-error",
                          f"https://www.youtube.com/watch?v={entradas[0]['id']}"])
        if v and v.get("upload_date"):
            ultimo = v["upload_date"]  # AAAAMMDD
    if ultimo:
        dias = (date.today() - datetime.strptime(ultimo, "%Y%m%d").date()).days
        if dias > cfg.max_dias:
            return None, f"inactivo:{dias}d"
    ultimo_fmt = f"{ultimo[:4]}-{ultimo[4:6]}-{ultimo[6:]}" if ultimo else ""

    return {
        "id": cid, "nombre": nombre or d.get("channel") or "",
        "url": url, "seguidores": subs, "videos": len(entradas),
        "ultimo_video": ultimo_fmt, "visitas_media": visitas_media,
        "titulos": titulos, "titulos_mito": f"{mito}/{len(titulos)}",
        "descripcion": desc,
    }, None


def enlaces_facebook(cid, descripcion):
    """Enlace a Facebook publicado en la página del canal o su descripción."""
    html = descargar(f"https://www.youtube.com/channel/{cid}/about")
    texto = urllib.parse.unquote(html) + " " + (descripcion or "")
    patron = r"facebook\.com/(?:profile\.php\?id=\d+|[A-Za-z0-9.\-_]{3,60})"
    for m in re.findall(patron, texto):
        u = m.rstrip("./")
        malo = any(x in u.lower() for x in (
            "sharer", "login", "plugins", "watch", "events", "groups", "pages",
            "hashtag", "reel", "photo", "story", "legal", "help", "tr?", "profile.php"))
        if not malo:
            return "https://www." + u
    return ""


def cmd_escanear(cfg):
    keywords = leer_keywords()
    canales = buscar_youtube(keywords, cfg.per_keyword)
    for cid, nombre in canales_ya_conocidos().items():
        canales.setdefault(cid, nombre)
    print(f"[escaneo] canales a medir: {len(canales)}", flush=True)

    aceptados, descartes = [], {}
    items = list(canales.items())[: cfg.max_channels]
    with ThreadPoolExecutor(cfg.hilos) as ex:
        futuros = {ex.submit(medir_canal, cid, n, cfg): cid for cid, n in items}
        for i, fu in enumerate(as_completed(futuros), 1):
            c, motivo = fu.result()
            if c:
                aceptados.append(c)
                print(f"[escaneo] ({i}/{len(items)}) PASA {c['nombre']} "
                      f"({c['seguidores']:,} subs, {c['titulos_mito']} mito)", flush=True)
            else:
                descartes[motivo] = descartes.get(motivo, 0) + 1

    print(f"[facebook] buscando enlaces en {len(aceptados)} canales", flush=True)
    with ThreadPoolExecutor(cfg.hilos) as ex:
        futuros = {ex.submit(enlaces_facebook, c["id"], c["descripcion"]): c
                   for c in aceptados}
        for fu in as_completed(futuros):
            futuros[fu]["facebook"] = fu.result()

    DATOS.mkdir(exist_ok=True)
    CANDIDATOS.write_text(json.dumps(aceptados, ensure_ascii=False, indent=1),
                          encoding="utf-8")
    resumen = ", ".join(f"{k}={v}" for k, v in sorted(descartes.items()))
    print(f"\n[escaneo] aceptados: {len(aceptados)} -> {CANDIDATOS}")
    print(f"[escaneo] descartados: {resumen}")


# ------------------------------------------------------------------ Volcar --

def leer_semillas():
    """data/seeds_*.csv: cuentas halladas por investigación web."""
    filas = []
    for f in sorted(DATOS.glob("seeds_*.csv")):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                url = (r.get("url") or "").strip()
                if not url:
                    continue
                filas.append({
                    "nombre": (r.get("nombre") or "").strip(),
                    "plataforma": (r.get("plataforma") or "facebook").strip().lower(),
                    "url": url,
                    "seguidores": int(re.sub(r"\D", "", r.get("seguidores") or "0") or 0),
                    "videos": "", "ultimo_video": (r.get("ultimo_post") or "").strip(),
                    "visitas_media": "", "titulos_mito": "",
                    "enlaces": {p: r[p].strip() for p in ("youtube", "facebook")
                                if (r.get(p) or "").strip()},
                    "fuente": f.name, "notas_semilla": (r.get("notas") or "").strip(),
                })
    return filas


def agrupar(cuentas):
    """Agrupa cuentas del mismo creador (mismo nombre o enlazadas entre sí)."""
    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            padre[ra] = rb

    def clave(c):
        return c["url"].rstrip("/").lower()

    for c in cuentas:
        padre.setdefault(clave(c), clave(c))

    por_nombre = {}
    for c in cuentas:
        n = normalizar_nombre(c["nombre"])
        if len(n) >= 5:
            if n in por_nombre:
                unir(clave(c), por_nombre[n])
            else:
                por_nombre[n] = clave(c)

    por_url = {clave(c): clave(c) for c in cuentas}
    for c in cuentas:
        for link in c.get("enlaces", {}).values():
            l = link.rstrip("/").lower()
            if l in por_url:
                unir(clave(c), l)

    grupos = {}
    for c in cuentas:
        grupos.setdefault(raiz(clave(c)), []).append(c)
    return list(grupos.values())


COLUMNAS = ["grupo", "nombre", "plataforma", "url", "seguidores", "videos",
            "ultimo_video", "visitas_media", "titulos_mito", "rol", "fuente",
            "estado", "notas"]

ESTADOS_AUTO = {"nuevo", ""}


def cmd_volcar(cfg):
    if not CANDIDATOS.exists():
        sys.exit("No hay data/candidatos.json. Ejecuta antes: discover.py escanear")
    candidatos = json.loads(CANDIDATOS.read_text(encoding="utf-8"))

    veredictos = {}
    if cfg.veredictos:
        veredictos = {v["id"]: v for v in
                      json.loads(Path(cfg.veredictos).read_text(encoding="utf-8"))}

    cuentas = []
    for c in candidatos:
        if veredictos:
            v = veredictos.get(c["id"])
            if not v or not v.get("es_mitologia") or not v.get("en_ingles", True):
                continue
        else:
            hechos, total = map(int, c["titulos_mito"].split("/"))
            if total == 0 or hechos / total < 0.4:
                continue
        fila = dict(c, plataforma="youtube", fuente="descubrimiento_auto",
                    enlaces={})
        if c.get("facebook"):
            fila["enlaces"] = {"facebook": c["facebook"]}
            cuentas.append({
                "nombre": c["nombre"], "plataforma": "facebook",
                "url": c["facebook"], "seguidores": 0, "videos": "",
                "ultimo_video": "", "visitas_media": "", "titulos_mito": "",
                "enlaces": {}, "fuente": "enlace_desde_youtube",
            })
        cuentas.append(fila)

    cuentas.extend(leer_semillas())

    # Sin duplicados exactos de URL.
    unicos, vistos = [], set()
    for c in cuentas:
        k = c["url"].rstrip("/").lower()
        if k not in vistos:
            vistos.add(k)
            unicos.append(c)

    previas = {}
    if SALIDA.exists():
        with open(SALIDA, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                previas[r["url"].rstrip("/").lower()] = r

    filas = []
    for g in agrupar(unicos):
        g.sort(key=lambda c: -(c["seguidores"] or 0))
        principal = g[0]
        gid = normalizar_nombre(principal["nombre"]) or principal["url"]
        for c in g:
            vieja = previas.pop(c["url"].rstrip("/").lower(), None)
            filas.append({
                "grupo": gid, "nombre": c["nombre"], "plataforma": c["plataforma"],
                "url": c["url"], "seguidores": c["seguidores"],
                "videos": c.get("videos", ""),
                "ultimo_video": c.get("ultimo_video", ""),
                "visitas_media": c.get("visitas_media", ""),
                "titulos_mito": c.get("titulos_mito", ""),
                "rol": "principal" if c is principal else
                       f"duplicado_de:{principal['plataforma']}",
                "fuente": c.get("fuente", "descubrimiento_auto"),
                "estado": (vieja["estado"] if vieja else "nuevo") or "nuevo",
                "notas": (vieja["notas"] if vieja else c.get("notas_semilla", "")),
            })

    # Filas antiguas con estado puesto a mano se conservan siempre
    # (menos TikTok, que queda fuera del sistema).
    for r in previas.values():
        if r.get("plataforma") != "tiktok" and r.get("estado") not in ESTADOS_AUTO:
            filas.append({col: r.get(col, "") for col in COLUMNAS})

    # Orden: creadores más fuertes primero (visitas medias del principal).
    fuerza = {}
    for f in filas:
        if f["rol"] == "principal":
            try:
                fuerza[f["grupo"]] = int(f["visitas_media"] or 0)
            except ValueError:
                fuerza[f["grupo"]] = 0
    filas.sort(key=lambda f: (-fuerza.get(f["grupo"], 0), f["grupo"],
                              f["rol"] != "principal"))

    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)
    n_grupos = len({f["grupo"] for f in filas})
    print(f"Hecho: {len(filas)} cuentas, {n_grupos} creadores -> {SALIDA}")


# -------------------------------------------------------------------- Main --

def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="orden", required=True)

    e = sub.add_parser("escanear", help="busca y mide canales de YouTube")
    e.add_argument("--per-keyword", type=int, default=20)
    e.add_argument("--max-channels", type=int, default=150)
    e.add_argument("--min-subs", type=int, default=20000)
    e.add_argument("--min-videos", type=int, default=30)
    e.add_argument("--max-dias", type=int, default=45)
    e.add_argument("--min-visitas", type=int, default=5000)
    e.add_argument("--hilos", type=int, default=6)

    v = sub.add_parser("volcar", help="escribe data/creators.csv")
    v.add_argument("--veredictos", help="JSON con la revisión título a título")

    cfg = ap.parse_args()
    if cfg.orden == "escanear":
        cmd_escanear(cfg)
    else:
        cmd_volcar(cfg)


if __name__ == "__main__":
    sys.exit(main())
