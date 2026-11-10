# RAM, heartbeat y recuperación

## Incidencia y nivel de certeza

El segmento `20261104T014916-98180427e4ea` de Codex + Sol/max en GTOC13
alcanzó `memory.max = 40 GiB`. La última muestra tuvo 39,9998 GiB y
`memory.events.max = 56`, con 27 procesos Python del agente. Varios workers
tenían entre 2 y 5 GiB de RSS. Esos RSS no se suman para estimar consumo real:
pueden compartir páginas; la cifra relevante es la del cgroup.

No hubo `oom_kill`. El corte confirmado fue `Controller heartbeat lost`, unos
25 segundos después de la primera observación del límite de RAM. Esto prueba
presión de memoria y pérdida de heartbeat; no permite demostrar retrospectivamente
qué hilo, bloqueo o escritura concreta retrasó aquel latido.

Dos debilidades del benchmark sí son confirmadas: el heartbeat utilizaba el
mismo pipe y lock que las respuestas del modelo; y la vigilancia de recursos
dependía del supervisor dentro del mismo límite de RAM que los optimizadores.
La presión de memoria podía retrasar el observador y el canal que debía
demostrar que el controlador seguía vivo. Un timeout de 10 segundos terminaba
entonces toda la ejecución con una causa poco informativa.

## Decisión

Se compararon tres opciones:

- Dar más RAM y avisar al agente: ayuda a esta búsqueda, pero no corrige la
  dependencia del latido ni controla futuros excesos.
- Subcgroups con reserva estricta para el supervisor: ofrecen aislamiento
  más fuerte, pero requieren delegación de cgroups y asignar correctamente todos
  los procesos antes de que asignen memoria. La delegación difiere entre Docker
  y Podman sin privilegios. Se conserva como posible perfil específico de Linux.
- Separar el latido y vigilar desde el host con margen de cierre: mantiene un
  contenedor, funciona con ambos motores y deja una causa verificable.

Se implementa la tercera opción, junto con instrucciones claras y una asignación
de RAM mayor para la continuación autorizada. No se añaden privilegios ni un
montaje escribible de los cgroups del host. Es una protección supervisada con
corte preventivo; `memory.max` sigue siendo el límite duro del kernel. No es una
reserva de RAM garantizada frente a una asignación instantánea arbitrariamente
grande. `supervision.json` declara expresamente `hard_reservation: false`.

La [documentación del kernel](https://docs.kernel.org/admin-guide/cgroup-v2.html)
define el consumo del cgroup, sus eventos de OOM y la protección jerárquica.
Mover un proceso después de que haya asignado memoria no migra esas páginas;
por ello no se presenta una migración en caliente como aislamiento retroactivo.

## Implementación

El controlador actualiza cada segundo un contador en un directorio privado,
montado **de solo lectura** en `/run/gtoc-control`. No contiene credenciales.
El supervisor comprueba que ese montaje sea de solo lectura. El contador se
observa con un reloj monotónico local: no se comparan relojes del portátil y
de la VM. Una respuesta grande o un bloqueo del pipe no impide renovar el
contador. Si no avanza durante 15 segundos, el supervisor mata los procesos del
agente y conserva edad y canal en el diagnóstico. El hilo de heartbeat del
cliente nativo de Claude también se separa del hilo de este contador.

En Spark (host ARM con contenedor x86 bajo QEMU), la revisión del 5 de noviembre
observó un bind mount `ro` confirmado en `/proc/self/mountinfo` y un rechazo
`EROFS` al crear un archivo, mientras `statvfs().f_flag` devolvía 32 sin
`ST_RDONLY`. La comprobación inicial interpretaba ese indicador como un montaje
escribible y abortaba antes del modelo. Ahora se prueba la protección efectiva:
se intenta crear un archivo de prueba con nombre único dentro del directorio
privado y solo se acepta el error `EROFS`. Un permiso insuficiente no se acepta
como prueba de solo lectura. Si el montaje permite crear el archivo, se elimina
y se rechaza el arranque. No se modifica el heartbeat ni se añaden privilegios.

Un guardián independiente del controlador, fuera del cgroup del contenedor,
lee `memory.current`, `memory.stat`, `memory.max`, `memory.events` y PSI cuando
Linux permite observar el cgroup. En Docker Desktop usa estadísticas del motor
y declara esa fuente. Descuenta `inactive_file` para no cortar solamente por
caché de archivos recuperable. No usa la suma del RSS de los workers.

- Al 75% del conjunto de memoria en uso registra `memory_pressure_warning`.
- Al 90%, o si observa un OOM kill, cancela inferencia y termina los procesos
  UID 1000 mediante un helper de confianza del motor, sin esperar al pipe.
  Si ese helper no puede arrancar, solicita la terminación del contenedor.
- El resultado es `state: interrupted`, `stop_reason: memory_limit`, con el
  presupuesto restante, los candidatos y el checkpoint que esté disponible.
  No se atribuye al proveedor ni se disfraza como error de heartbeat.

El tiempo y los tokens no se reinician. `memory.jsonl` conserva observaciones;
ambos archivos se sellan y se enlazan desde la auditoría. `gtoc status --json`
muestra `memory_limit_bytes`, `memory.working_set_bytes` y
`memory.working_set_fraction`. El prompt pide dimensionar el número de workers
y su consumo para mantenerse por debajo del aviso. Con 96 GiB, el corte
preventivo está en unos 86,4 GiB de conjunto en uso; la caché inactiva no cuenta
en esa cifra, aunque sí está bajo el límite duro del contenedor.

La admisión suma las RAM declaradas de los contenedores simultáneos y deja un
10% del host sin asignar. Los perfiles compartidos deben declarar su RAM: no se
asigna el 90% del servidor a cada prueba por separado.

## Continuar con más RAM

```sh
gtoc resume /ruta/al/padre --config configuracion.yaml --memory-gb 96 --detach
```

La opción requiere una continuación explícita y solo permite cambiar la RAM;
CPU, GPU, modelo, razonamiento y materiales siguen comprobándose. Conserva
sesión nativa, workspace, mejor candidato y tiempo/tokens restantes. No restaura
los procesos del optimizador ni sus datos no guardados en RAM.

`continuation.json` registra el valor anterior y nuevo y marca los recursos como
heterogéneos. Las gráficas mantienen unida la conversación completa, pero separan
ese perfil de las repeticiones con RAM constante. Una modificación del reloj
entre segmentos también se presenta como perfil temporal mixto.

La reparación operacional del experimento en Dilithium debe detener y sellar
la continuación antigua antes de restaurarla con el nuevo controlador e imagen.
El hueco sin cómputo no consume el presupuesto original de ocho horas. No se
modifican los artefactos históricos ni se presenta el nuevo tramo como una
repetición independiente.
