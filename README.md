# Buscador de competidores de mitología (YouTube + Facebook)

Sistema para encontrar canales de mitología en inglés que se puedan clonar
con Viral Clone. Solo YouTube y Facebook. Evita duplicar creadores que
suben lo mismo en las dos plataformas.

## Qué hay aquí

| Archivo | Para qué sirve |
|---|---|
| `data/creators.csv` | La lista final de canales. Es el archivo que consultas. |
| `data/candidatos.json` | Resultado intermedio del escaneo (datos crudos). |
| `data/seeds_*.csv` | Páginas de Facebook halladas por investigación web. |
| `tools/discover.py` | El rastreador. |
| `tools/keywords_en.txt` | Las palabras de búsqueda. Añade o quita las que quieras. |

## Filtros de calidad

Un canal solo entra en la lista si cumple TODO esto:

1. **Potente**: 20.000 seguidores o más.
2. **Con recorrido**: 30 vídeos o más.
3. **Vivo**: último vídeo hace menos de 45 días.
4. **Con audiencia real**: visitas medias de sus últimos vídeos ≥ 5.000.
5. **En inglés.**
6. **De mitología de verdad**: se leen los títulos de sus últimos ~30 vídeos
   y la mayoría deben ser de mitología. La revisión título a título la hace
   Claude; sin Claude, decide la mayoría de palabras clave.

Las cifras se cambian con parámetros: `--min-subs`, `--min-videos`,
`--max-dias`, `--min-visitas`.

## Cómo leer la lista (`data/creators.csv`)

- Está ordenada de mejor a peor (por visitas medias del creador).
- **grupo**: mismo grupo = mismo creador en distintas plataformas.
- **rol**: `principal` es su cuenta más fuerte. Las `duplicado_de:...` NO
  las clones: es el mismo contenido.
- **seguidores / videos / ultimo_video / visitas_media**: las métricas del
  canal. En Facebook, un 0 significa que no se pudo leer la cifra.
- **titulos_mito**: cuántos de sus últimos títulos son de mitología (ej. 24/31).
- **estado**: empieza en `nuevo`. Cámbialo tú a mano (`clonando`,
  `descartado`, `ya_vigilado`...). El rastreador no pisa lo que escribas.
- **notas**: campo libre para ti. Tampoco se pisa.

## Cómo volver a buscar canales nuevos

```
pip install yt-dlp
python3 tools/discover.py escanear    # 15-25 min: busca y mide canales
python3 tools/discover.py volcar      # escribe data/creators.csv
```

Con Claude: pídele que revise `data/candidatos.json` título a título y que
ejecute `volcar --veredictos data/veredictos.json`. Filtra mejor que las
palabras clave solas.

## Facebook: cómo llega

Facebook no deja buscar sin iniciar sesión, así que las páginas llegan por
dos vías:

1. Los enlaces que los propios canales de YouTube publican.
2. Investigación web (Claude), guardada en `data/seeds_facebook_research.csv`.

Para búsqueda automática dentro de Facebook o para rellenar los seguidores
que faltan hace falta Apify (de pago).

## Para excluir tus canales ya vigilados

Pon su fila en `data/creators.csv` con estado `ya_vigilado` (o el que
prefieras). Las pasadas siguientes conservan ese estado.
