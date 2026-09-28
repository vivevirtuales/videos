#!/usr/bin/env python3
"""
Descubridor de canales de mitología (en inglés) para clonar contenido.

Qué hace:
  1. Busca en YouTube con las palabras clave de tools/keywords_en.txt (sin clave de API).
  2. Recoge los canales que aparecen, sus suscriptores y su descripción.
  3. Filtra: solo canales en inglés y con temática de mitología.
  4. Busca los enlaces a Facebook / TikTok / Instagram que cada canal publica
     en su página (sirven para detectar al mismo creador en varias plataformas).
  5. Verifica perfiles de TikTok (existen y cuántos seguidores tienen).
  6. Agrupa las cuentas que son el mismo creador (mismo nombre o enlazadas
     entre sí) y marca cuál es la principal para no clonar contenido duplicado.
  7. Mezcla también las semillas manuales de data/seeds_*.csv (hallazgos de
     investigación web, p. ej. páginas de Facebook que no se pueden rastrear
     directamente).
  8. Escribe/actualiza data/creators.csv sin pisar el trabajo manual:
     las filas ya existentes conservan su columna "estado" y "notas".

Uso:
  python3 tools/discover.py                  # ejecución completa
  python3 tools/discover.py --max-channels 40 --per-keyword 10   # más rápido

Requisitos: pip install yt-dlp
"""

import argparse
import csv
import json
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
KEYWORDS = RAIZ / "tools" / "keywords_en.txt"
DATOS = RAIZ / "data"
SALIDA = DATOS / "creators.csv"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

# Términos que confirman que el canal va de mitología.
TERMINOS_MITO = [
    "mythology", "myth", "myths", "folklore", "legend", "legends", "gods",
    "goddess", "zeus", "odin", "thor", "loki", "hades", "poseidon", "athena",
    "anubis", "osiris", "ragnarok", "olympus", "titan", "titans", "norse",
    "pantheon", "deities", "epic tales", "ancient tales",
]

# Palabras muy frecuentes en inglés: si aparecen varias, el texto es inglés.
PALABRAS_EN = {"the", "and", "of", "to", "in", "is", "you", "that", "for",
               "with", "about", "from", "this", "will", "are", "we", "our"}
# Señales de otros idiomas frecuentes en este nicho.
PALABRAS_NO_EN = {"el", "la", "los", "las", "para", "una", "historia", "dios",
                  "dioses", "mitologia", "mitología", "canal", "vídeos",
                  "videos de", "der", "die", "und", "les", "des", "мифы"}


def leer_keywords():
    lineas = KEYWORDS.read_text(encoding="utf-8").splitlines()
    return [l.strip() for l in lineas if l.strip() and not l.startswith("#")]


def correr_ytdlp(args, timeout=90):
    """Ejecuta yt-dlp y devuelve el JSON parseado, o None si falla."""
    cmd = ["yt-dlp", "-J", "--flat-playlist", "--no-warnings"] + args
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
    t = " " + re.sub(r"[^a-záéíóúüñа-я]+", " ", texto.lower()) + " "
    en = sum(1 for p in PALABRAS_EN if f" {p} " in t)
    no = sum(1 for p in PALABRAS_NO_EN if f" {p} " in t)
    if en == 0 and no == 0:
        return True  # sin datos: no descartamos
    return en >= no


def es_mitologia(texto):
    t = texto.lower()
    return sum(1 for k in TERMINOS_MITO if k in t)


def normalizar_nombre(nombre):
    n = re.sub(r"[^a-z0-9]+", "", (nombre or "").lower())
    return n


# ---------------------------------------------------------------- YouTube --

