# Recuperación con pausa por indisponibilidad del proveedor

La ejecución SWE-agent + GPT-6.1 Sol de Spark1
`20261103T155706-1ad6c5e8c24f` terminó tras 10.688,12 segundos. El último
intento recibió HTTP 200 y un evento SSE `error` con código
`server_is_overloaded`. La conversión ChatGPT → Chat Completions aún no había
entregado la respuesta al agente, pero el controlador detuvo el presupuesto
como `provider_unavailable` y devolvió «ChatGPT response did not complete».
Fue una indisponibilidad del proveedor, no un error de la solución del agente.

El experimento se retiró posteriormente de los resultados porque otro defecto
de su imagen impedía ejecutar el validador oficial. Se conserva únicamente el
[registro técnico de la incidencia](../archive/incidents/validator-permissions-20261103/incident.json).
El fallo de proveedor sirve como caso de diagnóstico del transporte y no como
resultado de rendimiento del modelo.

El nuevo perfil `provider_wait` conserva esa petición pendiente y permite
recuperarse dentro de la misma ejecución y conversación. No modifica el
resultado histórico ni relanza aquel experimento.

### Incidencia de GTOC 13 en Dilithium

La continuación Codex + GPT-6.1 Sol/max `20261103T151645-199b61d4d693`
terminó por el mismo código. La petición `fcb643b955dd40db8adae747ab3f67bd`
recibió HTTP 200 y después un evento SSE `error` con
`error.type: service_unavailable_error`, `error.code: server_is_overloaded` y
`error.headers: {"retry-after": "302", "retry-after-ms": "302000"}`.
No fue una señal de cuota. El manifiesto conservaba `continuous_wall` y una
ventana de reintentos breves de 120 segundos; no incluía la pausa de una hora.

La recuperación posterior ya detectaba saturación en SSE, pero descartaba las
cabeceras incluidas en el evento. Ahora conserva y respeta esa espera, también
en eventos fragmentados, errores planos y `response.failed`. Cada error guarda
su clasificación y origen en `provider_stream_error` y en los metadatos de la
petición. Un mensaje textual dentro del evento ya no oculta su código de error.

## Configuración

### Generación lenta, keepalives y límite por intento

El timeout de lectura fijo de 60 segundos de la pasarela podía cancelar una
generación todavía pendiente. En la continuación Argon revisada por el usuario,
24 intentos con el mismo contenido terminaron cerca de ese plazo sin cabeceras;
otros seis se cortaron tras fragmentos seguidos de silencio. Los `499` del proxy
son compatibles con el cierre local. Esto identifica el límite inmediato,
pero no determina por qué el upstream dejó de aportar datos ni demuestra cuota
agotada o que todas esas generaciones fueran a terminar.

La política del POST de inferencia es ahora explícita y configurable:

```yaml
provider:
  # Mantener aquí los campos habituales de conexión y autenticación.
  timeouts:
    connect_seconds: 10
    read_seconds: 300
    attempt_seconds: 1800
  keepalive_seconds: 15  # Solo para provider.auth_method: antigravity.
```

`read_seconds` limita el silencio de transporte, también antes de las cabeceras.
`attempt_seconds` limita la duración total de un POST y su cuerpo con reloj
monotónico, aunque sigan llegando bytes. Ambos deben ser finitos y positivos;
el límite del experimento y la cancelación por tokens pueden cerrar la conexión
antes. El catálogo y la autenticación mantienen sus timeouts separados.

