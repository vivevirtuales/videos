# Buscador de competidores de mitología

Sistema para encontrar canales de mitología en inglés que se puedan clonar
con Viral Clone. Cubre YouTube, TikTok y Facebook, y evita duplicar
creadores que suben lo mismo en varias plataformas.

## Qué hay aquí

| Archivo | Para qué sirve |
|---|---|
| `data/creators.csv` | La lista de canales encontrados. Es el archivo que consultas. |
| `tools/discover.py` | El rastreador. Cada vez que se ejecuta, busca canales nuevos y actualiza la lista. |
| `tools/keywords_en.txt` | Las palabras de búsqueda. Puedes añadir o quitar las que quieras. |
| `data/seeds_*.csv` | Hallazgos de investigación web (sobre todo Facebook, que no se puede rastrear en directo). |

## Cómo leer la lista (`data/creators.csv`)

- **grupo**: identifica al creador. Si dos filas tienen el mismo grupo, son la misma persona en distintas plataformas.
- **rol**: `principal` es la cuenta más grande del creador. Las marcadas `duplicado_de:...` NO las clones: es el mismo contenido.
- **seguidores**: tamaño de la cuenta. Un 0 en Facebook significa que no se pudo leer la cifra (Facebook pide iniciar sesión).
- **senales_mito**: cuántas señales de mitología se detectaron. Cuanto más alto, más seguro que va de mitología.
- **estado**: empieza en `nuevo`. Cámbialo tú a mano a lo que te sirva (`clonando`, `descartado`...). El rastreador respeta lo que escribas y no lo pisa.
- **notas**: campo libre para ti. Tampoco se pisa.

## Cómo volver a buscar canales nuevos

```
pip install yt-dlp
python3 tools/discover.py
```

Tarda unos 10-15 minutos. Solo añade canales nuevos; lo ya revisado se queda como esté.

## Límites conocidos

- **Facebook no se puede rastrear directamente** (pide iniciar sesión). Las páginas de Facebook llegan por dos vías: los enlaces que los propios canales de YouTube/TikTok publican, y la investigación web guardada en `data/seeds_*.csv`.
- **TikTok no tiene buscador abierto.** Se verifican los perfiles enlazados desde YouTube y los que coinciden con el nombre del creador.
- Los seguidores de Facebook casi nunca se pueden leer sin sesión.

## Para excluir tus canales ya vigilados

Añade sus URLs a `data/creators.csv` con estado `ya_vigilado` (o el que
prefieras). En la siguiente pasada se conservará ese estado.
