# Informe comparativo automático

El comando `gtoc compare` reconstruye las figuras, estadísticas, tablas y datos
de comparación desde manifiestos, resúmenes y eventos confirmados. No ejecuta
código del agente, no abre sus sesiones ni consulta APIs de modelos. El análisis
se realiza en el host y no consume cuota de inferencia.

```sh
uv sync --extra analysis
uv run --extra analysis gtoc compare \
  --results results \
  --problem gtoc12 \
  --wall-seconds 28800 \
  --output analysis/comparisons/current
```

El informe se abre en `analysis/comparisons/current/index.html`. Sin filtros
de problema o presupuesto se generan paneles separados para cada problema,
presupuesto temporal original y límite de tokens. **Todos los pares del mismo
problema y presupuesto se comparan juntos**, incluidas ejecuciones con distintas
políticas de pausas y cambios de recursos. Esas condiciones se conservan como
metadatos; no separan las gráficas. El tiempo es el activo registrado por cada
ejecución: `provider_wait` excluye esperas con el contenedor congelado, mientras
que `continuous_wall` puede consumir ese tiempo. La comparación es descriptiva.

Para generar GTOC 12 y GTOC 13 juntos con ocho horas:

```sh
uv run --extra analysis gtoc compare --wall-seconds 28800
```

Toda la generación es código Python local sobre las entradas guardadas y la
plantilla fija. No requiere credenciales, sesiones de agentes, acceso a modelos
ni explicaciones redactadas por un LLM. Tras instalar las dependencias, también
funciona con `uv run --offline --extra analysis ...`.

Por defecto se seleccionan
evaluaciones iniciadas y finalizadas con al menos 3.600 segundos asignados;
`--min-wall-seconds 0` permite incluir presupuestos menores. Las carpetas de
archivo y los interiores de runs no se recorren. Las comprobaciones funcionales
cortas ya archivadas no se incluyen en las comparaciones activas.

`gtoc analyze` y el cierre del experimento siguen generando las vistas
individuales. `gtoc compare` actualiza el informe colectivo con un comando;
no instala un servicio de vigilancia ni añade trabajo de gráficas al cierre
crítico de un contenedor.

## Figuras y estadísticas

Por cada panel se generan cuatro figuras, en PNG y SVG:

1. Media de las puntuaciones máximas válidas observadas de cada par agente–modelo.
   Las barras de error son **±1 desviación estándar muestral** de los máximos
   entre evaluaciones independientes. Se muestran las muestras individuales y n.
   Con una sola evaluación la dispersión queda como desconocida, no como cero.
2. Mejor puntuación acumulada por tiempo activo, con media y banda ±1 SD.
3. La misma comparación por consumo acumulado conocido de entrada más salida.
4. La misma comparación por coste API equivalente acumulado, aplicando la
   plantilla fija de precios.

Cada gráfica del HTML tiene un botón por par agente–modelo para activarlo o
desactivarlo, además de **Mostrar todos** y **Ocultar todos**. Los controles son
independientes entre gráficas y también están disponibles en la comparación de
harness. Al ocultar una serie se ocultan sus curvas individuales, banda de
dispersión, barras y muestras; las escalas permanecen fijas para conservar la
referencia visual. Las series sin datos para un eje aparecen deshabilitadas y
con su motivo. Los filtros no modifican tablas, estadísticas ni archivos fuente.

Estos controles funcionan al abrir el HTML local, sin servidor ni conexión.
El único JavaScript es un controlador fijo autorizado por su hash en la política
de contenido; no descarga recursos ni ejecuta contenido de los agentes. Sin
JavaScript se muestran todas las series disponibles. Los enlaces **SVG completo**
y **PNG completo** conservan las figuras estáticas con todas las series.

