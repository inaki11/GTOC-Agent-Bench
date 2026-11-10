# Almacenamiento durante las ejecuciones

El HTML compacto evita expandir miles de instantáneas al analizar una ejecución.
Eso no limita el espacio ocupado durante la búsqueda. Crecen por separado el
workspace del agente, las peticiones con su contexto, las respuestas, los
candidatos, las instantáneas y las sesiones nativas. Las continuaciones conservan
copias independientes del workspace y de su evidencia; las imágenes de Docker o
Podman comparten capas, pero sus construcciones y versiones antiguas también
consumen cuota del usuario.

## Qué mostró la revisión de Dilithium

La última continuación de Codex + Sol en GTOC13 disponible en el servidor era
`20261104T014916-98180427e4ea`. No registró `ENOSPC` ni `EDQUOT`: terminó con
`Controller heartbeat lost`, después de alcanzar el límite de **40 GiB de RAM**.
No hubo `oom_kill`. La presión de memoria es una causa probable del retraso del
supervisor, no una prueba de agotamiento del disco. El workspace tenía unos
734 MiB, las peticiones 81 MiB y las instantáneas 59 MiB. Aumentar la cuota de
disco no corrige ese límite de RAM; hay que dimensionar memoria y workers por
separado. El prompt y `gtoc status --json` explicitan ahora ambos recursos.

El usuario tenía unos 15 GiB ocupados, de los cuales 9,7 GiB correspondían al
almacén de Podman y 4,6 GiB a despliegues con resultados. `quota` declaraba un
límite blando de **15 GiB** y uno duro de **40 GiB**. Rebasar el blando activa
un plazo de gracia; al expirar, el límite efectivo vuelve a ser el blando. Para
operar habitualmente cerca de 40 GiB conviene que sistemas ajuste también el
límite blando. El disco global tenía unos 234 GiB libres: ese dato por sí solo
no representa la cuota disponible para el usuario.

## Compresión durante la generación

Las nuevas peticiones se guardan desde el inicio como `request.manifest.json`
y fragmentos JSON comprimidos con gzip nivel 3 en `requests/blobs/`. Los mensajes
de `input` o `messages` y el resto del cuerpo se identifican por SHA-256. Los
contextos idénticos de turnos sucesivos comparten fragmentos dentro de la misma
ejecución; se conserva su orden y todos sus valores. El lector verifica cada
fragmento y el hash del cuerpo reconstruido. No se comparten datos entre runs.
Los cuerpos originales del cliente casi siempre
solo difieren en campos superiores como razonamiento o modalidad de servicio.
En ese caso `client_request.json` es un overlay pequeño que conserva exactamente
las diferencias respecto al cuerpo enviado. Si la conversión de protocolo
cambia todo el cuerpo se guarda un `client_request.manifest.json`, que puede
compartir los mismos mensajes sin duplicarlos.
`metadata.json` declara rutas, formato, tamaños y hash del cuerpo efectivo.

Las respuestas se escriben directamente como `response.jsonl.gz`, con flush y
sincronización periódicos durante la inferencia. Se conserva el límite original
de bytes lógicos de la traza. El lector acepta tanto JSONL antiguo como gzip,
incluidos registros completos sincronizados antes de un cierre abrupto. La
redacción se aplica **antes** de comprimir y el sello verifica los bytes
almacenados. No se omite contenido para reducir espacio ni se cambian peticiones
al modelo. Los metadatos, veredictos y el índice principal siguen en JSON legible.

Cada candidato aceptado se guarda inmediatamente como `solution.txt.gz`.
Se comprimen sus bytes originales, sin redondear números ni cambiar espacios o
saltos de línea. El juez recibe esos mismos bytes sin comprimir. El recibo
declara ruta, tamaños y hash del original; `integrity.json` sella el gzip.

```sh
gtoc inspect-request /ruta/run/requests/ID
gtoc inspect-request /ruta/run/requests/ID --client
gtoc inspect-submission /ruta/run/submissions/ID --output /ruta/candidato.txt
gzip -dc /ruta/run/requests/ID/response.jsonl.gz
```

