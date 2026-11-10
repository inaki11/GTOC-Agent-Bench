# Consumo de Claude Code por respuesta

El cliente oficial con suscripción emitía un evento `assistant` por bloque de
contenido. Su `usage.output_tokens` podía seguir siendo el contador provisional
del inicio de respuesta. El benchmark lo etiquetaba como `reported` y recuperaba
la diferencia con `result.modelUsage` al terminar. El total podía ser correcto,
pero la curva colocaba esa diferencia al final. Sin `result`, faltaba salida.

La corrección mantiene Claude Code 2.1.308 y añade
`--include-partial-messages`. No cambia el modelo, el razonamiento ni la
velocidad. Usa los eventos oficiales:

- `message_start`: registra entrada, caché y la salida provisional.
- `message_delta`: incorpora los contadores acumulados de esa respuesta.
- `message_stop`: confirma el consumo si llegó el contador de salida con un
  motivo de finalización. Un cierre sin ese dato mantiene `quality: partial`.
- `assistant`: conserva el contenido; un bloque sin `stop_reason` no certifica
  el consumo. Los mensajes repetidos se deduplican por ID y sus contadores no
  pueden retroceder.
- `result.modelUsage`: contrasta los totales y conserva cualquier diferencia.
  Una corrección acumulada se marca `temporal_quality: unattributed`; no se
  inventan respuestas ni tiempos para repartirla.

Los eventos `model_request_finished` son observaciones de consumo y pueden
repetir un `request_id`: sus contadores superiores son **incrementos**, mientras
que `raw` conserva los contadores acumulados de la respuesta. `usage.json`
conserva la última observación completa o parcial. El esquema es
`accounting_schema: 2`. El registro del consumo y la recepción de un candidato
comparten el bloqueo del almacén para evitar observaciones a medio persistir.

`native-usage.json`, sellado y enlazado desde la auditoría, distingue:

- `reported_responses` y `pending_responses`: respuestas confirmadas o pendientes.
- `session_totals_received`: presencia del resumen acumulado.
- `unattributed_input_tokens` / `unattributed_output_tokens`: diferencias cuya
  respuesta no pudo localizarse.
- `total_quality` frente a `timeline_quality`: conocer el total no implica poder
  reconstruir su distribución.
- `counter_regressions`: discrepancias si el resumen declara menos consumo que
  el ya observado; se conservan ambos datos y no se descartan tokens.

`unknown_usage_requests` conserva su significado de fallos con consumo
desconocido; no cuenta los bloques provisionales ni sustituye a
`pending_responses`. El estado y los recibos incluyen el contador de respuestas
pendientes. Una interrupción conserva los contadores ya observados aunque no
llegue el resumen final. La salida de una respuesta aún en generación puede
seguir siendo parcial hasta recibir sus eventos de uso; no se estima a partir
del texto ni se sondea al proveedor.

Las trazas guardan los eventos de uso y los bloques completos del cliente.
Se omiten los nuevos deltas de texto y de argumentos de herramientas porque
repiten esos bloques; así, habilitar el stream no multiplica la auditoría.

## Informes y resultados anteriores

Las curvas nuevas reflejan el consumo como máximo al terminar cada respuesta,
antes de ejecutar sus herramientas. No pretenden localizar cada token dentro
de la generación. Las tarifas por contexto largo usan la entrada completa de
la respuesta incluso cuando una observación posterior solo añade salida.

El comparador detecta los registros antiguos del adaptador nativo sin esquema 2
y las correcciones acumuladas sin atribución. Mantiene sus totales conocidos,
puntuaciones, ranking y curvas temporales, y excluye esas ejecuciones de las
curvas de eficiencia por tokens y precio, indicando el motivo. No reparte la
salida del cierre hacia atrás con una estimación sin evidencia. Los informes
existentes deben regenerarse con `gtoc compare` para aplicar esta clasificación;
no se modifican eventos, recibos ni sellos históricos.

Referencias oficiales: [flujo de mensajes de Claude Code](https://code.claude.com/docs/en/agent-sdk/streaming-output)
y [contadores acumulados del stream](https://platform.claude.com/docs/en/build-with-claude/streaming).

La regresión se comprueba con streams simulados, cierres incompletos, mensajes
repetidos, reconciliaciones y el cliente oficial contra un proveedor local
determinista, sin consumir cuota de modelos.
