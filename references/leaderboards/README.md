# Leaderboards de referencia — solo para el host

Estas tablas son referencias históricas del benchmark para el investigador.
**No se incluyen en el contexto de construcción Docker, la imagen, `/problem`,
las instrucciones ni el workspace inicial del agente.** Tampoco se utilizan para
validar una solución ni para conceder puntos a un agente.

| Edición | Versión | Equipos | Tabla | Datos completos |
| --- | --- | ---: | --- | --- |
| GTOCX | Oficial final, archivo completo | 42 | [Markdown](gtocx/official-final.md) | [JSON](gtocx/official-final.json) |
| GTOCX | B=1: 12 recalculadas y 30 aproximadas | 42 | [Markdown](gtocx/without-time-bonus.md) | [JSON](gtocx/without-time-bonus.json) |
| GTOC11 | Oficial final | 25 | [Markdown](gtoc11/official-final.md) | [JSON](gtoc11/official-final.json) |
| GTOC11 | Aproximación con B=1 | 25 | [Markdown](gtoc11/without-time-bonus-approx.md) | [JSON](gtoc11/without-time-bonus-approx.json) |
| GTOC12 | Oficial final | 28 | [Markdown](gtoc12/official-final.md) | [JSON](gtoc12/official-final.json) |
| GTOC13 | Oficial final | 52 | [Markdown](gtoc13/official-final.md) | [JSON](gtoc13/official-final.json) |
| GTOC13 | Aproximación con c=1, reordenada | 52 | [Markdown](gtoc13/without-time-bonus-approx.md) | [JSON](gtoc13/without-time-bonus-approx.json) |

Capturas: 31 de octubre de 2026; GTOC11 añadido el 3 de noviembre. Las fuentes, fechas de captura, método de acceso
y hashes SHA-256 están en [sources.json](sources.json). Se conservan las
respuestas/tablas de origen para reconstruir los resultados sin depender de la
disponibilidad futura de las webs.

## GTOCX