Si un modelo con el mismo esfuerzo de razonamiento se ha ejecutado con al menos
dos harness distintos, se añade la sección **Mismo modelo con distintos harness**
y una figura PNG/SVG. Muestra la media de máximos por evaluación independiente
de cada harness, su n y la desviación estándar muestral. Las continuaciones y
copias siguen contando una sola vez. Dos versiones de un mismo harness no
bastan para activar esta sección; cuando hay varios harness, cada versión
conserva su propia fila. Los problemas, presupuestos y esfuerzos de razonamiento
permanecen separados.

Con n=1 la media coincide con ese único máximo y no se estima dispersión. Las
interrupciones aportan el máximo observado y se identifican con su tiempo activo;
los cierres recuperados también quedan marcados. No se promedian todos los
candidatos de una ejecución ni se combinan las medias de distintos harness.
Los datos se exportan automáticamente en `harnesses.csv` y en
`panels[].harness_comparisons` de `comparison.json`. Si no existen modelos
comparables, el informe explica el motivo y el CSV conserva sus cabeceras.

Una versión distinta del agente o un esfuerzo de razonamiento distinto forman
series diferentes. Las condiciones de máquina, proveedor, imagen y recuperación
se conservan en cada evaluación; una comparación que las mezcle es descriptiva,
no una atribución causal al modelo ni al agente por separado.
Las tablas, colores y leyendas mantienen el mismo orden de series: agente y
orden de modelos declarado en `model_order` de la plantilla. Los identificadores
desconocidos se ordenan por nombre; el generador no infiere capacidades.

Cada gráfica indica **series representadas / series totales**. El HTML y
`coverage.csv` detallan la disponibilidad por par y eje. Si falta una tarifa o
el proveedor no comunicó consumo, se indican los pares y el motivo tanto en el
HTML como en la figura exportada. La tarifa ausente no se convierte en coste cero.
Si solo algunas repeticiones de un par tienen datos para un eje, se representan
esas repeticiones con su `n` y el total explícitos; las otras siguen presentes en
los ejes que sí permiten construir. Los motivos y las marcas de consumo parcial
se calculan desde los registros, sin intervención de un agente.

Las curvas son funciones escalonadas: el instante de un candidato es su
**recepción**, aunque su validación termine después. Un veredicto tardío
reconciliado sustituye al provisional del mismo envío; no añade una muestra.
Antes de la primera solución válida se usa el cero de referencia, conservando
por separado si existió alguna solución válida. Las evaluaciones sin solución
no desaparecen de la media.

Para una repetición que termina por presupuesto, salida voluntaria o fallo del
agente, la curva temporal mantiene el último máximo hasta el tiempo asignado.
Una interrupción de infraestructura sin resolver se detiene en el tiempo
observado. La media de un par utiliza el **horizonte común observado de las
repeticiones con datos para ese eje**, manteniendo n fijo para evitar cambios de población a mitad
de la gráfica. Las curvas individuales, cuando hay varias, se dibujan tenues
hasta su propio horizonte.

Las curvas por tokens y dólares terminan donde hay consumo registrado; no se
extrapolan a un presupuesto arbitrario mayor. Caché leída y razonamiento ya
forman parte de entrada y salida, respectivamente, y no se suman otra vez.
Una petición en vuelo puede no estar contabilizada al recibir un candidato.
El informe conserva consumo pendiente, desconocido, parcial y reportado por
un puente sin verificación upstream.

## Continuaciones y copias

Una evaluación lógica se identifica por el `run_id` de su raíz. Todas sus
continuaciones se concatenan automáticamente, incluso si sus presupuestos
restantes son inferiores al filtro temporal. Se suman tiempo activo, tokens
y dólares anteriores; los huecos sin ejecución no consumen el eje temporal.
Las curvas resultantes son continuas y cada cadena cuenta **una sola vez**.

Se guarda una representación unida estable en `evaluations/<root_run_id>.json`.
Este es el registro derivado que utiliza el comparador; no es necesario juntar
manualmente los directorios. Los segmentos originales, los motivos de corte,
el tipo de recuperación y los hashes de procedencia permanecen disponibles.
Así no se falsifica una ejecución continua cuando hubo restauración de archivos
sin conversación nativa, cambios de imagen o una pausa de varias horas.