def buscar_youtube(keywords, por_keyword):
    """Devuelve {channel_id: info} con los canales que salen en las búsquedas."""
    canales = {}
    for i, kw in enumerate(keywords, 1):
        print(f"[YT] ({i}/{len(keywords)}) buscando: {kw}")
        d = correr_ytdlp([f"ytsearch{por_keyword}:{kw}"])
        if not d:
            continue
        for e in d.get("entries") or []:
            cid = e.get("channel_id")
            if not cid:
                continue
            c = canales.setdefault(cid, {
                "nombre": (e.get("channel") or e.get("uploader") or "").strip(),
                "apariciones": 0, "vistas": 0, "titulos": []})
            c["apariciones"] += 1
            c["vistas"] += e.get("view_count") or 0
            if len(c["titulos"]) < 5:
                c["titulos"].append(e.get("title") or "")
    return canales


def enlaces_externos_youtube(channel_id, descripcion):
    """Extrae enlaces a otras plataformas desde la página del canal y su bio."""
    html = descargar(f"https://www.youtube.com/channel/{channel_id}/about")
    texto = urllib.parse.unquote(html) + " " + (descripcion or "")
    enlaces = {}
    for plat, patron in [
        ("facebook", r"facebook\.com/(?:profile\.php\?id=\d+|[A-Za-z0-9.\-_]{3,60})"),
        ("tiktok", r"tiktok\.com/@[A-Za-z0-9._]{2,24}"),
        ("instagram", r"instagram\.com/[A-Za-z0-9._]{2,30}"),
    ]:
        vistos = []
        for m in re.findall(patron, texto):
            u = m.rstrip("./")
            malo = any(x in u.lower() for x in (
                "sharer", "login", "plugins", "watch", "events", "groups",
                "hashtag", "reel", "photo", "story", "legal", "help", "tr?"))
            if not malo and u not in vistos:
                vistos.append(u)
        if vistos:
            enlaces[plat] = "https://www." + vistos[0]
    return enlaces


def meta_canal_youtube(cid, base):
    d = correr_ytdlp(["-I", ":0", f"https://www.youtube.com/channel/{cid}"])
    subs, desc = 0, ""
    if d:
        subs = d.get("channel_follower_count") or 0
        desc = d.get("description") or ""
    enlaces = enlaces_externos_youtube(cid, desc)
    return {
        "id": cid, "nombre": base["nombre"], "plataforma": "youtube",
        "url": f"https://www.youtube.com/channel/{cid}",
        "seguidores": subs, "descripcion": desc,
        "apariciones": base["apariciones"], "vistas": base["vistas"],
        "titulos": base["titulos"], "enlaces": enlaces,
    }


# ----------------------------------------------------------------- TikTok --

def verificar_tiktok(handle):
    """Comprueba un @usuario de TikTok. Devuelve info o None si no existe."""
    handle = handle.lstrip("@")
    html = descargar(f"https://www.tiktok.com/@{handle}")
    m = re.search(r'"uniqueId":"([^"]+)"', html)
    if not m or m.group(1).lower() != handle.lower():
        return None
    segs = re.search(r'"followerCount":(\d+)', html)
    nombre = re.search(r'"nickname":"([^"]*)"', html)
    bio = re.search(r'"signature":"([^"]*)"', html)
    bio_txt = (bio.group(1) if bio else "").encode().decode("unicode_escape", "ignore")
    enlaces = {}
    fb = re.search(r"facebook\.com/[A-Za-z0-9.\-_]{3,60}", bio_txt)
    yt = re.search(r"(?:youtube\.com/(?:@|channel/|c/)[A-Za-z0-9.\-_]+)", bio_txt)
    if fb:
        enlaces["facebook"] = "https://www." + fb.group(0)
    if yt:
        enlaces["youtube"] = "https://www." + yt.group(0)
    return {
        "id": "tt:" + handle.lower(), "nombre": (nombre.group(1) if nombre else handle),
        "plataforma": "tiktok", "url": f"https://www.tiktok.com/@{handle}",
        "seguidores": int(segs.group(1)) if segs else 0,
        "descripcion": bio_txt, "enlaces": enlaces,
    }