El [archivo oficial de JPL conservado por ESA](https://electra.estec.esa.int/jpl.gtocx/gtocx/leaderboard/index.html)
contiene las 42 entradas, incluyendo diez ausentes de la tabla abreviada del
portal GTOC. Se guardan sus puntuaciones con bonus y las fechas originales.

El bonus era `B = 1 + ((t_end - t_check)/(t_end - t_start))^4`, con inicio de
submissions el **21-05-2019 20:00 UTC** y fin el **12-06-2019 20:00 UTC**.
El benchmark fija B=1 y conserva todos los demás términos.

En la tabla sin bonus, 12 puntuaciones se recalculan directamente a partir de
las [entregas públicas completas](../gtocx/README.md), todas verificadas físicamente.
Sus N y Delta-V se contrastan con el leaderboard, considerando la precisión
de las cantidades publicadas. Estas filas no dependen de ninguna fecha estimada.
Las otras 30 se aproximan dividiendo J oficial por B, usando Best Submission.
Cada fila indica qué procedimiento se usó y conserva el puesto oficial.

La zona horaria de las filas de la web archivada no aparece rotulada. Para las
estimaciones se usa **UTC−05:00**, inferido y contrastado con los dos archivos
tempranos cuyo bonus es apreciable: Jena y Citadel. Además faltan los segundos
y el organizador indicó que el bonus se evaluaba al empezar el chequeo, que podía
ser posterior a la subida. Estas limitaciones quedan explícitas en los JSON;
las fechas convertidas se llaman `best_submission_utc_estimate`.

Los mismos archivos finales se mantienen al quitar B y se reordenan por su nueva
puntuación. No se puede recuperar la mejor entrega de cada equipo bajo B=1
entre todos sus intentos, ni una clasificación de resultados postcompetición.

## GTOC11

Se conservan las 25 entradas de la [tabla final de ESA](https://sophia.estec.esa.int/gtoc_portal/?page_id=1444),
con sus puntuaciones oficiales, M mínimo, número de asteroides y mejor entrega.
La captura usa la tabla indexada de la fuente oficial; la descarga directa no
estaba disponible al integrarla. Los timestamps están rotulados explícitamente UTC.

El benchmark utiliza el ejecutable oficial recibido y omite el bonus. La tabla
normalizada aproxima `J(B=1)=J_oficial/B(t)`, donde
`B=2-sqrt(1-((t_fin-t)/(t_fin-t_inicio))^3)`. El
[anuncio oficial](https://groups.google.com/g/gtoc_competition/c/VpCF-OOedC4)
sitúa la apertura de submissions el **17-10-2021 12:00 UTC** y el cierre el
**07-11-2021 12:00 UTC**. El origen del bonus es la apertura de submissions,
no la publicación del problema del 10 de octubre.

Las 25 puntuaciones sin bonus son aproximadas: las fechas no incluyen segundos
y la tabla tiene tres decimales. El JSON conserva un intervalo por esa precisión.
La fila de Iowa State muestra 12:04 del día del cierre; se preserva su timestamp
y se fija B=1 en esa estimación, sin extrapolar un bonus inferior a 1. No se
deduce por qué el timestamp aparece después del cierre.

La solución completa que envió el organizador obtiene 1556,5911 puntos sin bonus
y coincide en masa y cantidad con AU-LU, pero fue identificada solo como ejemplo.
No se asume que sea su archivo exacto ni se la usa para recalcular esa fila.
Se normalizan las mismas entregas retenidas, sin reconstruir el mejor intento
histórico de cada equipo bajo B=1.

## GTOC12

El [API del organizador](https://gtoc12.tsinghua.edu.cn/prod-api/solutions/getSolutionLeaderboard)
devuelve las 28 entradas finales, con fechas, números de naves y asteroides y
puntuaciones. Todos sus puestos, nombres, cantidades y scores se han contrastado
con el [leaderboard final del portal GTOC de ESA](https://sophia.estec.esa.int/gtoc_portal/?page_id=1437).
Se admite únicamente la diferencia tipográfica entre apóstrofos rectos y curvos.

El benchmark ya **omite el componente competitivo decreciente por asteroide**:
fija todos los coeficientes de actividad a 1 y puntúa la masa total devuelta.
La tabla oficial histórica conserva sus puntuaciones ponderadas originales.
Por tanto, **no es directamente comparable con la puntuación del benchmark**.
No se puede deshacer la ponderación dividiendo por un factor global: depende de
la masa recuperada en cada asteroide y de las soluciones de los otros equipos.
Estas tablas no publican la masa total sin ponderar de cada equipo. Los valores
normalizados quedan explícitamente como `null`, sin inventar una clasificación.

## GTOC13

Se conserva el [leaderboard final oficial de JPL](https://gtoc.jpl.net/gtoc13/leaderboard/)
con las 52 entradas, puntuación, envíos, fechas de mejor y última entrega, número
de flybys científicos, Vx y tiempo de vuelo. La captura procede de la extracción
indexada de esa página oficial; no de una respuesta HTML directa desde el host.

La segunda versión estima, para cada entrada final:

```text
d = (Best Submission en UTC - 2025-10-20 17:00:00 UTC) / 86400 segundos
c(d) = 1.13                     si d <= 7
c(d) = 1.165 - 0.005*d          si d > 7
J_sin_bonus ≈ J_oficial / c(d)
```

El origen corresponde a la publicación del problema el **20 de octubre** según
el [calendario oficial](https://gtoc.jpl.net/), no al inicio de recepción de
soluciones del día 24. La fórmula de c está en el
[enunciado original](https://gtoc.jpl.net/static/gtoc13/gtoc13_problem_statement.pdf).
Se conserva el bonus de gran tour b, y solo se elimina el bonus temporal c.

Se utiliza **Best Submission**, que es la fecha asociada al score publicado.
**Last Submission** puede ser varios días posterior; por ejemplo, en KAIST,
NUAA & Friends y DSJ. Usar esa fecha quitaría un bonus que no corresponde al score.
El JSON conserva ambas fechas y la posición oficial, además de la posición
aproximada al ordenar por J/c.

Esta reconstrucción es aproximada: faltan los segundos y la puntuación original
está redondeada. El JSON incluye un intervalo orientativo que considera el
redondeo a 0,001 puntos y un instante dentro del minuto publicado. No cubre
convenciones internas del host no documentadas. En la tabla se muestran dos
decimales para los valores sin bonus.

Además, se recalcula **la misma entrega retenida por equipo en el leaderboard
oficial final**. No tenemos aquí todas sus entregas y no podemos asegurar que
esa fuera su mejor trayectoria si c hubiera sido siempre 1. La clasificación
normalizada no es oficial y tampoco implica que el validador independiente del
benchmark acepte todas esas trayectorias.

Como contraste numérico, KAIST queda aproximadamente en **111,20713** puntos
sin bonus; el archivo público utilizado en las pruebas del evaluador obtiene
**111,20733**. La diferencia es inferior a 0,001 puntos, compatible con la
precisión de los datos publicados.

## Reconstrucción e integridad

Desde la raíz de `bench`:

```sh
uv run python references/leaderboards/rebuild.py
uv run python references/leaderboards/rebuild.py --check
```

El programa lee las capturas, verifica sus hashes y el PDF de reglas utilizado,
comprueba el acuerdo entre las dos fuentes GTOC12 y genera catorce tablas/datasets.
No consulta Internet, no ejecuta archivos de participantes y no modifica jueces
ni resultados de runs. Para actualizar una captura, hay que obtenerla otra vez
de la fuente oficial y actualizar explícitamente su procedencia y hash.
Si cambia el juez GTOCX, hay que repetir la auditoría de las 12 entregas y
actualizar el hash del informe de comprobaciones en `sources.json` antes de
regenerar ese leaderboard. El generador rechaza auditorías de otro código.

La exclusión `references/` en `.dockerignore` también impide copiar estas
referencias accidentalmente mediante un `COPY .` futuro. Las pruebas incluyen
una construcción Docker que debe rechazar el intento de copiarlas.
