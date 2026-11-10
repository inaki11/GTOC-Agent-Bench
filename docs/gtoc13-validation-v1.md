> Informe histórico de la revisión v1, sustituida por v2. Los rechazos y límites
> descritos aquí se conservan como evidencia del estado anterior. Consulte
> [el informe vigente](gtoc13-validation.md) para los resultados actuales.

# GTOC13: implementación y comprobaciones

Revisión: `gtoc13-conservative-v1`. Auditoría local del 31 de octubre de 2026.

El ejercicio está integrado en el controlador, la configuración, la CLI y la
imagen Docker. Incluye los dos PDF originales completos, los tres catálogos
de efemérides, transcripciones de lectura, instrucciones para el agente y la
política explícita del evaluador. Los cinco archivos originales se han comparado
byte a byte con los suministrados y coinciden.

## Qué se ha implementado

El juez comprueba formato, finitud de los números, identificadores, estructura de
arcos, continuidad, estado inicial, tiempos, dinámica, encuentros, velocidades
asintóticas, altitudes de flyby, restricciones científicas y distancias mínimas a
Altaira dentro de los tramos. Usa propagación kepleriana analítica, DOP853 y
Radau, además del requisito RK4 para la vela. Propaga el estado entre arcos sin
reiniciarlo en cada fila, comprueba directamente la llegada propagada y conserva
exactamente el módulo de v-infinity en las asistencias gravitatorias.

Después de aceptar toda la trayectoria, calcula los factores estacionales, de
velocidad y de gran tour. El bonus de calendario es **c = 1**, fijo para el
benchmark. Los intentos rechazados o no verificables tienen `score: null`.
Los errores de reglas incluyen código, número de línea y explicación. El mismo
texto se conserva en `submissions/<id>/report.txt`.

Código principal: [`judge.py`](../src/gtoc_bench/gtoc13/judge.py).
Especificación de las diferencias: [política del ejercicio](../problems/gtoc13/validation-policy.md).
Los hashes del código y los materiales se comprueban en el controlador y en el
juez protegido. Cada veredicto incluye el hash del candidato y del juez.

## Referencias comprobadas

| Archivo | Validación física completa | Puntuación de benchmark, c=1 |
| --- | --- | ---: |
| Conic inicial con llegada a PlanetX | Aceptada; 1 encuentro científico | 21,92704582 |
| Solución pública de KAIST `high-score.txt` | Aceptada; 6 encuentros científicos, 6 arcos balísticos | 111,20732894 |
| `antipodes_submitted.csv`, suministrado en el proyecto Jack | Rechazada por el perfil de interpolación de vela | Sin puntuación |

