# Correcciones de observabilidad y metadatos de Codex

Revisión: 2 de noviembre de 2026. Caso de referencia: ejecución
`20261031T204250-4f9cb597cf39`, Codex 0.164.0, `gpt-6.1-terra`, razonamiento
`max`, ocho horas. Se conserva la versión del agente; estas correcciones cambian
las condiciones de futuras ejecuciones y quedan identificadas por los hashes del
código y de la configuración resuelta.

## Tamaño final del workspace

**Causa confirmada.** Al detenerse el supervisor, un recorrido de archivos podía
salir anticipadamente y devolver su suma parcial como si fuera una medida
completa. La última suma sobrescribía las anteriores. El resumen publicó
164.483.392 bytes, frente a 1.452.871.673 bytes de archivos regulares en el
inventario final verificado: 15.904 archivos y ninguna discrepancia de hash.
La última muestra completa en ejecución era de 1.452.488.968 bytes.

**Impacto.** El dato final de espacio quedó subestimado. No cambió la validación
de candidatos ni la puntuación de 23.268,1 kg. El defecto también podía afectar
al control orientativo del tamaño del workspace cerca del cierre.

**Corrección.** `workspace_usage()` devuelve bytes, número de archivos,
completitud, cancelación, errores y duración. Solo una medición completa puede
sustituir la última medida completa. Una muestra cancelada conserva el valor
anterior y declara su procedencia. Después de detener los procesos se mide de
nuevo, ignorando la señal de cancelación; el controlador repite el recorrido
después de terminar y retirar el contenedor. `workspace-usage.json` conserva
el resultado de ese recorrido y la medida finalmente publicada. Si falla,
se conserva la anterior y se declara `final_scan_complete: false`.

La métrica es **tamaño lógico de archivos regulares**, incluidas instalaciones,
cachés y sesiones. No sigue enlaces simbólicos, no abre ni ejecuta su contenido
y cuenta cada ruta de un enlace duro. No equivale al espacio físico de `du`:
un archivo disperso cuenta su longitud lógica. Este recorrido no comparte el
límite de entradas del inventario de hashes. Durante la ejecución es una
muestra sobre archivos que pueden cambiar; después de detener el contenedor
se evita esa concurrencia.

## Auditoría HTML y capturas

**Causa confirmada.** El exportador expandía los descriptores de archivos de
todas las instantáneas en el HTML. Aunque el contenido ya se deduplicaba, los
metadatos completos se repetían en cada captura. El informe original llegó a
2.267.220.573 bytes. Hubo 2.201 instantáneas; 2.011 declararon truncamiento y
428 observaciones se omitieron, 420 por cola llena y ocho al cerrar.

**Impacto.** La vista era difícil de abrir y costosa de generar y almacenar.
Las capturas repetitivas aumentaban el trabajo del observador y la presión
sobre su cola. No se recuperan retrospectivamente los estados que no se
capturaron y no se atribuyen estas omisiones al agente.

**Corrección del informe.** `gtoc analyze` lee las trazas secuencialmente y genera
una vista compacta. Cada sección muestra hasta 200 registros, los primeros y
los últimos, con vistas previas acotadas. No carga ni expande manifiestos de
instantáneas. Enlaza JSON, JSONL y blobs originales; cuando se han archivado,
enlaza `evidence.tar.gz`. `exports/audit-summary.json` recoge los recuentos
completos y la cobertura de la vista. Todo texto del agente se escapa y el HTML
no ejecuta scripts.

Aplicado al caso de referencia: **851.638 bytes**, 1,38 segundos de generación
en la máquina local y 614 enlaces locales comprobados, ninguno roto. El pico de
memoria del proceso fue de unos 39 MB. Son medidas de esa exportación concreta,
no un límite universal. El HTML de 2,27 GB ya estaba archivado; la anterior
vista compactada manualmente ocupaba 9.288.875 bytes. No se reescribieron el
resumen, los eventos, el inventario ni el sellado originales.

