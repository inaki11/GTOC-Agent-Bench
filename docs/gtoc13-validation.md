# GTOC13: validación de la revisión v2

Revisión vigente: `gtoc13-reconstructed-controls-v2`. Auditoría del 31 de octubre de 2026.

**El archivo Antipodes completo pasa ahora la validación física y obtiene
329,33149579 puntos con c=1.** KAIST y el ejemplo de PlanetX siguen pasando.
La mejora reconstruye los controles interiores de vela y aplica la tolerancia
relativa por intervalo del enunciado. Se ha retirado el cierre global adicional
que v1 imponía a los arcos de vela.

La [explicación técnica y migración](gtoc13-control-reconstruction-v2.md) detalla
el algoritmo, las restricciones conservadas, el cambio de contrato numérico y
sus límites. La [política entregada al agente](../problems/gtoc13/validation-policy.md)
contiene la especificación de esta revisión.

## Resultados con referencias reales

| Archivo | Resultado completo | Puntuación c=1 |
| --- | --- | ---: |
| `planetx-conic.txt` | Válido; 1 encuentro científico | 21,92704582 |
| KAIST `high-score.txt` | Válido; 6 encuentros científicos | 111,20732894 |
| `antipodes-submitted.csv` | Válido; 252 encuentros, 133 científicos | 329,33149579 |