La comprobación de KAIST conserva la trayectoria entre arcos y obtiene un
residuo máximo de cierre de aproximadamente **28,1 m**, inferior al límite de
100 m, y un residuo máximo de velocidad de aproximadamente **4,65e-11 km/s**.
La referencia es el [archivo público del participante](https://github.com/GunheeYi/gtoc13/blob/main/solutions-important/high-score.txt);
la coincidencia con un resultado publicado no sustituye una etiqueta oficial
del host para el archivo exacto.

El archivo Antipodes se rechaza en la línea 2: error RK4 de velocidad
`1.43375161e-7 km/s`, frente al límite relativo equivalente de
`9.14931911e-8 km/s`, usando normales interpoladas linealmente y normalizadas.
Esto **no demuestra que el archivo fuera inválido para JPL**: el host optimizaba
controles interiores mediante un procedimiento no publicado.

Como comprobación separada de las fórmulas, los encuentros declarados de ese
archivo dan 252 flybys, 133 científicos, suma científica **274,44291316**, bonus
de gran tour **1,2** y producto **329,33149579** con c=1. Es un diagnóstico
aritmético de los encuentros, **no una puntuación concedida a la trayectoria**.
El artículo de los participantes informa de una suma científica de 274,443:
[informe Antipodes, sección de resultados](https://arxiv.org/html/2607.10150v2#S6.SS1).

Los veredictos y residuos de referencia se conservan en
[`gtoc13-reference-checks.json`](gtoc13-reference-checks.json). Las trayectorias
de otros participantes utilizadas en pruebas no se incluyen en la imagen ni
en los materiales entregados al agente.

## Pruebas reproducibles

Entorno local: NumPy 2.2.6 y SciPy 1.16.2. La imagen de evaluación es Linux amd64,
construida con el Dockerfile del repositorio como `gtoc-bench:gtoc13-cpu`.
ID de imagen comprobado:
`sha256:b006c7ac21cecaa924f543b10ab3e2e69aa8a2fa7c7934cc7c3c18ef2d90cdae`.

La suite sin Docker contiene **363 casos: 241 previos y 122 de GTOC13**.
Los casos nuevos incluyen:

* Referencias completas de PlanetX y KAIST, efemérides contrastadas con otro
  propagador y conservación de invariantes para órbitas elípticas, parabólicas
  e hiperbólicas.
* Trayectoria de vela generada con ecuaciones escritas independientemente y
  un integrador implícito; controles incorrectos y cambios de control duplicados.
* **64 alteraciones aleatorias reproducibles** de llegadas y velocidades, todas
  rechazadas; también NaN, infinitos, números enormes, flags fraccionarios,
  campos ausentes, tiempos ilegales y rupturas de continuidad.
* Perihelio peligroso oculto entre extremos seguros, dos pasos profundos,
  varios mínimos dentro de una región profunda y una órbita cuyo perihelio
  peligroso queda fuera del tramo realmente volado.
* Orientación de vela ilegal dentro del segmento, deriva acumulada entre filas
  y entre arcos, doble gasto de tolerancia al llegar y falsos impulsos en
  cuerpos sin masa.
* Una trayectoria fabricada para coincidir exactamente con RK4 que es
  rechazada por la propagación de mayor orden.
* Altitudes y velocidades de flyby, navegación entre encuentros repetidos,
  límite científico, asteroides/cometas antes del primer perihelio, factores
  estacionales, gran tour y ejemplo numérico del enunciado.
* Estados sin puntuación al agotarse el tiempo, diagnósticos legibles y JSON,
  selección de ejercicio, integridad de materiales y configuración.

Comando:

```sh
uv run pytest -q -m 'not docker'
```

También pasan **4 pruebas Docker seleccionadas**: los dos nuevos tests GTOC13,
la validación nativa determinista de GTOC12 y su ejecución con Codex y corte por
tokens. La prueba completa GTOC13 utiliza un proveedor simulado, entrega un
archivo de **26 MiB**, atraviesa la CLI, HTTP, el controlador y el juez protegido,
verifica los permisos y conserva un resultado válido y otro rechazado.
No requiere inferencia de pago.

```sh
GTOC_TEST_IMAGE=gtoc-bench:gtoc13-cpu uv run pytest -q \
  tests/test_gtoc13_docker.py \
  tests/test_docker.py::test_real_native_validator_valid_invalid_and_deterministic \
  'tests/test_docker.py::test_native_agent_gateway_and_soft_token_cutoff[codex]'
```

El código nuevo y los archivos modificados pasan Ruff. Los tests de Docker no
seleccionados, las variantes CUDA y las ejecuciones con modelos reales no forman
parte de esta comprobación.

## Límites de la conclusión

Este es un evaluador independiente y conservador utilizable para comparar
soluciones bajo una política explícita. **No se ha demostrado equivalencia con el
host ni una tasa estadística de falsos aceptados.** Las 64 alteraciones comprueban
fallos concretos, no una muestra representativa de todos los errores posibles.
La comparación entre integradores no es una prueba con aritmética de intervalos.

La mayor limitación práctica es la vela: los controles interiores del host no
se conocen. Esta versión impone una interpolación concreta y un cierre global
adicional. Puede rechazar soluciones admitidas históricamente, como ilustra el
archivo Antipodes. Las referencias reales que pasan de extremo a extremo son
balísticas; las pruebas positivas de vela son sintéticas. Para ampliar la
evidencia harían falta archivos exactos etiquetados por el host, especialmente
casos de vela y de frontera, o acceso a una consulta del evaluador oficial.

No se han relajado restricciones para conseguir que una referencia puntúe.
Las futuras modificaciones del perfil deberán generar una nueva revisión
experimental y actualizar hashes, pruebas e imagen.