**Corrección de las capturas.** Los nuevos manifiestos usan `schema_version: 2`:

- `layout: full` para la primera captura; `layout: delta` y `base_snapshot`
  para las siguientes. `files` contiene solo descriptores nuevos o cambiados;
  `removed` contiene las rutas eliminadas.
- El contenido se transmite y almacena una vez por hash. Los descriptores
  referencian blobs compartidos; no se escribe un JSON adicional por cada
  archivo de cada captura. El hash del contenido original y el del contenido
  redactado quedan diferenciados.
- Una caché de tamaño, identidad y fechas de modificación y cambio evita
  volver a leer y calcular el hash de archivos sin cambios. Los archivos
  grandes, binarios o ilegibles siguen declarando su omisión.
- Una enumeración incompleta conserva los archivos previamente conocidos y
  **no infiere eliminaciones**. `capture_complete` describe la enumeración;
  `truncated` también avisa de los límites de contenido. No se promete un
  respaldo completo del workspace.
- Hay una captura pendiente que agrupa solicitudes repetidas, con un máximo
  de 256 disparadores distintos. Conserva razones, recuentos, intervalo de
  solicitud y todos los identificadores de envío; cada envío recibe su enlace.
  Saturación, cancelación o cierre dejan una omisión explícita.

La captura es asíncrona y no detiene al agente. Varios envíos pueden compartir
una observación posterior; esto no demuestra que compartan código ni recupera
estados intermedios. Se mantienen las exclusiones de cachés, sesiones y entornos;
el límite es de 256 KiB por archivo de texto, 4 MiB de archivos leídos sin caché por captura
y 20.000 entradas examinadas. El contenido sin cambios se reutiliza sin
consumir ese límite de lectura. El inventario final de hashes es independiente.

Para reconstruir una vista bajo demanda, sin ejecutar el código del agente:

```python
from gtoc_bench.snapshots import read_snapshot

view = read_snapshot(run_directory, "snapshots/ID/manifest.json")
files = view["files"]  # Mapa ruta -> descriptor, con sus omisiones y blobs.
```

El lector admite también los manifiestos completos históricos y sus descriptores
por archivo. Rechaza referencias fuera de `snapshots`, enlaces simbólicos,
ciclos y cadenas excesivas. No descomprime automáticamente archivos de evidencia.

## Compatibilidad de metadatos del modelo

**Problema reproducido.** El catálogo incorporado en Codex 0.164.0 no incluye
`gpt-6.1-terra`. El cliente acepta el nombre pero avisa de que usa metadatos
de respaldo. En la ejecución histórica las peticiones confirmaron Terra/max;
ese aviso no significa que se usara otro modelo. No se conoce su efecto sobre
la gestión del contexto ni puede deducirse su impacto en la puntuación.

**Corrección.** Antes de iniciar el presupuesto y exponer el problema, el
preflight consulta el binario fijado mediante `app-server`: `initialize`,
`model/list` y `thread/start` efímero. No envía `turn/start`, no genera tokens
y utiliza un perfil temporal sin cuentas ni conversaciones. Comprueba el
nombre exacto, el razonamiento soportado y la resolución efectiva. La ausencia
de metadatos o un aviso de respaldo para el modelo solicitado impiden empezar;
`model-compatibility.json` conserva el motivo y los diagnósticos. Un aviso de
un modelo auxiliar diferente queda registrado sin confundirse con el principal;
la pasarela sigue bloqueando inferencias de modelos no autorizados.

Con ChatGPT se utiliza el `ModelInfo` completo obtenido por el preflight del
proveedor para construir `codex-model-catalog.json`. La copia que usa el agente
está protegida fuera del workspace. Se comprueba la versión mínima del cliente
y se registran la fuente, el hash, la ventana de contexto y la política de
compactación. El preflight y el agente utilizan la misma configuración.
Un listado API que solo contenga nombres no sustituye al catálogo de Codex.