def candidatos_tiktok(cuentas_youtube):
    """Handles de TikTok a comprobar: los enlazados y los adivinados por nombre."""
    handles = {}
    for c in cuentas_youtube:
        link = c["enlaces"].get("tiktok")
        if link:
            handles[link.split("@")[-1].lower()] = c["id"]
        else:
            n = normalizar_nombre(c["nombre"])
            if 4 <= len(n) <= 24:
                handles.setdefault(n, None)
    return handles


# ---------------------------------------------------------------- Semillas --

def leer_semillas():
    """Lee data/seeds_*.csv: cuentas halladas por investigación web manual."""
    filas = []
    for f in sorted(DATOS.glob("seeds_*.csv")):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not (r.get("url") or "").strip():
                    continue
                filas.append({
                    "id": r["url"].strip().rstrip("/").lower(),
                    "nombre": (r.get("nombre") or "").strip(),
                    "plataforma": (r.get("plataforma") or "").strip().lower(),
                    "url": r["url"].strip(),
                    "seguidores": int(re.sub(r"\D", "", r.get("seguidores") or "0") or 0),
                    "descripcion": r.get("notas") or "",
                    "enlaces": {p: r[p] for p in ("youtube", "facebook", "tiktok")
                                if r.get(p, "").strip()},
                    "fuente": f.name,
                })
    return filas


# ------------------------------------------------------------- Duplicados --

def agrupar(cuentas):
    """Agrupa cuentas que son el mismo creador (por nombre o por enlaces)."""
    padre = {}

    def raiz(x):
        while padre.get(x, x) != x:
            x = padre[x]
        return x

    def unir(a, b):
        ra, rb = raiz(a), raiz(b)
        if ra != rb:
            padre[ra] = rb

    for c in cuentas:
        padre.setdefault(c["id"], c["id"])

    # 1) mismo nombre normalizado
    por_nombre = {}
    for c in cuentas:
        n = normalizar_nombre(c["nombre"])
        if len(n) >= 5:
            if n in por_nombre:
                unir(c["id"], por_nombre[n])
            else:
                por_nombre[n] = c["id"]

    # 2) enlaces cruzados (el canal enlaza a la otra cuenta)
    por_url = {c["url"].rstrip("/").lower(): c["id"] for c in cuentas}
    por_handle = {}
    for c in cuentas:
        h = c["url"].rstrip("/").split("/")[-1].lstrip("@").lower()
        por_handle[(c["plataforma"], h)] = c["id"]
    for c in cuentas:
        for plat, link in c.get("enlaces", {}).items():
            l = link.rstrip("/").lower()
            if l in por_url:
                unir(c["id"], por_url[l])
            else:
                h = l.split("/")[-1].lstrip("@")
                if (plat, h) in por_handle:
                    unir(c["id"], por_handle[(plat, h)])

    grupos = {}
    for c in cuentas:
        grupos.setdefault(raiz(c["id"]), []).append(c)
    return list(grupos.values())


# ----------------------------------------------------------------- Salida --

COLUMNAS = ["grupo", "nombre", "plataforma", "url", "seguidores", "rol",
            "senales_mito", "fuente", "estado", "notas"]