Las lecturas y análisis siguen admitiendo los resultados antiguos. No se
reescriben sus archivos sellados. El workspace y la sesión nativa en uso no se
comprimen ni se eliminan: el agente debe poder seguir accediendo a ellos.

## Reservas de admisión y espacio real disponible

La ejecución GTOC11 `20261105T212505-631aec663bb0` se interrumpió a los
12.136,1 segundos por el antiguo límite de 2 GiB de auditoría, con unos 3,44 TB
libres. Los candidatos ocupaban 987,9 MB y las peticiones 981,5 MB: comprimir solo
el HTML posterior no podía evitarlo. Ese límite se elimina. La auditoría puede
superar su reserva estimada sin cancelar la prueba.

```yaml
hardware:
  cpus: 20
  memory_gb: 96
  gpus: none
  workspace_gb: 4
storage:
  audit_reserve_gb: 2
  minimum_free_gb: 2
```

Los 96 GiB son RAM por contenedor; no cuentan como 96 GiB de cuota de disco.
Son un ejemplo para el servidor observado, no una garantía de que cualquier
solución pueda abrir un número arbitrario de workers. Cada prueba debe declarar
los otros runs en `hardware.concurrent_with`, según la autorización de compartir
máquina. No se inicia una campaña como parte de esta revisión.

`configs/codex-gtoc13-max-8h-storage40g.yaml` recoge ese perfil con Codex + Sol/max,
suscripción ChatGPT, ocho horas y sin límite de tokens. Se deben seleccionar
perfil de autenticación y peers al preparar una campaña; crear el archivo no
inicia ninguna inferencia ni cambia las condiciones de los resultados antiguos.

Con 15 GiB de base, tres límites de 4 GiB de workspace + reservas estimadas de
2 GiB de auditoría y 2 GiB de margen común suman **35 GiB estimados**. Esto no
garantiza que tres pruebas arbitrarias quepan en 40 GiB: la auditoría no tiene
un máximo. El espacio ya usado por resultados previos cuenta en la
base: habrá que archivarlos fuera de la cuota antes de campañas sucesivas.
Las continuaciones no son repeticiones independientes y sus copias antiguas
también cuentan. No se borran ni podan imágenes o resultados automáticamente.

`storage.audit_reserve_gb` solo estima lo que necesitará la auditoría al admitir
una ejecución; no es un límite ni una reserva física. Los perfiles antiguos
con `storage.audit_gb` se aceptan como esa misma estimación, también en las
configuraciones en cola. No existe parada por `audit_budget` en el código nuevo.

El arranque guarda `storage-preflight.json` y exige margen para su workspace,
estimación de auditoría y reservas declaradas por los contenedores activos. La admisión se
serializa con el bloqueo ya existente, antes de cualquier inferencia. Cada diez
segundos se compara el espacio libre global con la cuota del usuario en Linux,
y se mide la auditoría excluyendo workspace y symlinks. `storage-status.json`
conserva ambas cifras, `audit_limit_bytes: null` y `audit_limit_enforced: false`.
Solo si se reduce el espacio realmente disponible por debajo de
`storage.minimum_free_gb` se registra `storage_pressure` con causa
`user_quota_or_disk_margin`, se cancela la ejecución y se
preservan sus resultados y presupuesto restante. Los dos JSON quedan sellados y
enlazados desde la auditoría.

El monitor no es una cuota de filesystem por run: un proceso que escriba muchos
GB entre observaciones puede agotar el margen antes del siguiente control. Para garantizar
topes estrictos frente a escrituras hostiles hacen falta cuotas de proyecto o
volúmenes limitados administrados por el servidor. Si no hay una herramienta de
cuota compatible, el preflight lo declara y usa el espacio global, sin afirmar
que conoce la cuota del usuario. Las reservas tampoco abarcan archivos que
otros procesos del usuario creen fuera del benchmark.
