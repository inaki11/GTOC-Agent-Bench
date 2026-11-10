# Integración y comprobaciones de GTOC11

Integrado el 3 de noviembre de 2026 como **gtoc11**, revisión
**gtoc11-official-b1-v1**. Se utiliza el ejecutable Linux original enviado
por los organizadores; no es un validador reconstruido. La adaptación fija
el bonus temporal a **B=1**, comprueba el archivo completo antes de ejecutar
C++ y exige acuerdo entre el resultado oficial y un cálculo independiente
de la función de mérito.

## Material recibido y ejercicio

El usuario aportó cuatro archivos en `Downloads/Check Program`. Sus bytes
originales se han preservado:

| Archivo | Destino | Bytes |
| --- | --- | ---: |
| Candidate Asteroids.txt | problems/gtoc11/data/ | 8.862.855 |
| GTOC11Check_Linux | problems/gtoc11/validator/ | 169.880 |
| GTOC11Check_Windows.exe | references/gtoc11/received/ | 75.776 |
| Solution.txt | references/gtoc11/received/ | 41.538.543 |

El catálogo contiene **83.453 asteroides**. El ejercicio incluye instrucciones,
ecuaciones, constantes, restricciones, el formato de solución y la política
de validación. Hay configuración de ejemplo en `configs/codex-gtoc11.yaml`.
La selección está integrada en CLI, controlador, pasarela protegida, límites
de tamaño/tiempo y agentes. La imagen construida es `gtoc-bench:gtoc11-cpu`
(Linux amd64, todos los runtimes de agentes).

Los [materiales del ejercicio](../problems/gtoc11/instructions.md) y su
[procedencia](../problems/gtoc11/PROVENANCE.md) están fijados con hashes SHA-256.
El binario Linux conserva SHA-256
`6beb6456ee3959034ef3510ea646edf1633d37cb1fd61e41687dee3d94b4eabb`.
La adaptación probada tiene hash
`a966f7264b993fa9cd88dacdf2b8b59ad134c603c76af54b85847f12bc4a3fcb`.

Se identificó y evitó una discrepancia entre versiones del enunciado. El PDF
inicial del 10 de octubre de 2021 usa `alpha=1e-9 s^-1`; las reglas finales
del 16 de octubre y el ejecutable usan **6e-9 s^-1**. El PDF antiguo recuperado
se conserva únicamente en las referencias del host. La descarga directa del
PDF final y el acceso al sitio original no estaban disponibles durante la
integración (errores de certificado / HTTP 503 y timeout). La referencia
matemática usa las correcciones de la versión final, contrastadas con su texto
indexado, el artículo de los organizadores y el programa recibido. No se
presenta el PDF antiguo como versión final ni se finge una descarga del final.

## Ejecución del ejemplo completo

El archivo de **203.828 filas** fue aceptado por el programa original y por
la integración protegida, sin red y con raíz de contenedor de solo lectura:

| Magnitud | Resultado |
| --- | ---: |
| Estado oficial | Status:0; exit code 0 |
| Asteroides transferidos | 148 |
| Masa mínima de estación | 549.777.130.818.197 kg |
| Radio Dyson | 1,16410197870057 AU |
| Penalización total de Delta-V | 26,063339920199628 |
| Score original del programa | **1556,5911** |
| Score independiente, B=1 | **1556,5911158875317** |
| Bonus temporal aplicado | **1** |

La comprobación completa tarda aproximadamente **5-6 segundos** en este Mac
usando Docker Linux amd64. El límite nativo es 600 segundos. Esta medida no
es un límite garantizado para otros tamaños, controles o máquinas.

La cifra coincide con la entrada histórica de AU-LU en masa, cantidad y score
redondeados. El organizador solo identificó el archivo como ejemplo: **no se
afirma que sea la entrega exacta de ese equipo** ni se utiliza para sustituir
su fila del leaderboard.

Hay detalles de serialización relevantes para generar soluciones: la fila
de salida de la Tierra contiene la velocidad heliocéntrica **posterior** a
la salida y el vector de exceso por separado; la fila de impulso posterior
contiene la velocidad entrante. La masa impresa en el ejemplo es la masa de
llegada, repetida durante cada transferencia. El programa admite además pasos
ligeramente superiores al día nominal en ese ejemplo. La adaptación conserva
esas comprobaciones originales y no añade reglas numéricas incompatibles.

## Ensayos de rechazo y de aislamiento

La [auditoría protegida](../references/gtoc11/reference-checks.json) registra
**20 casos**: cuatro aceptados y **16 rechazados**. Los aceptados son el
ejemplo completo, un subconjunto con una transferencia real y score cero,
y dos flotas vacías de referencia con tipos de control 0 y 1. Estos dos
últimos verifican el contrato y el cero legítimo, no constituyen un ensayo
positivo de trayectoria de empuje en ambos modos. El ejemplo completo usa
tipo 1.