CLIProxyAPI está fijado a **8.0.54**, con los hashes de sus binarios. Se activan
`requests.streaming.keepalive-seconds`, `send-keepalive-before-ft` y
`requests.nonstream-keepalive-interval`, conforme a su
[configuración oficial](https://github.com/router-for-me/CLIProxyAPI/blob/v8.0.54/internal/config/sdk_config.go).
Así se mantienen vivas tanto las respuestas SSE como las JSON pendientes.
El intervalo debe ser menor que el timeout de lectura; cero lo desactiva de
forma explícita. Se mantienen una sola cuenta, cero reintentos ocultos del proxy
y las protecciones de las credenciales.

El constructor migra únicamente configuraciones antiguas o nuevas que coincidan
exactamente con las generadas por el benchmark, bajo la reserva exclusiva del
perfil. Solo cambia estos ajustes; no regenera claves ni requiere otro login.
Las modificaciones ajenas al perfil esperado siguen provocando rechazo.

Un keepalive demuestra actividad en el proxy, no progreso del modelo. No añade
tokens ni renueva el límite total. `first_byte_seconds` mide el primer byte de
transporte; `first_provider_event_seconds` mide el primer evento JSON del
protocolo e ignora comentarios SSE. Ese evento puede ser un handshake y tampoco
se presenta como un token generado. La auditoría registra contadores de
keepalives y el último evento del proveedor por separado.

Cada intento conserva sus límites efectivos y un evento `model_attempt_timeout`
con origen `benchmark_gateway`, fase (`connect`, cabeceras o cuerpo), límite y
silencio observado. Se distingue `model_read_timeout` de
`model_attempt_timeout`, se cierra la conexión y el consumo desconocido continúa
marcado como tal: el cierre local no certifica el cese de facturación upstream.
`provider-transport.json` queda sellado y enlazado desde la auditoría. Los
reintentos siguen la política de recuperación de este documento.

Las ejecuciones y despliegues congelados conservan su código y configuración.
La nueva política queda registrada al iniciar una ejecución con esta revisión;
los datos históricos no se reescriben. En una continuación cambian las
condiciones de transporte y deben interpretarse como tales.

```yaml
recovery:
  clock_policy: provider_wait
  max_transient_retries: 2
  max_retry_wait_seconds: 120
  provider_retry_interval_seconds: 300
  provider_wait_seconds: 3600
```

Es el perfil predeterminado de los nuevos ejemplos con pasarela. Un manifiesto
existente que declara `continuous_wall` mantiene su política: los archivos
sellados y las colas ya desplegadas no cambian al actualizar el repositorio.
Para usarlo en un servidor hay que desplegar el código y reconstruir la imagen
con la misma revisión de supervisor y controlador. No es una actualización en
caliente de experimentos en curso.

## Qué sucede al fallar

1. Los reintentos breves configurados siguen contabilizando tiempo activo.
   También se detectan errores transitorios dentro de respuestas HTTP 200,
   incluidos saturación, EOF y cortes de transporte antes de completar el turno.
2. Antes de entregar la respuesta al cliente, la pasarela comprueba que el
   intento terminó correctamente. Este perfil mantiene la respuesta en un
   búfer acotado de 16 MiB. Un turno fallido nunca entrega herramientas al
   agente: puede repetirse la petición sin ejecutar dos veces sus comandos.
   Cada intento y su posible consumo parcial siguen guardándose por separado.
3. Si sigue indisponible, se espera a que termine cualquier validación ya
   aceptada y se congela el contenedor CPU completo mediante el motor de
   contenedores. Solo después de confirmar la congelación se pausa el
   presupuesto. Se conservan procesos, memoria, archivos y conversación.
4. El controlador, fuera del contenedor, reintenta **la misma petición** cada
   300 segundos durante una ventana máxima de 3.600 segundos desde la primera
   pausa. El primer reintento largo es a los cinco minutos; no se envían más
   peticiones después de expirar la ventana. La espera mínima se calcula desde
   cada fallo como `max(provider_retry_interval_seconds, espera_del_proveedor)`.
   No se hacen inferencias adicionales para sondear disponibilidad ni se cambia
   de cuenta, clave, proveedor, modelo o razonamiento.
5. Al recibir cabeceras de una respuesta satisfactoria se reanuda el contenedor
   y vuelve a correr el reloj: el tiempo de inferencia de ese intento cuenta.
   Si el stream vuelve a fallar, se pausa de nuevo dentro de la misma ventana
   de recuperación. La ventana se considera resuelta al completar la respuesta;
   recibir cabeceras o keepalives no la prolonga. Si vence con un intento todavía
   abierto, también se cancela esa conexión.
6. Si expira la hora, se conserva `provider_unavailable`, el presupuesto activo
   restante, todas las soluciones y los intentos. El cierre termina los procesos
   y libera el contenedor siguiendo el procedimiento de finalización habitual.
   Codex conserva conversación y workspace en un checkpoint sellado para
   `gtoc resume`; después del cierre no se conserva memoria de procesos. Otros
   agentes declaran su disponibilidad de continuación en `checkpoint.json`.

### Política de `Retry-After`

Se aceptan segundos o fecha HTTP en `Retry-After`, milisegundos en
`retry-after-ms`, y los equivalentes estructurados dentro de `error.headers`
o `error.retry_after` / `error.retry_after_ms`. Para la cabecera HTTP se aplica
la semántica de [RFC 9110, sección 10.2.3](https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.3).
Si llegan varios mínimos válidos se utiliza el mayor. Valores malformados,
negativos o no finitos no se usan para planificar.

Los 120 segundos de `max_retry_wait_seconds` limitan solo los reintentos
breves: nunca recortan el mínimo indicado por el proveedor. Con intervalo de
300 segundos y un mínimo de 302, el siguiente intento espera al menos 302.
Si ese mínimo supera lo que queda de la ventana de recuperación, el contenedor
permanece congelado hasta agotarla y no se envía una petición prematura.

`provider-recovery.json`, incluido en `integrity.json`, conserva causa, estado,
valor y origen del mínimo, fecha UTC más temprana permitida, planificación y
resultado de recuperación. Al agotar la ventana queda `state: interrupted` y
`outcome: recovery_window_exhausted`; el resumen conserva el presupuesto activo
restante y `checkpoint.json` identifica la conversación recuperable. La orden
`gtoc resume` comprueba el sello y rechaza una continuación si todavía no ha
llegado el instante mínimo del proveedor. Se puede solicitar de nuevo después.
La planificación dentro del run usa duraciones monotónicas; la fecha UTC
permite conservar la restricción al cerrar y continuar en otro proceso.

Una recuperación resuelta conserva `state: recovered` y `outcome: recovered`;
una cancelación posterior de otra petición no sobrescribe ese episodio. El
motivo de cierre de la ejecución queda separado en `gateway-stop.json`, con
`scope: run_termination`. Ese registro también se sella y enlaza en la auditoría.
La [corrección observada en Astra](astra-gtoc13-corrections.md) detalla la
distinción y sus regresiones.

El reloj de tokens permanece activo durante la espera: cualquier uso reportado,
incluido el de intentos fallidos, se suma. Un límite de tokens, una cancelación
del operador o un fallo de infraestructura cancelan la espera sin aguardar al
próximo intervalo. Autenticación inválida, configuración incorrecta y cuotas
económicas explícitas siguen siendo terminales; no se intenta eludir límites.

## Tiempos, auditoría y límites

El estado y resumen añaden `budget_paused`, `provider_waiting`, `paused_seconds`
y `wall_elapsed_seconds`. `elapsed_seconds` y los eventos de progreso usan
tiempo activo. UTC y las duraciones de los intentos conservan la espera real.
Los eventos principales son `provider_wait_paused`, `provider_wait_started`,
`provider_wait_retry_scheduled`, `provider_wait_resumed`,
`provider_wait_recovered`, `provider_wait_exhausted` y `provider_stream_error`.
La planificación distingue la espera efectiva hasta el siguiente intento o
hasta la caducidad (`wait_seconds`), el mínimo completo requerido
(`minimum_retry_delay_seconds`) y si un intento cabe en la ventana
(`retry_within_window`). Esperar hasta la caducidad no autoriza un reintento.

El comparador distingue paneles de `continuous_wall` y `provider_wait`: no
promedia ambos perfiles temporales como repeticiones equivalentes. La pausa no
crea otro run ni otra repetición. El agente ve su presupuesto real mediante
`gtoc status --json`; las instrucciones iniciales explican esta política.

Los relojes del sistema operativo siguen avanzando dentro de un proceso
congelado. Los clientes configurables tienen timeouts de modelo ampliados para
que la espera no cierre la petición local. Codex utiliza
[`stream_idle_timeout_ms`](https://learn.chatgpt.com/docs/config-file/config-reference);
SWE-agent, mini-SWE-agent y OpenHands configuran el timeout de su cliente de
modelos. Una rutina propia del agente que use una fecha límite absoluta puede
tener que consultar de nuevo el presupuesto tras reanudarse.

Esta versión verifica la congelación en **contenedores CPU**. El freezer del
contenedor no garantiza detener kernels CUDA ya enviados a una GPU; si hay GPU
asignadas, se registra `provider_wait_unsupported` y se conserva la interrupción
en lugar de excluir cómputo GPU del tiempo de evaluación. La suscripción nativa
de Claude sigue usando los reintentos del cliente oficial y `continuous_wall`:
su tráfico de inferencia no pasa por esta pasarela. Las pruebas de integración
de recuperación cubren SWE-agent y Codex; otros clientes de pasarela comparten
el mecanismo y requieren verificar sus timeouts al incorporarse a una campaña.

Los procesos no pueden comprobar el heartbeat mientras están congelados. Al
volver se concede el intervalo normal del watchdog para leer el heartbeat
pendiente. Si el controlador desaparece durante la congelación, el contenedor
permanece detenido y no obtiene cómputo adicional; la recuperación operacional
debe confirmar su retirada antes de admitir otra evaluación.

## Verificación

### Pausa de Podman en Argon, 5 de noviembre de 2026

La continuación `20261104T230031-e48f2f703482` recibió HTTP 200, un fragmento
SSE y después 60 segundos sin datos. El timeout de transporte activó la
recuperación, pero `podman pause` no terminó en los 15 segundos asignados.
El benchmark cerró con `infrastructure_failure`, conservando 25.341,804 segundos
del presupuesto original. No hubo OOM ni evidencia de cuota agotada. Una
reproducción posterior con un contenedor sintético en el mismo servidor tardó
73 ms: la causa interna del bloqueo histórico sigue sin estar demostrada.

Se corrigen tres debilidades del benchmark: depender del retorno de la CLI sin
confirmar el estado real; no deshacer una congelación parcial al fallar; y
reanudar el reloj después de una confirmación que podía demorarse mientras los
procesos ya volvían a ejecutar.

- En Podman local con cgroup v2 delegado se escribe directamente en
  `cgroup.freeze` del contenedor y se espera `frozen=1` en `cgroup.events`.
  La vuelta confirma `frozen=0`. La ruta proviene del PID y del ID completo del
  contenedor, se valida que sea su grupo dedicado y se comprueba su identidad.
  Nunca se congela un grupo del usuario, del host o del controlador. El agente
  continúa viendo los cgroups de solo lectura.
- En otros entornos se usa el motor con plazos acotados: ante un error o timeout
  se consulta el estado y se admite una transición ya realizada; si no está
  confirmada se permite un segundo intento. El grupo de procesos de la CLI
  se termina al vencer el timeout. Nunca se pausa el presupuesto sin confirmación.
- Antes de la primera inferencia se comprueba un ciclo de congelación y vuelta.
  Un fallo aborta antes de iniciar el agente. Toda pausa fallida mantiene una
  obligación de descongelar para la finalización, incluso si no llegó a
  confirmarse. El reloj vuelve a correr antes de pedir la descongelación para
  contabilizar conservadoramente cualquier trabajo durante su confirmación.
- `freezer.json` y `freezer.jsonl`, sellados y enlazados en la auditoría,
  registran backend, preflight, duración, confirmación y errores. Ante un fallo
  del freezer nativo se añaden estado del kernel, presión de recursos y una
  muestra acotada de procesos y sus puntos de espera, sin argumentos ni secretos.

Se usa el mecanismo descrito en la
[documentación del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html):
solicitar la congelación y confirmar que terminó son pasos distintos. En el
backend directo el estado de pausa autoritativo es el del kernel y el benchmark;
no se depende del estado que haya almacenado Podman. Si el kernel tampoco puede
congelar o descongelar, se conserva una interrupción de infraestructura con
diagnósticos y el checkpoint disponible. No se oculta cómputo activo como espera.

La recuperación mantiene los cinco minutos entre reintentos y la ventana de
una hora. La corrección del freezer no cambió el timeout de inferencia; la
política de generación lenta descrita arriba lo sustituye tras el diagnóstico
de los cortes repetidos a los 60 segundos. No se sondea al modelo con peticiones
adicionales. Las pruebas incluyen Antigravity SDK 0.1.23
con un proveedor local que se queda sin enviar datos, además de saturación,
caducidad y preflight fallido sin consumo de modelo.

Las pruebas unitarias cubren saturación SSE con HTTP 200, conversión ChatGPT,
replay sin entrega de herramientas parciales, uso de tokens durante pausa,
caducidad, cancelación, cuotas y autenticación terminales, plazos de la cola
medidos en tiempo activo y el protocolo de congelación/reanudación.
El caso observado de Dilithium se reproduce con sus dos cabeceras y fragmentos
de 11 bytes, tanto en Responses como en la conversión ChatGPT. Una prueba de
planificación verifica los 302 segundos sin esperar cinco minutos reales.
También se comprueba que un mínimo mayor que la ventana no genere reintentos,
que se selle su estado y que la continuación respete el instante permitido.

Las pruebas Docker usan SWE-agent y Codex fijados, un proveedor HTTP local
simulado y un proceso de fondo que incrementa un contador. Durante una pausa
real de doce segundos el contador permanece inmóvil y el presupuesto no
avanza. La pausa se solicita mediante una cabecera de doce segundos dentro del
error SSE, con un intervalo local menor. Después se repite exactamente el mismo cuerpo de petición y el agente
termina correctamente. El intervalo supera el watchdog ordinario de diez
segundos para comprobar también la vuelta de la suspensión. No se utilizan
modelos externos, credenciales reales ni cuota de suscripción en estas pruebas.

La revisión del 4 de noviembre de 2026 pasó 643 pruebas unitarias (una omitida)
y cuatro pruebas Docker. El [registro de verificación](../results/provider-retry-verification-20261104/verification.json)
conserva los conteos JUnit, la imagen, los hashes del código probado y la
referencia a la incidencia original. La comprobación se realizó localmente;
Dilithium necesita una revisión coincidente de controlador e imagen antes de
usar esta recuperación en nuevas evaluaciones.