KAIST procede del [archivo público del participante](https://github.com/GunheeYi/gtoc13/blob/main/solutions-important/high-score.txt).
La puntuación sin bonus reconstruida desde el leaderboard es aproximadamente
111,20713, una diferencia de 0,00020 puntos respecto al archivo. Aplicando el
bonus estimado al resultado calculado se recuperan los 125,142 puntos oficiales
al redondear a tres decimales.

Antipodes es una copia byte a byte del archivo suministrado por el usuario, con
SHA-256 `6297881d46e0e1ea238c1cd9a9f612b15ab2e4cb27c880383c41d429a6ad89b8`.
El historial suministrado contiene un último registro `pass`, 337,878 puntos,
133 flybys científicos, Vx 41,582 km/s y TOF 191,177 años: las métricas redondeadas
coinciden. El historial no contiene un hash del archivo enviado al host, por lo
que esa coincidencia no es una certificación independiente de identidad exacta.

El archivo completo verifica **23.931 intervalos de vela**, cada uno con RK4,
DOP853, Radau y controles interiores. Se reconstruye la orientación en **3.648**
intervalos; los otros **20.283** mantienen la interpolación lineal. La suma
científica es **274,4429131566** y el bonus de gran tour es **1,2**. La suma
coincide por redondeo con los 274,443 publicados en el
[artículo de los participantes](https://arxiv.org/html/2607.10150v2#S6.SS1).

| Comprobación de Antipodes | Máximo observado | Límite |
| --- | ---: | ---: |
| Error relativo RK4 | 9,58435e-5 | <1e-4 |
| Error relativo de propagación independiente, con margen numérico | 9,69015e-5 | <1e-4 |
| Diferencia DOP853–Radau en posición | 0,365 m | 6,25 m de umbral de desacuerdo |
| Diferencia DOP853–Radau en velocidad | 8,62e-11 km/s | 6,25e-9 km/s de umbral de desacuerdo |
| Distancia mínima a Altaira | 0,05845014 AU | Reglas de perihelio del enunciado |

Los defectos respecto a los nodos enviados alcanzan **41,63 km y 0,181 m/s**;
son distintos de la diferencia entre integradores. Cumplen el límite relativo
local, que escala con el cambio de estado. Esta revisión **no certifica una
única propagación global con error absoluto de 100 m**. Esos valores quedan
registrados en el veredicto, sin ocultarlos ni confundirlos con tolerancias cónicas.

Los [veredictos completos](gtoc13-reference-checks.json) conservan los hashes
reales de candidato y código de cada comprobación, incluidos los de Docker.
La pequeña diferencia entre dos hashes nativos se debe a la corrección de una
cadena de documentación del grado de iluminación; el cálculo numérico es idéntico.

## Qué se conserva y qué cambia

Se conservan formato, valores finitos, estado inicial, tiempos, controles unitarios,
cono de vela, distancias mínimas interiores, efemérides, encuentros, v-infinity,
altitudes, límites científicos, navegación entre flybys repetidos y fórmulas de
puntuación. No se modifica el archivo de referencia ni se acepta por nombre/hash.
Si no se reconstruye y verifica un control, el resultado es `unverified`, sin score.
Los errores literales de formato y reglas siguen devolviendo `invalid` con causa.

Cambian la familia de controles interiores y el tratamiento de los defectos de
vela: cada intervalo parte de su nodo enviado y utiliza el criterio relativo
publicado. Su nodo final verificado se usa para el intervalo o encuentro siguiente.
Los arcos cónicos conservan sus comprobaciones analíticas y absolutas. La antigua
prueba que exigía un cierre global adicional de vela se ha sustituido por pruebas
explícitas del límite local, su registro y el rechazo al superarlo.

## Pruebas y tiempos

En esta auditoría pasan **391 pruebas rápidas** y la nueva prueba completa local
Antipodes. La suite rápida tardó **26,48 segundos**. La prueba completa local
pasó en **151,40 segundos**, con NumPy 2.2.6 y SciPy 1.16.2.

Las regresiones nuevas incluyen los diez primeros intervalos reales, el archivo
completo inalterado, un perfil curvo generado con ecuaciones e integración escritas
independientemente, la preservación de extremos y controles ilegales en el interior.
También se prueban impulsos artificiales de 1 m/s en las tres componentes de llegada,
un defecto de posición excesivo y el archivo completo con su velocidad inicial
corrompida: todos deben quedar sin puntuación. Se conservan los 64 mutantes de
llegadas balísticas y las demás pruebas físicas de GTOC13.

Dentro del juez protegido Docker Linux amd64, el archivo Antipodes también pasa,
con el mismo score, en **285,22 segundos**, por debajo del límite de 600 segundos.
La imagen construida para Codex CPU es `gtoc-bench:gtoc13-cpu-v2`.

Los archivos del checkout compartido cambiaron durante la construcción de imagen
por trabajo ajeno a este juez. Dos pruebas del controlador detectaron el desfase
y se detuvieron antes de arrancar el agente. La verificación del archivo completo
en el juez protegido sí pasó. Las dos pruebas del flujo pasaron después en **19,76 segundos** contra una copia
inmutable del código y los materiales de esa imagen, conservando activa la
comprobación de hashes. El informe JSON registra esa repetición.

Tras reconstruir también con el checkout actual, **las tres pruebas de evaluación
y flujo seleccionadas pasaron directamente en 35,54 segundos**. La imagen final
tiene ID `sha256:4c865ee7604122694db160677aa38859a73b1dfb0f25a8ca93d0d2505e5074a1`; el hash de su juez GTOC13 coincide
con el del juez protegido que aceptó Antipodes completo. No ha sido necesario
relajar ni desactivar la comprobación de integridad.

Las pruebas de flujo verifican el registro de un candidato de 26 MiB, los mensajes
de rechazo, permisos del juez, ausencia de referencias/leaderboards en la imagen
y el corte por tokens con proveedor simulado. No se usa inferencia de pago.

## Reproducción

```sh
uv sync --extra judge
uv run pytest -q -m 'not docker and not slow'
uv run pytest -q tests/test_gtoc13_controls.py -m slow
uv run gtoc build --cpu --agent codex --tag gtoc-bench:gtoc13-cpu-v2
GTOC_TEST_IMAGE=gtoc-bench:gtoc13-cpu-v2 uv run pytest -q tests/test_gtoc13_docker.py
```

Para guardar el veredicto completo de las pruebas lentas en JUnit, añadir
`-o junit_family=legacy --junitxml=/tmp/gtoc13-audit.xml`. La comprobación directa es:

```sh
uv run gtoc validate --problem gtoc13 --local tests/fixtures/gtoc13/antipodes-submitted.csv
uv run gtoc validate --problem gtoc13 --image gtoc-bench:gtoc13-cpu-v2 tests/fixtures/gtoc13/antipodes-submitted.csv
```

Las trayectorias de referencia y los leaderboards permanecen fuera del contexto
Docker y de los materiales del agente. El ejemplo de ejecución usa la nueva
etiqueta v2; para otros agentes, reconstruir con el perfil correspondiente.

## Alcance y evidencia histórica

La equivalencia con el host no está demostrada para todo archivo posible y la
tasa de falsos aceptados no se ha medido. La reconstrucción cuadrática puede
fallar donde el algoritmo privado de JPL encuentre otro control. Las pruebas
negativas ejercitan fallos concretos; no son una muestra estadística representativa.
La comprobación entre integradores tampoco es aritmética de intervalos.

El resultado anterior se conserva en [informe v1](gtoc13-validation-v1.md) y
[veredictos v1](gtoc13-reference-checks-v1.json). No se reescriben resultados de runs
previos. Los hashes y la revisión distinguen las dos versiones; las comparaciones
entre versiones deben identificar el cambio o reevaluar los mismos candidatos.
