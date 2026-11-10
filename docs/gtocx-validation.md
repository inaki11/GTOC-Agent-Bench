# GTOCX: implementación, referencias y pruebas

Revisión: `gtocx-independent-v1`. Fecha: 31 de octubre de 2026.

GTOCX está integrado como ejercicio `gtocx`, con documentos originales,
100.001 estrellas, formato de entrega y aclaraciones del organizador. El juez
independiente comprueba todo el árbol y todas las trayectorias antes de puntuar.
El bonus temporal se fija a **B=1**; se mantienen el resto de términos originales.

Código: [`src/gtoc_bench/gtocx`](../src/gtoc_bench/gtocx/judge.py).
Política completa: [validation-policy.md](../problems/gtocx/validation-policy.md).
La interfaz de benchmark sigue siendo `gtoc evaluate ARCHIVO --json`.
Los errores incluyen código, línea y explicación. `invalid`, `unverified` y
`validator_error` no conceden puntos y devuelven `score: null`.

## Datos y fuentes

El [archivo de la web original JPL alojado por ESA](https://electra.estec.esa.int/jpl.gtocx/)
conserva los dos PDF, el catálogo y 12 archivos finales completos. Se han
descargado sin cambios. También coinciden byte a byte con las copias históricas
del repositorio de participantes `jjsouthworth/GTOCX`, commit
`ac4c41d95ea48ab2fea5fb25d08c0c114bae150b`.

Las reglas incluyen los avisos del organizador sobre theta_f, cómputo de Delta-V,
espera de colonias, separación madre/pods, pods de impulso cero y fin de vuelo
de una madre en su última liberación. Se han inspeccionado los recursos públicos
C++, Fortran y MATLAB de participantes como contraste; no se usa su código
como juez de confianza ni se ejecutan sus optimizadores.

Las capturas y entregas permanecen en [references/gtocx](../references/gtocx/README.md).
Son referencias del host, excluidas de todo el contexto Docker, de la imagen,
de `/problem` y del workspace inicial del agente. Solo los documentos de reglas,
las efemérides y las instrucciones del ejercicio se entregan al agente.

## Doce comprobaciones físicas completas

Los doce archivos oficiales publicados pasan el juez actual. Entre todos suman
**23.217 asentamientos comprobados**, contando por separado los presentes en
varias entregas. Se verifican la causalidad completa y los límites de ramificación,
las trayectorias con su fuerza galáctica real, los encuentros y Delta-V, las
restricciones temporales y los extremos de radio dentro de cada tramo.

| Equipo | N | J con B=1 recalculado |
| --- | ---: | ---: |
| NUDT&XSCC | 3798 | 3101,14603816 |
| Tsinghua LAD - XINGYI | 2806 | 2070,53722173 |
| ESA-ACT | 2652 | 1996,11072351 |
| The Aerospace Corporation | 2435 | 1559,28104296 |
| HIT_BACC | 2855 | 1167,42300146 |
| CSU | 1246 | 1111,00886632 |
| worhp2orb | 3235 | 873,30956377 |
| 1-2-B-#1 | 1863 | 802,93202397 |
| Team Kataskopoi | 1352 | 597,04557776 |
| Team Jena | 5 | 207,91466259 |
| MSU-RAS-RUDN | 968 | 201,58317082 |
| The Citadel | 2 | 144,40684890 |

La puntuación de la entrega ganadora redondea a **3101,14604**, la cifra de la
web original. En los casos tempranos el bonus sí importa: Jena pasa de
368,847689 oficiales a 207,91466259 sin bonus y Citadel de 173,746592 a 144,40684890.
Los resultados completos, hashes y residuos están en
[`reference-checks.json`](../references/gtocx/reference-checks.json).

El mayor error de llegada en posición en este conjunto es **8,73857e-5 kpc**,
inferior al límite publicado de 1e-4 kpc. El mayor error de magnitud del impulso
de llegada es **0,00764453 km/s**, inferior a 0,01 km/s. Las comparaciones
numéricas independientes consumen parte de esos límites; no los amplían.

La entrega ganadora completa también pasa en la imagen Linux amd64
`gtoc-bench:gtocx-cpu`, con la misma puntuación, en aproximadamente **105 s**
en esta máquina. Su [veredicto de Docker](../references/gtocx/winner-docker-check.json)
registra la comprobación protegida. ID de imagen:
`sha256:159e1d2e6f1552879f5ff96cc8ecd7c5297bb8cbb157baffe21255b8b5cdad84`.
Estos tiempos son observaciones, no garantías para otras máquinas o tamaños.

## Particularidades confirmadas con los archivos reales

El enunciado define el error del impulso final por su **magnitud**. Varios
archivos públicos usan un signo distinto para su vector final. El juez reconstruye
el impulso físico necesario, `v_estrella - v_entrante`, comprueba su magnitud,
y comprueba también que esa maniobra real cabe en los presupuestos del vehículo.
Las direcciones de salida e intermedias se aplican íntegramente. No se interpreta
el vector final como una velocidad entrante arbitraria ni como una maniobra libre.

Algunos archivos usan etiquetas de Fast Ships repetidas o grupos de hijos sin
orden canónico. Se aceptan esas equivalencias de serialización, con notas, mientras
que el recuento global, el árbol, los tiempos y la física se comprueban igualmente.
No se ajusta la tolerancia física para conseguir que las referencias pasen.

## Leaderboards locales

El [leaderboard oficial completo](../references/leaderboards/gtocx/official-final.md)
contiene **42 equipos**. La tabla abreviada del portal GTOC contenía solamente 32.
La [versión sin bonus](../references/leaderboards/gtocx/without-time-bonus.md)
reordena las mismas entregas finales: 12 puntuaciones salen directamente de los
archivos validados y 30 se aproximan como J oficial dividido por B.

Para estas últimas, la apertura correcta es el 21 de mayo de 2019 a las 20:00 UTC,
no la publicación del problema del día 15. Las fechas de las filas del archivo
estático no llevan zona horaria; se utiliza UTC−05:00 inferido y contrastado con
las referencias tempranas de Jena y Citadel. Esa conversión se identifica como
estimada. Además, faltan los segundos y el organizador explicó que B se calculaba
cuando el servidor comenzaba el chequeo, con posibles demoras respecto a la subida.
Los valores aproximados no se presentan como puntuaciones exactas.

No se reconstruyen aquí todos los intentos históricos de cada equipo ni se
mezclan con resultados postcompetición. Cada fila indica procedencia, puesto
oficial y puesto recalculado. Los JSON conservan los valores completos.

## Pruebas y reproducción

La suite general sin Docker pasa con **483 pruebas**. Las nuevas pruebas GTOCX
cubren archivos públicos pequeños, un subconjunto ramificado de la ganadora,
impulsos corruptos, llegada por magnitud, límites de flota/Delta-V, duplicados,
orígenes sin asentamiento, espera de colonias, separación madre/pod, pods de
impulso cero, permutaciones del árbol y formatos numéricos hostiles.

Hay **48 alteraciones reproducibles de vectores de vuelo**, todas rechazadas.
También se verifica un periastro ilegal oculto entre extremos seguros, límites
exteriores, un caso circular analítico, conservación del momento angular y un
ejemplo de puntuación calculado a mano. Los tests de referencias verifican la
revisión del juez y el hash de los doce archivos para exigir una nueva auditoría
si cambia el código.

Pasan además las dos pruebas Docker GTOCX y la prueba de exclusión de referencias
del contexto. La prueba de ejecución completa usa un proveedor simulado, comprueba
el aislamiento y conserva una entrega válida y otra rechazada con causa legible.
No se ha consumido inferencia de pago para estas comprobaciones.

```sh
uv run pytest -q -m 'not docker'
uv run python references/gtocx/audit_submissions.py --jobs 3 --output /tmp/gtocx-reference-checks.json
uv run python references/leaderboards/rebuild.py --check
GTOC_TEST_IMAGE=gtoc-bench:gtocx-cpu uv run pytest -q \
  tests/test_gtocx_docker.py \
  tests/test_leaderboards.py::test_docker_context_cannot_copy_host_leaderboards
```

Se usa NumPy 2.2.6 y SciPy 1.16.2. La comparación entre DOP853 y Radau y el examen
de los polinomios de interpolación son comprobaciones de ingeniería, no una prueba
formal con aritmética de intervalos. Los doce positivos y las alteraciones negativas
aportan evidencia concreta, pero no demuestran equivalencia universal con JPL ni
una tasa estadística de falsos aceptados. Las dudas numéricas o límites de recursos
se resuelven sin conceder puntuación.
