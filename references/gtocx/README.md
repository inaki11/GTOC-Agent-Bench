# GTOCX: entregas oficiales y comprobaciones — solo host

Este directorio conserva las **12 entregas finales completas** enlazadas por el
[organizador en su web archivada por ESA](https://electra.estec.esa.int/jpl.gtocx/gtocx/teams-solutions/index.html).
Los archivos se descargaron directamente y sin modificaciones. Sus URL, tamaños
y SHA-256 están en [manifest.json](manifest.json). Coinciden también byte a byte
con las copias del repositorio público `jjsouthworth/GTOCX`, commit
`ac4c41d95ea48ab2fea5fb25d08c0c114bae150b`.

**No entran en la imagen ni en el contexto Docker, en `/problem` ni en el
workspace inicial del agente.** La exclusión `references/` se aplica a este
directorio y a los leaderboards. Son datos de contraste para el investigador.

[reference-checks.json](reference-checks.json) registra la comprobación física
completa de los 12 archivos con el juez independiente. Todos pasan en la revisión
guardada: se comprueban 23.217 asentamientos en total entre las doce entregas,
contando de nuevo los que aparecen en más de una. La puntuación siempre lleva B=1.
Cada resultado conserva el hash exacto del archivo y del código del juez, los
residuos numéricos, los recuentos y el desglose de puntuación.

La de NUDT&XSCC produce **3101,14603816 puntos**, con 3.798 asentamientos. Jena
produce **207,91466259** y Citadel **144,40684890**. Los dos últimos tienen una
diferencia sustancial respecto al score histórico por el bonus de entregas tempranas.

Para repetir toda la comprobación, desde `bench`:

```sh
uv run python references/gtocx/audit_submissions.py --jobs 3 --output /tmp/gtocx-reference-checks.json
```

El valor por defecto es un solo proceso; se permiten entre uno y cuatro. Las
comprobaciones completas tardan más que los tests unitarios. Cada archivo tiene
el mismo presupuesto numérico de 600 segundos que el evaluador. No se ejecuta
código de los participantes ni se hacen llamadas a modelos.
La opción `--output` anterior permite repetir la auditoría sin sustituir la
captura fijada. Si se sustituye `reference-checks.json`, también hay que actualizar
su hash en `references/leaderboards/sources.json` y regenerar las tablas locales.

Los [leaderboards locales](../leaderboards/README.md) incluyen las **42 entradas**
de la página original, más completas que la tabla abreviada del portal WordPress.
La versión sin bonus usa estas 12 puntuaciones recalculadas y estima las otras
30 a partir de las fechas. No mezcla los resultados posteriores a la competición
discutidos en el foro con las entregas del leaderboard oficial final.

`sources/` conserva páginas y aclaraciones del organizador utilizadas en la
implementación. Los documentos y datos necesarios para resolver el ejercicio,
con sus propios hashes, están en `problems/gtocx/`; estos sí se entregan al agente.
El informe de alcance y pruebas está en [docs/gtocx-validation.md](../../docs/gtocx-validation.md).