Las copias del mismo run se excluyen tras comprobar que manifiesto, resumen,
eventos y metadatos de continuación tienen los mismos hashes. Copias
incompatibles, padres ausentes, ramas, ciclos, desfases de presupuesto o un sello
parental que no coincide producen un error explícito. Una bifurcación que
reutiliza trabajo previo no se cuenta como dos repeticiones independientes.

El análisis registra hashes de sus entradas canónicas y contrasta los sellos
parentales declarados. No vuelve a leer todos los blobs del workspace para
verificar una auditoría de integridad completa en cada regeneración.

## Cierre fallido con el presupuesto agotado

Una carpeta con manifiesto pero sin resumen final aparece explícitamente entre
las exclusiones. El estado `finalizing` por sí solo no permite incorporarla ni
dar por terminado un proceso que podría seguir vivo.

Después de comprobar que los procesos están detenidos, el operador puede guardar
un registro `results/comparison-recoveries/<run_id>.json`, fuera de los archivos
originales. El registro contiene `schema_version: 1`, `kind: comparison_recovery`,
`run_id`, `run_path` relativo a `results`, `processes_stopped: true`, una nota
explicativa y los SHA-256 de `manifest.json`, `status.json` y `events.jsonl` en
`source_sha256`. En `evidence` se conservan referencias relativas con `role`,
`path` y `sha256`; son obligatorios `stop_confirmation` y `best_revalidation`.
La confirmación de parada es una comprobación del operador respaldada por la
evidencia referenciada, no una deducción a partir de un estado antiguo.

Esta recuperación admite presupuestos temporales agotados: contrasta el estado
con los eventos de inicio, cancelación por tiempo y salida del agente. Reconstruye
tokens y veredictos desde el registro y comprueba la revalidación independiente
de la mejor solución con la misma imagen y los mismos bytes. Rechaza cambios de
hash, rutas fuera de `results` y enlaces simbólicos. No ejecuta código del agente.

La evaluación recuperada participa en las curvas y tablas con la marca
**cierre recuperado**. Cuenta como ejecución terminada por tiempo, conservando
que falló la finalización y que faltan el resumen y el sello originales. No se
crean sustitutos de esos archivos. Las interrupciones por proveedor conservan
su horizonte observado hasta que se incorpore una continuación terminada.

## Plantilla de precios

La plantilla editable es [`analysis/prices.yaml`](../analysis/prices.yaml), en
USD por millón de tokens. Se puede seleccionar otra con `--prices RUTA`.
Al cambiar tarifas se actualiza `as_of`; cada informe guarda una copia JSON y
el hash SHA-256 del YAML utilizado. La regeneración no descarga tarifas ni
edita el consumo original. Para conservar un análisis fechado, se selecciona
un directorio de salida nuevo en vez de sobrescribir `current`.

La plantilla inicial del **2 de noviembre de 2026**, ampliada el **4 de noviembre**
con Luna y el **5 de noviembre** con Fable 5.1 y Argon, contiene tarifas estándar
de entrada, lectura de caché, escritura corta/larga y salida, y los recargos de
contexto largo por petición cuando corresponden. Fuentes:

- [GPT-6.1 Sol](https://developers.openai.com/api/docs/models/gpt-6.1-sol),
  [GPT-6.1 Terra](https://developers.openai.com/api/docs/models/gpt-6.1-terra) y
  [GPT-6 Astra](https://developers.openai.com/api/docs/models/gpt-6-astra) y
  [GPT-6 Luna](https://developers.openai.com/api/docs/models/gpt-6-luna).
- [Tarifas de Claude](https://platform.claude.com/docs/en/about-claude/pricing).
- [Fable 5.1](https://platform.claude.com/docs/en/models/fable-5-1/overview).
- [Tarifas de Gemini Flash](https://ai.google.dev/gemini-api/docs/pricing#gemini-3.8-flash).
- [Tarifas de Argon](https://aistudio.google.com/docs/pricing#gemini-4-argon), con
  la promoción vigente hasta el 30 de noviembre de 2026. Los alias de Antigravity
  expresan una equivalencia de precio, sin acreditar la identidad upstream.

La duración de caché distingue las escrituras nativas de Claude de cinco
minutos y una hora. Las observaciones repetidas del mismo mensaje y la
reconciliación acumulada final no se cobran dos veces. Para una escritura sin
duración explícita se aplica la tarifa predeterminada y se indica la hipótesis.
Los nombres de modelos del puente Antigravity se relacionan explícitamente
con el modelo público mediante `aliases`; esto no acredita el modelo upstream.

Los importes son **equivalentes API**, incluso cuando la prueba usó suscripción.
No son facturas ni representan un coste real cero de Ollama o suscripciones.
Se excluyen hardware, impuestos, herramientas facturadas y almacenamiento de
caché. Una tarifa ausente no vale cero: se declara desconocida. Una observación
parcial contribuye solo con el uso conocido. Si no se puede calcular ningún
coste, la curva de precio del par queda no disponible. Se rechaza una plantilla
cuya fecha supere la validez de una tarifa promocional declarada.

## Tabla histórica

Se utiliza el leaderboard oficial final congelado del problema, almacenado en
`references/leaderboards/<problem>/official-final.json`. `--leaderboards` permite
seleccionar otro directorio de referencias del mismo formato. Las filas de
agentes se destacan y se intercalan por puntuación con las filas humanas.

Para cada par se muestran la posición de su media de máximos y la de su mejor
evaluación. Ambas se calculan **contra los equipos históricos exclusivamente**:

```text
puesto(score) = 1 + número de equipos históricos con score estrictamente superior
```

Los agentes no desplazan a otros agentes ni alteran las posiciones oficiales.
Un empate numérico no cuenta como superior. El informe mantiene la procedencia
de la tabla y declara la comparación contextual cuando las funciones objetivo
difieren. En GTOC 12 el benchmark usa masa sin coeficientes competitivos y el
histórico conserva puntuaciones ponderadas: la inserción numérica sigue
disponible, pero no es una equivalencia de objetivos o recursos.

## Archivos producidos y comprobaciones

```text
index.html                 informe compacto con gráficas y tablas
figures/*.png, *.svg        cuatro figuras por problema/presupuesto
figures/*-harnesses.*       figura adicional cuando un modelo tiene varios harness
comparison.json            datos, estadísticas, políticas y procedencia
prices.json                copia exacta de los valores y hash de la plantilla
summary.csv                medias, dispersión, máximos y puestos históricos
harnesses.csv              media y SD por modelo/harness, n, versiones y duración
coverage.csv               disponibilidad por par y eje, n y motivos de ausencia
progress.csv               todos los candidatos con ejes acumulados de la cadena
curves.csv                 media y SD en cada punto de las curvas
evaluations/<root_id>.json  cada evaluación lógica unida
```

Las regresiones cubren cadenas de tres segmentos, copias, ramas, ciclos,
padres ausentes, offsets, reconciliación de veredictos, SD muestral, horizontes
comunes, precios con caché y contexto largo, repetición de mensajes nativos,
reconciliación acumulada, repricing sin modificar las fuentes e independencia
del puesto histórico frente a otras filas agénticas. Una prueba CLI genera
los cuatro PNG/SVG, el HTML y los CSV con las conexiones de red bloqueadas y sin
API keys; comprueba que las entradas no cambian. Las regresiones verifican que
las políticas temporales y cambios de RAM comparten panel, que una tarifa
ausente siempre se explica y que las repeticiones utilizables no desaparecen.
La comparación de harness cubre n desiguales, continuaciones y copias, versiones,
filtros por modelo/razonamiento/presupuesto y exportación de la figura sin red.