Entre los rechazados hay salida incorrecta de la Tierra, exceso superior a
6 km/s, ruptura de dinámica impulsiva, aceleración ATD incorrecta, masa de
llegada falsa, posición/velocidad final incorrectas, activación a los 29 días,
NaN, estación/radio/tipo inválidos, truncamientos, datos extra y duplicación de
transferencia. El ensayo de espera recalcula la posición y velocidad Kepleriana
del asteroide en la nueva fecha, de modo que alcanza realmente el chequeo
oficial de **30 días**. No se confunde con un rechazo por efemérides incoherentes.

Los rechazos físicos conservan mensajes como:

```text
Line 2: Initial DV is too large! (Violates 6 km/s constraint)
Line 44: The activation time is too early! (Violates 30 days constraint)
Line 1303: Mass cannot match!
```

La interfaz devuelve `score: null` para rechazos; un cero validado sigue siendo
`status: valid, score: 0`. Las pruebas de salida provocan errores, nunca puntos,
ante resultados ausentes, contradictorios, duplicados, negativos o no finitos,
procesos fallidos y cualquier score con un bonus añadido. Hay ensayos de
timeout y limpieza de directorios privados.

La prueba de ejecución completa usa un proveedor simulado: el agente puede
evaluar un candidato y recibir un rechazo físico, mientras el juez se ejecuta
como UID 1001 y las herramientas del agente como UID 1000. El agente no puede
leer ni sobrescribir el ejecutable, modificar el catálogo ni acceder a las
referencias del host. Se comprueba la conservación de veredictos y mensajes
en los archivos de la ejecución. No se ha consumido inferencia de pago.

Validación realizada:

- Suite general: **582 pruebas pasadas**, una omitida y 54 pruebas Docker/lentas
  deseleccionadas, en 43,63 segundos.
- Pruebas unitarias específicas de GTOC11: **63 pasadas**; leaderboards:
  **15 pasadas**. Las dos últimas comprueban la auditoría guardada y las
  efemérides usadas para aislar la restricción de espera.
- Integración Docker: **10 pruebas pasadas en 31,95 segundos**, incluyendo
  ejemplo completo, cinco rechazos físicos, trayectoria derivada, regresión de
  GTOC12, ejecución de agente, exclusión de referencias y rechazo de activación
  temprana con estado orbital coherente.
- Integridad de materiales de las cuatro ediciones y reconstrucción sin red
  de los **14 artefactos** de leaderboard.

Los ensayos negativos demuestran el rechazo de esos casos; no estiman una
tasa estadística de falsos positivos ni prueban formalmente el binario cerrado.
La referencia autoritativa para la aceptación es el ejecutable recibido. No se
certifica que cualquier versión histórica del servidor sea byte a byte idéntica.

## Leaderboards y referencias fuera del agente

Se guardan dos versiones con **25 equipos**:

- [Leaderboard oficial final](../references/leaderboards/gtoc11/official-final.md),
  con sus puntuaciones históricas y mejores fechas publicadas.
- [Aproximación sin bonus](../references/leaderboards/gtoc11/without-time-bonus-approx.md),
  calculada como `J_oficial/B(fecha_mejor_entrega)`.

La apertura de submissions fue **17-10-2021 12:00 UTC**, y el cierre
**07-11-2021 12:00 UTC**, según el anuncio oficial. No se utiliza el día de
publicación del problema como inicio del bonus. Las fechas tienen resolución
de minutos y los scores tres decimales; la tabla sin bonus es aproximada.
La fila de Iowa State muestra una hora cuatro minutos posterior al cierre:
se conserva esa hora y se fija B=1 para su estimación, sin inferir la causa.

Todos los leaderboards, la solución de ejemplo completa, el ejecutable de
Windows y el PDF histórico están en `references/`, excluido por `.dockerignore`.
Solo el catálogo, los documentos actuales y el juez Linux entran en la imagen.

## Repetir las comprobaciones

Desde `bench`:

```sh
uv run gtoc validate --problem gtoc11 --image gtoc-bench:gtoc11-cpu /ruta/solucion.txt
uv run python references/gtoc11/check_reference.py --image gtoc-bench:gtoc11-cpu --output /tmp/gtoc11-checks.json
uv run pytest tests/test_gtoc11.py tests/test_leaderboards.py -m 'not docker'
GTOC_TEST_IMAGE=gtoc-bench:gtoc11-cpu uv run pytest tests/test_gtoc11_docker.py
uv run python references/leaderboards/rebuild.py --check
```

En Linux amd64 puede usarse también `gtoc validate --problem gtoc11 --local
ARCHIVO`. En macOS se necesita Docker. Para iniciar una ejecución de búsqueda
real, con el proveedor y presupuesto configurados, usa
`uv run gtoc run configs/codex-gtoc11.yaml`.