def escribir_salida(grupos):
    previas = {}
    if SALIDA.exists():
        with open(SALIDA, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                previas[r["url"].rstrip("/").lower()] = r

    filas = []
    for g in grupos:
        g.sort(key=lambda c: -c["seguidores"])
        principal = g[0]
        gid = normalizar_nombre(principal["nombre"]) or principal["id"]
        for c in g:
            clave = c["url"].rstrip("/").lower()
            vieja = previas.pop(clave, None)
            filas.append({
                "grupo": gid,
                "nombre": c["nombre"],
                "plataforma": c["plataforma"],
                "url": c["url"],
                "seguidores": c["seguidores"],
                "rol": "principal" if c is principal else
                       f"duplicado_de:{principal['plataforma']}",
                "senales_mito": es_mitologia(
                    c["nombre"] + " " + c.get("descripcion", "") +
                    " " + " ".join(c.get("titulos", []))),
                "fuente": c.get("fuente", "descubrimiento_auto"),
                "estado": vieja["estado"] if vieja else "nuevo",
                "notas": vieja["notas"] if vieja else "",
            })

    # Conserva filas antiguas que esta pasada no haya vuelto a ver.
    filas.extend(previas.values())
    filas.sort(key=lambda r: (r["grupo"], r["rol"] != "principal"))

    DATOS.mkdir(exist_ok=True)
    with open(SALIDA, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNAS)
        w.writeheader()
        w.writerows(filas)
    return filas


# ------------------------------------------------------------------- Main --

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--per-keyword", type=int, default=20,
                    help="resultados por palabra clave en YouTube")
    ap.add_argument("--max-channels", type=int, default=80,
                    help="máximo de canales de YouTube a analizar a fondo")
    ap.add_argument("--hilos", type=int, default=4)
    args = ap.parse_args()

    keywords = leer_keywords()
    brutos = buscar_youtube(keywords, args.per_keyword)
    print(f"[YT] canales vistos en búsquedas: {len(brutos)}")

    # Prioriza los canales que más aparecen y con más vistas.
    orden = sorted(brutos.items(),
                   key=lambda kv: (-kv[1]["apariciones"], -kv[1]["vistas"]))
    orden = orden[: args.max_channels]

    cuentas = []
    with ThreadPoolExecutor(args.hilos) as ex:
        futuros = {ex.submit(meta_canal_youtube, cid, b): cid
                   for cid, b in orden}
        for i, fu in enumerate(as_completed(futuros), 1):
            c = fu.result()
            texto = c["nombre"] + " " + c["descripcion"] + " " + " ".join(c["titulos"])
            if not parece_ingles(texto):
                continue
            if es_mitologia(texto) == 0:
                continue
            cuentas.append(c)
            print(f"[YT] ({i}/{len(orden)}) ok: {c['nombre']} "
                  f"({c['seguidores']:,} subs)")

    # TikTok: verifica enlazados y adivinados.
    handles = candidatos_tiktok(cuentas)
    print(f"[TT] handles a comprobar: {len(handles)}")
    with ThreadPoolExecutor(args.hilos) as ex:
        futuros = {ex.submit(verificar_tiktok, h): h for h in handles}
        for fu in as_completed(futuros):
            t = fu.result()
            if not t:
                continue
            texto = t["nombre"] + " " + t["descripcion"]
            # Si fue adivinado por nombre, exige señal de mitología en su bio.
            enlazado = handles.get(t["url"].split("@")[-1].lower())
            if not enlazado and es_mitologia(texto) == 0:
                continue
            cuentas.append(t)
            print(f"[TT] ok: @{t['url'].split('@')[-1]} "
                  f"({t['seguidores']:,} seguidores)")

    # Facebook enlazado desde YouTube/TikTok (no rastreable en directo).
    for c in list(cuentas):
        fb = c.get("enlaces", {}).get("facebook")
        if fb and not any(x["url"].rstrip("/").lower() == fb.rstrip("/").lower()
                          for x in cuentas):
            cuentas.append({
                "id": fb.rstrip("/").lower(), "nombre": c["nombre"],
                "plataforma": "facebook", "url": fb, "seguidores": 0,
                "descripcion": "", "enlaces": {},
                "fuente": "enlace_desde_" + c["plataforma"],
            })

    cuentas.extend(leer_semillas())

    # Quita duplicados exactos por URL.
    unicos, vistos = [], set()
    for c in cuentas:
        k = c["url"].rstrip("/").lower()
        if k not in vistos:
            vistos.add(k)
            unicos.append(c)

    grupos = agrupar(unicos)
    filas = escribir_salida(grupos)
    n_grupos = len({f['grupo'] for f in filas})
    print(f"\nHecho: {len(filas)} cuentas, {n_grupos} creadores -> {SALIDA}")


if __name__ == "__main__":
    sys.exit(main())