Otros proveedores pueden aportar explícitamente un catálogo compatible con la
versión fijada, sin claves, mediante una ruta relativa al YAML o absoluta:

```yaml
model:
  id: gpt-6.1-terra
  reasoning: max
  catalog_file: ../models/codex-models.json
```

Su formato es `{"models": [ModelInfo, ...]}`; no basta con inventar una ventana
de contexto. El catálogo debe proceder de una fuente comprobada y contener los
campos que requiere ese cliente. Si no se aporta uno, se comprueba el catálogo
incorporado y se rechaza el modelo si no está reconocido.

En la cuenta de esta prueba el proveedor declaraba `context_window: 500000`,
`max_context_window: 872000` y `auto_compact_token_limit: null`. Se configura
la ventana recomendada de 500.000; el máximo anunciado no se usa para ampliarla.
El valor nulo de compactación conserva el predeterminado del cliente fijado y
se registra como tal; **no se inventa un umbral numérico** ni se sustituye por
las especificaciones de otro producto o modalidad de acceso.

La comprobación offline con el binario oficial 0.164.0 reproduce la ausencia de
Terra sin catálogo y confirma Terra/max con catálogo. Una respuesta simulada
confirma también que `/v1/responses` conserva ese modelo y razonamiento y que
la pasarela admite el cuerpo producido por el cliente. Estas verificaciones
no usaron inferencias de proveedores externos ni cuotas de suscripción.

Referencias oficiales: [configuración de Codex](https://learn.chatgpt.com/docs/config-file/config-reference)
(`model_catalog_json`, `model_context_window`, `model_auto_compact_token_limit`)
y [protocolo app-server](https://learn.chatgpt.com/docs/app-server).

## Verificación y alcance histórico

Las regresiones cubren cancelación a mitad de recorrido, retención de la
última medida completa, recuento final de archivos grandes, agrupación de
solicitudes, edición/eliminación/reversión de archivos, redactado, reconstrucción
de deltas, límite de lectura incluso para binarios, compatibilidad histórica y
HTML acotado sin lectura de manifiestos.
Se comprueban además el rechazo de metadatos de respaldo y la configuración
del cliente real fijado. Las pruebas de contenedor utilizan proveedores
simulados.

La suite local completa terminó con 502 pruebas aprobadas y una omitida
(la prueba opcional del binario real, ejecutada después por separado). La
verificación con el Codex oficial fijado pasó sus siete casos; las regresiones
de observación y validación, incluido un caso añadido después de iniciar la
suite, pasaron sus 17 casos. Dos pruebas de integración Docker comprobaron las
herramientas, el validador y el corte temporal. Una comprobación adicional de
Terra/max en Linux confirmó dos peticiones al proveedor simulado, metadatos
reconocidos y 21.500.929 bytes finales coincidentes con el inventario. No hubo
llamadas de inferencia a proveedores externos. Ruff pasó en código y pruebas.

Los resultados de verificación locales se guardan en
`results/terra-audit-fixes-20261102/`. La auditoría derivada se ha regenerado en
la ejecución de referencia. Su resumen sellado sigue mostrando el dato
histórico incorrecto; el informe presenta aparte los 1.452.871.673 bytes del
inventario. Parte del workspace y las capturas están archivados: medir solo
los archivos que hoy quedan fuera del archivo comprimido no reconstruye el
tamaño original. No se ha repetido ni alterado el experimento.

Las imágenes ya construidas conservan su código anterior. Antes de una nueva
ejecución hay que construir y seleccionar una imagen actualizada en el servidor,
por ejemplo `uv run gtoc build --cpu --agent codex --tag gtoc-bench:codex-audit-v2`
para Spark bajo QEMU. La versión 0.164.0 del agente se mantiene. La validación
local utilizó una capa de código sobre la imagen científica existente, sin
reinstalar las bibliotecas; no se modificó ningún servidor ni experimento activo.
