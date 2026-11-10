# GTOC-Bench

GTOC-Bench mide cómo avanza un agente de programación al intentar resolver un
problema de optimización. Una **ejecución** (*run*) combina un agente, un modelo,
una máquina y un presupuesto de tiempo; también puede tener un límite orientativo
de tokens. El agente propone soluciones, un validador calcula su puntuación y el
bench guarda **cada intento**, no solo el mejor. Esto permite estudiar la
puntuación alcanzada y cómo mejora con el tiempo y los tokens consumidos.

Los ejercicios disponibles son **GTOCX, GTOC11, GTOC12 y GTOC13**. Los agentes disponibles son
**Codex, Claude Code, OpenHands, SWE-agent, mini-SWE-agent y Antigravity SDK**. El agente es el programa que lee archivos, escribe
código y ejecuta herramientas. El modelo de lenguaje es el servicio que le
responde y le ayuda a decidir qué hacer. Se configuran por separado, siempre
que hablen un protocolo compatible.

## Cómo funciona una ejecución

1. El **controlador** se ejecuta en la máquina de evaluación, sea local o un
   servidor al que se accede por SSH. Prepara un único contenedor Docker, lleva
   el reloj, aplica los límites y guarda los resultados. Si se inicia de forma
   desacoplada, continúa trabajando aunque se cierre la conexión SSH.
2. Dentro del **contenedor** se ejecutan las herramientas y el código del agente. Puede
   usar las CPU y GPU asignadas, las bibliotecas instaladas y su propio código
   para buscar soluciones. El contenedor no tiene acceso directo a Internet ni
   recibe las claves reales de los proveedores.
3. En las modalidades API key y ChatGPT, el agente también reside dentro del contenedor
   y consulta al modelo a través de una dirección local.
   La **pasarela**, que forma parte del controlador, comprueba la petición, la
   envía al proveedor con la credencial guardada fuera del contenedor y
   registra el consumo. También facilita acceso limitado a documentación y
   paquetes aprobados.
   Con suscripción de Claude, su cliente oficial reside en el host, gestiona la
   autenticación y consulta directamente al proveedor; sus herramientas se
   limitan a un MCP que ejecuta comandos dentro del contenedor.
4. Cuando el agente tiene una solución, ejecuta `gtoc evaluate`. El controlador
   guarda una copia y pide al **validador** que calcule su puntuación dentro del
   contenedor, con permisos separados. El agente recibe el veredicto y puede
   seguir mejorando la solución hasta agotar el presupuesto.
5. Al terminar, el controlador detiene el contenedor y deja los candidatos,
   veredictos, tiempos, tokens y configuración en archivos que se pueden
   inspeccionar y comparar.

**Coste de inferencia:** puedes usar claves de API de OpenAI o Anthropic, o
autorizar el uso de tu **suscripción a ChatGPT para los seis agentes** o de **Claude para
Claude Code oficial**. Cada modalidad usa
los límites y la facturación de su cuenta; una suscripción no significa consumo
ilimitado. Las pruebas automatizadas
utilizan proveedores simulados y no consumen una API de pago.

Esta es una primera implementación del [diseño](DESIGN.md), destinada a pruebas
de desarrollo. El código del controlador es nuevo. Del legado se han importado
el enunciado, los datos y los dos archivos necesarios del validador de GTOC12.
GTOC13 incluye los documentos originales, las tres efemérides y un validador
independiente con reconstrucción de controles; no utiliza código propietario del host.
GTOC11 utiliza el ejecutable original recibido de sus organizadores y el catálogo
completo, con una capa de comprobación del formato y de la salida del programa.

## Ejercicio GTOCX

[GTOCX — Settlers of the Galaxy](problems/gtocx/instructions.md) incluye los dos
PDF originales, el catálogo completo de 100.001 estrellas, las aclaraciones del
organizador y un validador/evaluador independiente. Comprueba toda la estructura
de asentamientos, las trayectorias, los impulsos, las esperas y los límites
de distancia al centro galáctico. El bonus temporal queda fijado a **B=1**.
Los rechazos incluyen causa y línea; una solución no verificable no recibe puntos.

```sh
uv run gtoc validate --problem gtocx --local /ruta/solucion.txt
uv run python -m gtoc_bench.gtocx /ruta/solucion.txt
uv run gtoc build --cpu --tag gtoc-bench:gtocx-cpu
uv run gtoc run configs/codex-gtocx.yaml
```

El último comando inicia una ejecución con el proveedor configurado. La imagen
incluye los cuatro ejercicios; los otros agentes también admiten `problem: gtocx`
con una imagen actualizada. Las [12 entregas públicas de contraste](references/gtocx/README.md)
y los leaderboards quedan fuera de Docker. El [informe de validación](docs/gtocx-validation.md)
detalla los resultados y los límites de la comprobación.

## Ejercicio GTOC11

[GTOC11 — Dyson Sphere Building](problems/gtoc11/instructions.md) incluye el
catálogo completo de **83.453 asteroides**, el enunciado matemático con las
correcciones finales, el formato de entrega y el **validador original de Linux
enviado por los organizadores**, sin modificar. La puntuación se calcula con
**B=1**, sin bonus por fecha de envío. La integración conserva los mensajes
originales de rechazo y sus líneas, comprueba el resultado escrito por el
programa y contrasta la puntuación con un cálculo independiente del mérito.

```sh
uv run gtoc build --cpu --tag gtoc-bench:gtoc11-cpu
uv run gtoc validate --problem gtoc11 --image gtoc-bench:gtoc11-cpu /ruta/solucion.txt
uv run gtoc run configs/codex-gtoc11.yaml
```

El programa requiere **Linux amd64**; Docker permite usarlo en macOS. En Linux
amd64 también se puede usar `gtoc validate --problem gtoc11 --local ARCHIVO`.
Durante una ejecución, el agente utiliza `gtoc evaluate ARCHIVO` y el bench se
encarga del nombre `Solution.txt` y el directorio privado exigidos por el juez.
Los otros agentes admiten `problem: gtoc11` con una imagen actualizada.

La solución completa de ejemplo recibida se acepta con **1556,5911 puntos y
148 asteroides**. Se conserva como referencia del host, junto al ejecutable de
Windows y las instrucciones recibidas; no se entrega al agente. El valor final
de pérdida de masa es `alpha=6e-9 s^-1`; el PDF inicial recuperado usaba un valor
anterior y queda fuera del contenedor. El [informe de comprobaciones](docs/gtoc11-validation.md)
documenta los ensayos del ejecutable y los límites de la evidencia disponible.

## Ejercicio GTOC13

Los materiales están en [`problems/gtoc13`](problems/gtoc13/instructions.md).
La [política de validación](problems/gtoc13/validation-policy.md) explica las
diferencias respecto al host: reconstrucción de controles interiores de vela,
comprobaciones numéricas independientes y tolerancias relativas por intervalo.
La revisión v2 acepta el archivo Antipodes completo, además de KAIST; la revisión
v1 y sus resultados permanecen documentados como históricos.
Se fija el bonus temporal a **c = 1**. Una solución rechazada o no verificable
no recibe puntuación. Los mensajes incluyen causa y línea cuando corresponde.

Validación local sin Docker:

```sh
uv sync --extra judge
uv run gtoc validate --problem gtoc13 --local /ruta/solution.txt
# Salida breve legible, con la misma comprobación física:
uv run python -m gtoc_bench.gtoc13 /ruta/solution.txt
```

Construcción y uso en una ejecución aislada:

```sh
uv run gtoc build --cpu --agent codex --tag gtoc-bench:gtoc13-cpu-v2
uv run gtoc validate --problem gtoc13 --image gtoc-bench:gtoc13-cpu-v2 /ruta/solution.txt
uv run gtoc run configs/codex-gtoc13.yaml
```

El último comando inicia una ejecución con el proveedor configurado. No es
necesario ejecutarlo para validar archivos localmente. Los otros agentes también
admiten `problem: gtoc13`; construye una imagen con `--agent all` para incluir
todos los runtimes, o `--agent claude-code` para ese cliente oficial. El entorno
científico y el juez son los mismos en todos los perfiles.
Dentro de cualquier ejecución, la interfaz sigue siendo `gtoc evaluate ARCHIVO`.

Pruebas y límites de la evidencia: [informe GTOC13](docs/gtoc13-validation.md).
La prueba completa del archivo Antipodes se marca como `slow` y tarda unos
minutos: `uv run pytest tests/test_gtoc13_controls.py -m slow`.

## Leaderboards oficiales de referencia

El benchmark conserva los [leaderboards finales de GTOCX, GTOC11, GTOC12 y GTOC13](references/leaderboards/README.md)
como referencias para el investigador, **fuera de la imagen y de los materiales
del agente**. GTOC13 incluye además una clasificación aproximada sin bonus
temporal, calculada con la fecha de la mejor entrega final de cada equipo y
reordenada por `J/c`.
GTOCX incluye las 42 entradas oficiales y una versión sin bonus: 12 puntuaciones
se recalculan desde entregas completas verificadas y 30 se estiman por fecha.
GTOC11 incluye sus 25 equipos y una segunda versión aproximada sin bonus,
utilizando las fechas UTC publicadas y la apertura de submissions del 17 de octubre.

Las puntuaciones oficiales de GTOC12 incluyen coeficientes competitivos por
asteroide; el benchmark los omite y evalúa masa sin ponderar. Se conserva la
tabla histórica y se señala que no es directamente comparable. No se deduce
una masa sin coeficientes a partir de la puntuación agregada.

Las tablas se ofrecen en Markdown y JSON, con sus fuentes, fechas y hashes.
La [metodología](references/leaderboards/README.md) recoge la precisión de la
corrección GTOC13 y permite reconstruir los datos sin conexión.

## Entorno fijado

| Componente | Versión |
| --- | --- |
| Codex CLI | 0.164.0 |
| Claude Code | 2.1.308, canal estable consultado el 27-10-2026 |
| OpenHands | CLI 1.16.0; bucle del SDK 1.21.0 con herramientas del CLI |
| SWE-agent | 1.1.0, commit `0f3acafacabc0def8cc76b4e48acb4b6cf302cb9` |
| mini-SWE-agent | 2.4.6 |
| Antigravity | SDK oficial `google-antigravity` 0.1.23; runtime nativo, protocolo Chat Completions |
| Pasarela Antigravity | CLIProxyAPI 8.0.54, integración comunitaria experimental |
| PyKEP | 3.0.0 |
| PyTorch | 2.8.0, imagen CPU o CUDA 12.8 |
| NumPy / SciPy | 2.2.6 / 1.16.2 |
| Plataforma del validador | Linux x86-64 |

Las distribuciones oficiales de los agentes están fijadas en
`environments/package-lock.json`, `agents.lock.json` y `python-agents.lock`.
Los agentes Python tienen un entorno propio, separado del entorno científico.
El cliente Claude del host utiliza `environments/claude-host/package-lock.json`.
Las imágenes base tienen
digest y las dependencias de Conda están resueltas por URL y checksum en
`science-linux-64.lock`. `science.yaml` conserva la selección legible original.
Cada ejecución registra el ID inmutable de la imagen utilizada y comprueba que
su código y materiales coinciden con este checkout.
La identidad experimental incluye también instrucciones, política de red y
recursos observados, evitando agrupar como iguales imágenes distintas que
compartan una etiqueta Docker.

Referencias: [Codex CLI](https://learn.chatgpt.com/docs/cli/reference),
[configuración de Codex](https://learn.chatgpt.com/docs/config-file/config-reference),
[versiones de Claude Code](https://code.claude.com/docs/en/setup) y
[ejecución no interactiva de Claude Code](https://code.claude.com/docs/en/headless),
[OpenHands 1.16.0](https://github.com/OpenHands/OpenHands-CLI/releases/tag/1.16.0),
[SWE-agent 1.1.0](https://github.com/SWE-agent/SWE-agent/releases/tag/v1.1.0) y
[mini-SWE-agent 2.4.6](https://github.com/SWE-agent/mini-swe-agent/releases/tag/v2.4.6).

## Preparación

Se necesita Docker y Python 3.11 o posterior. `uv` instala las dependencias del
controlador en `.venv`. Los comandos se ejecutan desde la raíz del repositorio:

```sh
git clone https://github.com/inaki11/GTOC-Agent-Bench.git
cd GTOC-Agent-Bench
uv sync --locked
uv run gtoc build
uv run gtoc doctor
uv run gtoc validate tests/fixtures/zero_mass.txt
```

Para actualizar una imagen existente conservando sus agentes y su variante
CPU/CUDA, se utiliza la receta compartida:

```sh
uv run gtoc build --base-image gtoc-bench:phase1-cpu --tag gtoc-bench:actualizada
```

Las recetas completa, de actualización y QEMU aplican el mismo ajuste de
permisos después de copiar materiales. El arranque comprueba propietario y
permisos del juez; en GTOC 12 también valida dos controles pequeños como UID
1001 antes de iniciar el agente. Véase [construcción y comprobación del juez](docs/container-images.md).

Por defecto la imagen contiene todos los agentes y las mismas herramientas científicas. En
macOS con Apple Silicon, Docker emula x86-64: sirve para probar el entorno, pero
sus tiempos no son comparables con los de un servidor Linux nativo.

La imagen común, tanto CPU como CUDA, incluye `file` para identificar formatos
y ejecutables. `gtoc tools` registra su ruta y versión junto al resto de herramientas.

Antes del preflight científico, el supervisor prepara las cachés de Matplotlib
y Fontconfig como UID 1000. El agente y sus herramientas comparten
`XDG_CONFIG_HOME`, `XDG_CACHE_HOME` y `MPLCONFIGDIR` dentro de su workspace.
Las [correcciones de Astra](docs/astra-gtoc13-corrections.md) explican esta
preparación, la clasificación de incertidumbre y la separación del cierre de
ejecución respecto a una recuperación ya resuelta.

También incluye `threadpoolctl` 3.6.0 para inspeccionar y controlar los hilos de
BLAS/OpenMP desde `/opt/conda/bin/python`, y PyYAML 6.0.3 para configuraciones YAML
y la salida de diagnóstico de NumPy. Sus wheels y hashes están fijados en
`environments/science-tools.lock`. El paquete Debian `poppler-utils` proporciona
`pdfinfo`, `pdftotext`, `pdftoppm` y `pdftocairo` para inspeccionar, extraer texto y
renderizar documentos PDF. Sus versiones y las de ambos paquetes Python aparecen
en `gtoc tools`, el preflight y el inventario de la imagen. Las ayudas locales se
consultan con `pdfinfo -h`, `pdftotext -h` o
`/opt/conda/bin/python -m pydoc threadpoolctl`.

El compilador general [Zig](https://pypi.org/project/ziglang/0.17.0/) está
preinstalado como `zig`, `python-zig` y `/opt/conda/bin/python -m ziglang`.
También permite compilar C/C++ con `zig cc` y `zig c++`. La versión 0.17.0 y el
SHA-256 del wheel están fijados en `environments/science-tools.lock` y aparecen
en el inventario de la imagen. `ziglang` forma parte del catálogo PyPI permitido;
su referencia se consulta con `gtoc docs https://ziglang.org/documentation/0.17.0/`.
En las pruebas locales con Rosetta, un ejecutable Zig mínimo sin libc falló con
`bss_size overflow`; compilarlo con `zig build-exe archivo.zig -lc` permitió
ejecutarlo. Es una limitación observada de ese perfil de emulación.

En Linux ARM también hace falta registrar un emulador x86-64. Para pruebas de
infraestructura con QEMU, se puede derivar un perfil CPU de la imagen ya construida:

```sh
docker build --platform linux/amd64 -f environments/Dockerfile.qemu \
  -t gtoc-bench:phase1-cpu-qemu .
```

Selecciona esa imagen en `environment.image` y mantén `hardware.gpus: none`.
El perfil fija `KMP_AFFINITY=disabled` para evitar un fallo de detección de
topología de OpenMP bajo QEMU; el valor llega también a las herramientas del
agente y queda registrado en el hardware observado. Este perfil no acredita
ejecución ARM nativa ni uso de GPU. La configuración de emulación del host
se describe en la [documentación de Docker](https://docs.docker.com/build/building/multi-platform/).
El preflight comprueba la versión del agente seleccionado; un cliente ajeno a
esa ejecución no debe impedir su arranque.

La imagen incorpora CasADi 3.8.2, cyipopt 1.7.0 sobre el IPOPT de Conda,
Numba 0.68.1 / llvmlite 0.50.0, pybind11 3.1.0, Cython 3.3.0 y `pkg-config`.
Las versiones y hashes están en `science-tools.lock` y `science-source.lock`;
no es necesario construir una interfaz propia para utilizar IPOPT.
`gtoc tools` muestra versiones y rutas, también registradas en el preflight.
La [revisión de validación y herramientas](docs/validation-and-tools.md)
documenta los problemas observados, sus correcciones y los nuevos límites.

Las nuevas configuraciones usan `hardware.gpus: auto` y `environment.image: auto`.
En un servidor NVIDIA se asignan todas las GPU y se selecciona
`gtoc-bench:phase1-cuda`; sin NVIDIA se selecciona `gtoc-bench:phase1-cpu`.
`gtoc build` construye la variante correspondiente al servidor. Se puede elegir
explícitamente con `--cuda` o `--cpu`:

```sh
uv run gtoc build --cuda
uv run gtoc doctor --gpus all
```

Para ahorrar disco se pueden construir imágenes de un único cliente oficial:

```sh
uv run gtoc build --cuda --agent codex
uv run gtoc doctor --image gtoc-bench:codex-cuda --gpus all
# Alternativa: --agent claude-code, imagen gtoc-bench:claude-code-cuda
```

Selecciona esa imagen explícitamente en `environment.image`. El perfil omite
los otros clientes y SDK, conservando PyTorch, PyKEP, IPOPT, Numba, compiladores
y materiales idénticos. `--agent all` conserva la imagen universal habitual.
CPU y CUDA comparten las capas científicas previas a PyTorch; CUDA se instala
directamente, sin apilarla sobre una instalación CPU. Se limpia la caché npm en
la misma capa que la instalación. Las imágenes publicadas antes de este cambio
conservan su ID y sus contenidos para reproducir las pruebas anteriores.
En Podman, la construcción utiliza un directorio temporal privado del sistema
para los blobs de imagen y lo limpia al terminar. Esto evita el valor por defecto
`/var/tmp`, que puede agotar una cuota de disco durante el empaquetado. Si se
declara `TMPDIR`, se utiliza esa ubicación para crear el directorio privado.

`hardware.gpus: none` permite una prueba deliberadamente CPU; `all` exige NVIDIA.
Las configuraciones fechadas de experimentos anteriores mantienen sus condiciones.
Una imagen explícita en `environment.image` conserva prioridad sobre la selección
automática, pero debe soportar CUDA si se asignan GPU. El servidor necesita NVIDIA
Container Toolkit y un controlador compatible con CUDA 12.8. Docker utiliza
`--gpus all`; Podman utiliza [dispositivos CDI](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/cdi-support.html).
Con Podman/crun se requiere crun >= 1.26 para ejecutar los hooks NVIDIA antes
de montar la raíz como solo lectura; las versiones anteriores se rechazan.
La corrección está en [crun 1.26](https://github.com/containers/crun/releases/tag/1.26).
Actualizar el runtime no requiere hacer el contenedor privilegiado ni habilitar
escritura en su raíz.
`gtoc doctor` y el preflight comprueban una multiplicación de matrices en **cada
GPU como UID 1000**, antes del presupuesto y sin llamar a ningún modelo. Un driver
roto, una imagen CPU o un error de acceso detienen la preparación; no se cambia
silenciosamente a CPU. El inventario, UUID, VRAM, CUDA y los cálculos de prueba
quedan registrados en `gpu-allocation.json`, `hardware.json` y `preflight.json`.
El prompt informa al agente de los dispositivos y `gtoc status --json` muestra
`gpu_allocation`. Las bibliotecas científicas se ejecutan con
`/opt/conda/bin/python`; el código CPU no se acelera automáticamente.

La imagen científica actual es amd64: en Spark/ARM bajo emulación x86 se debe
usar `gpus: none` y `gtoc build --cpu` hasta disponer de una imagen científica
nativa. La excepción exploratoria `concurrent_with` también exige `gpus: none`:
no asigna las mismas GPU a dos evaluaciones. Una continuación comprueba que
conserva las GPU efectivas del segmento anterior; cambiar de CPU a GPU requiere
una prueba independiente.

Cada contenedor tiene un
máximo de **32 CPU lógicas**. `hardware.cpus` vale 32 por defecto; en una máquina
con menos CPU se usan las disponibles. Se permite un límite inferior, mientras
que una configuración superior a 32 se rechaza. `cpus: null` también queda
limitado a 32. El controlador aplica siempre `--cpus`: es una cuota de cómputo
para el conjunto de procesos, no una asignación de núcleos físicos ni un límite
al número de hilos. Los límites solicitados y efectivos quedan registrados;
el agente recibe el límite efectivo al comenzar y puede consultarlo en
`gtoc status --json`, campo `cpu_limit`.
[Cuotas CPU de Docker](https://docs.docker.com/engine/containers/resource_constraints/#cpu).
Si se omite el límite de memoria, se usa el 90 % de la declarada por Docker,
reservando espacio para supervisión. Esta política se aplica a nuevos
contenedores; las pruebas ya iniciadas conservan sus condiciones originales.

## Credenciales y modelo

### Suscripción a ChatGPT

El controlador implementa el flujo oficial **Sign in with ChatGPT** para
aplicaciones locales, con OAuth y PKCE. No hace falta una API key ni modificar
Codex. El consentimiento se realiza en el navegador y la pasarela renueva la
sesión en el host, manteniendo al agente en ejecución.

Codex usa Responses directamente. Claude Code, OpenHands, SWE-agent y
mini-SWE-agent pueden usar la misma autorización mediante el adaptador experimental
`chatgpt-responses-v1`. La conversión ocurre en la pasarela; los clientes oficiales
no se modifican. En esta modalidad Claude Code consulta un modelo de OpenAI y
consume la suscripción de ChatGPT, no una suscripción de Anthropic.

```sh
uv run gtoc auth login chatgpt
uv run gtoc auth status chatgpt
uv run gtoc auth models chatgpt
# Revisa el modelo y el razonamiento del ejemplo según el catálogo de tu cuenta.
uv run gtoc run configs/codex-chatgpt.yaml --detach
```

Si el inicio de sesión no concedió permiso para usar el plan, solicita ese
consentimiento con `uv run gtoc auth login chatgpt --enable-plan`. El servicio
está en preview: el acceso efectivo depende de la cuenta, el modelo y las
políticas del proveedor. El catálogo se comprueba antes de empezar; una
inferencia real es la comprobación definitiva de disponibilidad.

Para varias cuentas, usa `--profile NOMBRE` en los comandos de autenticación y
el mismo nombre en `provider.profile`. Cada perfil conserva su registro de
cliente y su identidad. Cambiar o volver a autorizar el perfil durante una
evaluación detiene las siguientes llamadas de esa evaluación; no cambia de
cuenta silenciosamente.

Las sesiones se guardan en `~/.config/gtoc-bench/auth/`, fuera del repositorio,
con directorio `700` y archivos `600`. No se importan sesiones de `~/.codex`.
Un bloqueo entre procesos protege la rotación del refresh token. Si una
interrupción deja incierto si se consumió ese token, se exige iniciar sesión
otra vez. Los tokens no se copian al contenedor ni a los resultados.

Para autorizar una cuenta en un servidor, abre el túnel desde tu portátil:

```sh
ssh -L 1455:127.0.0.1:1455 servidor
# En el servidor, dentro de bench/:
uv run gtoc auth login chatgpt --no-browser --port 1455
```

Abre la URL mostrada en el navegador del portátil. El túnel solo es necesario
durante la autorización. Una ejecución posterior con `--detach` continúa en
el servidor al cerrar SSH. Si el puerto está ocupado, utiliza otro en ambos
extremos y en `--port`. No compartas una misma sesión rotatoria entre máquinas;
autoriza un perfil en cada host.

`uv run gtoc auth logout chatgpt` intenta revocar la sesión y borra sus tokens
locales, conservando el registro para el siguiente inicio de sesión. Si no
puede confirmar la revocación remota, lo indica. Puedes revisar permisos,
cuotas y créditos en [ChatGPT Settings → Usage](https://chatgpt.com/settings/usage).

Referencias oficiales: [registro y autorización](https://developers.openai.com/siwc/token-sharing-open-source/sign-in),
[sesiones y renovación](https://developers.openai.com/siwc/token-sharing-open-source/profiles-and-sessions),
[inferencia y catálogo](https://developers.openai.com/siwc/token-sharing-open-source/models-and-inference).

En `provider.protocol` se declara el protocolo del cliente: `responses` para
Codex, `anthropic` para Claude Code y `chat_completions` para los ejemplos de
OpenHands y los dos SWE. El destino siempre es el endpoint público Responses.
El adaptador convierte texto, funciones locales y sus resultados; conserva el
razonamiento cifrado necesario entre llamadas dentro de la ejecución. Para los
protocolos traducidos entrega la respuesta cuando finaliza la inferencia,
incluido cuando el cliente pide streaming. Este retraso forma parte del perfil.
No admite contenido multimodal ni las rutas de compactación remota o
`messages/count_tokens`. No es una declaración de compatibilidad completa para
ejecuciones largas. Los metadatos de cada petición identifican el adaptador,
protocolo original y parámetros solicitados y efectivos.

La versión `chatgpt-responses-v3` conserva los elementos completos recibidos en
`response.output_item.done`, por su índice. Así mantiene texto, llamadas a
herramientas y razonamiento aunque `response.completed` no repita el contenido
en `output`; el consumo y el nivel de servicio siguen procediendo del proveedor.
Al convertir la respuesta, los errores de ventana de contexto se devuelven como un error 400 con
el código y diagnóstico originales: esperar no corrige la petición y no se abre
una ventana de reintentos ni se congela el contenedor por ese motivo.

Hay configuraciones de desarrollo en `configs/claude-code-chatgpt.yaml`,
`configs/openhands-chatgpt.yaml`, `configs/swe-agent-chatgpt.yaml` y
`configs/mini-swe-agent-chatgpt.yaml`, con Luna y razonamiento `low`.
Cada archivo se lanza con `uv run gtoc run ARCHIVO --detach`. Sus presupuestos
son ejemplos editables; las pruebas breves utilizan los valores de
`tests/live_smoke.py`.

### Antigravity: agente y acceso a modelos

Son dos opciones independientes:

- **Agente `antigravity`:** utiliza el bucle y las herramientas del SDK oficial
  de Google, fijado a 0.1.23. Puede conectarse a un modelo distinto de Google
  mediante Chat Completions: OpenAI, Ollama/vLLM en otra máquina o una pasarela
  compatible. También admite el perfil ChatGPT existente. Los ejemplos son
  `configs/antigravity-openai.yaml` y `configs/antigravity-chatgpt.yaml`.
- **Proveedor `antigravity`:** permite a los seis agentes consultar los modelos
  que anuncie la cuenta, incluidos los de otros fabricantes. Utiliza una
  instancia privada de CLIProxyAPI 8.0.54 en el host. Es experimental y no es una
  API oficial de Google. Google restringe expresamente reutilizar la suscripción
  de Antigravity en clientes de terceros; puede afectar al acceso a la cuenta.
  [FAQ de Google](https://antigravity.google/docs/faq) y
  [pasarela fijada](https://github.com/router-for-me/CLIProxyAPI/releases/tag/v8.0.54).

Para preparar el proveedor experimental en la máquina que ejecutará la prueba:

```bash
gtoc install-provider antigravity
gtoc auth login antigravity
gtoc auth status antigravity
gtoc auth models antigravity
```

La instalación comprueba el SHA-256 del archivo publicado. El login abre el
navegador y guarda un perfil privado, separado del Antigravity personal ya
instalado. En SSH puede usarse `--no-browser`; el callback de Antigravity es
`51121`, que requiere un túnel SSH si el navegador está en otra máquina.
`--port` cambia ese callback y `--bridge-port` fija el puerto de la pasarela
(8317 por defecto). `--profile` y `--auth-dir` permiten separar credenciales;
deben coincidir con el YAML de la ejecución.

Copia un ID exacto del catálogo en `model.id`; las plantillas llevan
`REPLACE_WITH_MODEL_ID_FROM_CATALOG` para no inventar disponibilidad. No se
filtra el catálogo por fabricante. Modelos, cuotas y permisos dependen de la
cuenta. Los ejemplos de acceso son:

```bash
gtoc run configs/antigravity.yaml --detach          # Agente y proveedor Antigravity
gtoc run configs/codex-antigravity.yaml --detach    # Codex con ese catálogo
gtoc run configs/claude-code-antigravity.yaml --detach
```

Para OpenHands, SWE-agent o mini-SWE-agent, conserva su `agent` y usa
`provider.auth_method: antigravity`, `provider.id: antigravity`, perfil privado
y URL `http://127.0.0.1:8317/v1`, con `protocol: chat_completions`.

El controlador inicia y detiene su pasarela; un supervisor la cierra también si
desaparece el controlador o deja de recibir sus señales. Una única cuenta y un bloqueo por
perfil impiden rotación entre cuentas. Se desactivan los reintentos internos,
el cambio de modelo por cuota y el gasto adicional de créditos. La escucha es
solo local y la administración web está deshabilitada. Las credenciales OAuth
permanecen fuera del repositorio, los resultados y el contenedor. El cliente
del contenedor solo recibe una credencial sin valor fuera de la evaluación.

La auditoría registra versión y hash del puente, identidad opaca de cuenta,
modelo solicitado/devuelto, razonamiento, consumo y cierre del transporte.
El uso se identifica como `CLIProxyAPI_reported`: el bench ve las respuestas
traducidas, no las peticiones internas al proveedor. Tampoco puede confirmar el
nivel de servicio real que haya aplicado Google. No se atribuyen precios API a
esa cuota ni se presenta este perfil como equivalente a una conexión nativa.

El agente SDK usa sus herramientas nativas de terminal y archivos, con permisos
delegados al contenedor, sin subagentes, web ni imágenes. Sus hooks solo recogen
actividad. Se conserva su sesión en `/workspace/antigravity`; `gtoc resume`
restaura esa conversación mediante el SDK oficial. No se evalúa la interfaz del IDE
ni el CLI `agy`. En ARM se incluye el ejecutable ARM de la misma versión para
evitar el fallo del ejecutable amd64 bajo emulación; ciencia y validador siguen
en amd64. Todas estas condiciones quedan ligadas a la imagen y configuración.

El adaptador registra los pasos públicos, errores, petición de parada y causa de
cierre del SDK. Un turno sin respuesta final completada admite hasta dos
recuperaciones en la misma conversación, con un mensaje neutro registrado y
consumo normal de tiempo/tokens. Una finalización voluntaria no se prolonga.
Si persiste el cierre incompleto, aparece `agent_runtime_interrupted`, con estado
`interrupted`; los límites o errores explícitos del SDK no se reintentan así.

`gtoc auth logout antigravity` elimina la credencial local; para revocar el
consentimiento también hay que retirarlo desde la cuenta de Google. Las pruebas
simuladas no verifican el login ni la disponibilidad de una cuenta real.

### Suscripción de Claude con Claude Code oficial

La cuota de Claude se usa exclusivamente con su cliente oficial. Anthropic no
permite ofrecerla como backend de otros agentes mediante sus credenciales OAuth.
Por tanto, no se extraen tokens, no se construye un proxy OAuth de Anthropic y
no se utiliza un fork. [Política de autenticación de Anthropic](https://code.claude.com/docs/en/legal-and-compliance#authentication-and-credential-use)

En la máquina que ejecutará el benchmark:

```sh
uv run gtoc install-agent claude_code
uv run gtoc auth login claude
uv run gtoc auth status claude
uv run gtoc run configs/claude-subscription.yaml --detach
```

La instalación privada en `~/.local/share/gtoc-bench/claude-code/2.1.308/` no
sustituye tu instalación personal. Necesita npm. El comando de login delega en
`claude auth login --claudeai`: el cliente oficial abre el navegador y gestiona
la sesión. Por SSH, sigue su URL y pega el código si lo solicita. El perfil se
sitúa en `~/.config/gtoc-bench/claude-code/default/`; puedes elegir otro con
`--profile` y `provider.profile`. `gtoc auth logout claude` delega el cierre
de sesión al mismo cliente. Los flags `--enable-plan`, `--no-browser`, `--port`
y el comando `auth models` son exclusivos de ChatGPT.

Para usar la sesión de Claude Code que ya está iniciada en esa misma máquina,
selecciona `provider.profile: current` y compruébala con
`gtoc auth status claude --profile current`. El cliente oficial conserva su
configuración de autenticación habitual (o `CLAUDE_CONFIG_DIR`, si estaba
definido); el benchmark no importa ni copia sus credenciales. El bloqueo del
benchmark se guarda en `auth_dir`, fuera de la sesión personal. El perfil
`default` sigue usando el inicio de sesión separado descrito arriba.

La ejecución usa `--restricted`, sin herramientas incorporadas, hooks ni MCP
adicionales. Solo habilita `mcp__gtoc__exec`, que ejecuta como UID 1000 en el
contenedor y permite leer archivos, programar y llamar a `gtoc status/evaluate`.
La clave API y los tokens de sesión no se transmiten a ese entorno. El bench
no lee ni renueva las credenciales de Claude: lo hace el cliente oficial.

El manifiesto registra `official_cli_container_tools`. El razonamiento y la
velocidad estándar se configuran en el CLI; el consumo procede de sus eventos
y se reconcilia con sus totales finales. No se inspeccionan las peticiones HTTPS
nativas. Las llamadas sin cierre se marcan como consumo incierto y el límite
de tokens sigue siendo orientativo. El coste real queda en `null`.
Este perfil de herramientas y medición debe distinguirse del de Claude con API
key en las comparativas. Si desaparece el controlador, un supervisor también
termina el cliente del host.
Las políticas administradas de Claude en la máquina siguen siendo aplicables;
deben estar controladas al preparar un servidor de campaña.

Varias evaluaciones CPU declaradas con `hardware.concurrent_with` pueden usar
el mismo perfil de Claude con conversaciones, herramientas y workspaces
independientes. Los clientes oficiales comparten un bloqueo de uso del perfil;
login/logout siguen necesitando su bloqueo exclusivo y no pueden cambiar la
cuenta mientras quede alguna evaluación activa. La autenticación y renovación
siguen en el CLI oficial; el benchmark no lee ni copia sus credenciales.

La autorización y disponibilidad de modelos reales dependen de tu cuenta. Los
tests automáticos verifican este recorrido con el CLI oficial y un proveedor
simulado. `auth status` puede mostrar una sesión iniciada cuyo OAuth ya caducó:
una inferencia real confirma que puede renovarse y utilizar el modelo. Los errores
nativos de autenticación se registran como `native_provider_error` y los 401/403
cierran con `configuration_error`. Los mensajes locales con modelo `<synthetic>`
no se contabilizan como inferencia ni como cambios del modelo; el consumo real
parcial disponible se conserva como incierto.

### API key y proveedores compatibles

Crea un archivo privado por proveedor **fuera del repositorio**, por ejemplo:

```text
~/.config/gtoc-bench/providers/openai.env
    OPENAI_API_KEY=...

~/.config/gtoc-bench/providers/anthropic.env
    ANTHROPIC_API_KEY=...
```

El archivo debe tener permisos `600`. No se ejecuta como un script, no se
interpolan variables y su contenido no se escribe en el manifiesto. La clave
real permanece en el proceso del controlador; los agentes reciben únicamente
un valor local sin acceso directo al proveedor.

Revisa `configs/codex.yaml` o `configs/claude-code.yaml`: el modelo es una elección
independiente del cliente. Los modelos de esos ejemplos no se han utilizado
para medir rendimiento. Se requiere un modelo disponible para tu cuenta y
compatible con las herramientas y el razonamiento solicitados.

Codex comprueba además los metadatos del modelo con el cliente fijado, antes de
iniciar el presupuesto y sin generar tokens. Con ChatGPT utiliza el catálogo
completo del proveedor. Si el modelo no está reconocido se rechaza el arranque,
evitando los metadatos de respaldo silenciosos. Otros proveedores pueden aportar
`model.catalog_file`, un catálogo JSON compatible con esa versión de Codex.
La resolución queda en `model-compatibility.json`; véanse el formato y los
[criterios de contexto y compactación](docs/observability-and-model-metadata.md).

| Agente | Protocolos con API key | Ejemplo |
| --- | --- | --- |
| Codex | Responses | `configs/codex.yaml` |
| Claude Code | Anthropic Messages | `configs/claude-code.yaml` |
| OpenHands | Responses, Chat Completions o Anthropic Messages, según su registro de modelos | `configs/openhands.yaml` |
| SWE-agent | Chat Completions o Anthropic Messages | `configs/swe-agent.yaml` |
| mini-SWE-agent | Chat Completions o Anthropic Messages | `configs/mini-swe-agent.yaml` |

OpenHands conserva el agente, herramientas y condensador oficiales del CLI,
ejecutados mediante su SDK, sin descarga automática de skills. El ejemplo usa
GPT-5.4 y Responses; su versión del registro de capacidades puede no reconocer
modelos posteriores. SWE-agent usa SWE-ReX local dentro del contenedor existente;
no necesita Docker anidado. Su comando `finish` termina la conversación;
únicamente `gtoc evaluate` registra candidatos. mini-SWE-agent utiliza su bucle
y herramienta bash nativos. Ninguno recibe una estrategia de optimización.

Los adaptadores Python usan el protocolo nativo seleccionado a través de
LiteLLM, fijado en el lock. Con API key la pasarela conserva el protocolo; con
ChatGPT aplica la adaptación experimental descrita arriba. No sustituye
un modelo incompatible. La URL puede apuntar a otro proveedor compatible; la matriz de
compatibilidad con Ollama y otros proveedores se ampliará en la siguiente fase.

Las llamadas que pasan por la pasarela se fuerzan al servicio estándar y al razonamiento declarado.
Con API key, el máximo de salida actúa como techo, conservando un límite menor
solicitado por el cliente. Con ChatGPT, `model.max_output_tokens` debe omitirse:
ese servicio no acepta un techo por respuesta. Se conserva el presupuesto
orientativo de tokens por ejecución y se registran los parámetros recibidos y
los enviados. Las
llamadas auxiliares deben usar el mismo modelo. No hay claves administrativas,
creación automática de claves ni gestión de presupuestos económicos del proveedor.

## Ejecutar y consultar

```sh
uv run gtoc run configs/codex.yaml
uv run gtoc run configs/claude-code.yaml --detach
uv run gtoc run configs/openhands.yaml --detach
uv run gtoc run configs/swe-agent.yaml --detach
uv run gtoc run configs/mini-swe-agent.yaml --detach
uv run gtoc status ID_DE_EJECUCION
uv run gtoc stop ID_DE_EJECUCION
uv run gtoc analyze ID_DE_EJECUCION
```

`run` crea siempre un directorio nuevo. `--detach` inicia el controlador en una
sesión independiente, con salida en `controller.log`. Puede lanzarse por SSH y
continuar cuando se desconecte el portátil:

```sh
ssh servidor 'cd /srv/GTOC-Agent-Bench && uv run gtoc run configs/codex.yaml --detach'
```

Para las próximas pruebas remotas de este proyecto, el destino preferido es el
alias SSH `spark1`.

El controlador permanece en ese servidor. Por defecto, un bloqueo del host y la
comprobación de contenedores etiquetados impiden dos evaluaciones simultáneas.
Para una excepción exploratoria autorizada, `hardware.concurrent_with` puede
nombrar el `run_id` de una evaluación existente o una lista de identificadores
distintos. El controlador comprueba sus identidades y cuotas de CPU, rechaza
cualquier contenedor no declarado y exige que la suma de todas las cuotas,
incluida la nueva evaluación, no supere las CPU del host. Solo se admite CPU;
la condición compartida se registra en la configuración y `host-sharing.json`.
La admisión queda serializada hasta que el nuevo contenedor está preparado.
Este modo no acredita igualdad de recursos ni una evaluación con máquina exclusiva.

Si muere el
controlador, el proceso protegido del contenedor detecta la pérdida de su señal
periódica y mata los procesos del agente en un máximo aproximado de diez segundos.
No se concede un presupuesto nuevo automáticamente.
Un intento no puede arrancarse dos veces. `status` señala cuando el PID del
controlador ya no existe, conservando el estado anterior en los archivos. Tras
una muerte abrupta pueden quedar un contenedor parado y envíos pendientes:
los archivos se conservan para inspección; esta fase no reanuda ni certifica
automáticamente ese intento.

El cierre registra cada fase, su duración y sus errores en `finalization.json`.
Las esperas del transporte y los comandos Docker tienen plazos acotados. Un
timeout de `docker kill` no impide intentar la retirada del contenedor ni
guardar el resumen y los resultados ya recibidos. Los fallos de exportación
tampoco impiden sellar los registros originales cuando sus escritores se han
detenido. `finalization.status` distingue `completed`, `completed_with_errors`
e `incomplete`; la causa original del corte permanece en `stop_reason`.
Si no se confirma la parada del contenedor o quedan escritores activos, se
guarda un resumen incompleto y no se publica un sello de integridad exitoso.
Un fallo al crear el sello también queda declarado y no convierte el intento
en una ejecución completada.

Desde el host, `gtoc status RUTA_DE_RUN --json` incluye `successor.ready`,
`successor.blocked` y `successor.reason`. Una cola debe exigir `ready: true`,
comprobar por separado la liberación de credenciales y la disponibilidad de la
máquina, y detener su espera si aparece `blocked: true`. El helper
`gtoc_bench.lifecycle.predecessor_state` ofrece la misma comprobación: detecta
controladores muertos, contrasta los hashes de los metadatos de cierre y
rechaza un cierre incompleto. No modifica resultados ni reanuda pruebas.
Los operadores y despliegues congelados anteriores mantienen su código;
actualizar el repositorio no cambia una cola ya arrancada en otro servidor.

Los nuevos ejemplos con pasarela usan `provider_wait`: mantienen el reloj
durante inferencia, herramientas, validación, instalaciones y esperas breves;
ante indisponibilidad transitoria congelan el contenedor CPU y pausan el tiempo,
reintentando cada cinco minutos durante un máximo de una hora. La petición y
la conversación se conservan. El perfil `continuous_wall` sigue disponible e
incluye todas las esperas. La
[documentación de recuperación](docs/provider-wait.md) detalla configuración,
registro y límites: la pausa actual requiere CPU sin GPU asignadas y tráfico
de modelo a través de la pasarela. Los ejemplos de suscripción nativa de Claude
conservan `continuous_wall`.

El almacenamiento también se vigila durante la ejecución. Las peticiones y
respuestas nuevas se comprimen al generarse, evitando una segunda copia completa
del cuerpo del cliente cuando basta un overlay. Los mensajes repetidos comparten
fragmentos comprimidos y los candidatos se comprimen sin alterar sus bytes.
`storage.audit_reserve_gb` estima la auditoría al admitir una ejecución, sin
imponer un máximo. El antiguo `storage.audit_gb` se interpreta como esa estimación.
`storage.minimum_free_gb` reserva margen de cierre (2 GiB);
`hardware.workspace_gb` limita los archivos del agente. El preflight
consulta la cuota de usuario cuando está disponible y reserva capacidad para
las pruebas simultáneas. Véanse el [perfil para tres pruebas con 40 GiB](docs/storage.md)
y `gtoc inspect-request --help` / `gtoc inspect-submission --help` para recuperar
los cuerpos y candidatos comprimidos.

El heartbeat del controlador usa un contador privado montado de solo lectura,
independiente del tráfico de inferencia. Un guardián en el host vigila la RAM,
avisa al 75% y corta al 90% del conjunto de memoria en uso, descontando caché
inactiva, con causa `memory_limit` y checkpoint disponible. El prompt y el estado
muestran esos límites. `gtoc resume --memory-gb 96` permite una continuación de
investigación con más RAM, registrando el cambio y conservando el presupuesto.
Véase [RAM, heartbeat y recuperación](docs/memory-supervision.md).

Se respeta `Retry-After` tanto en cabeceras HTTP como dentro de errores SSE:
un mínimo de 302 segundos prevalece sobre el intervalo local de 300. Si no
cabe en la ventana, se conserva una interrupción con checkpoint y
`provider-recovery.json` sellado. `gtoc resume` comprueba que haya transcurrido
el mínimo del proveedor antes de admitir la continuación.

El límite de tokens suma entrada y salida,
incluida la entrada de caché, sin volver a sumar el razonamiento ya incluido en
la salida. Es orientativo: las solicitudes en vuelo pueden excederlo y se
conserva el exceso conocido. Un stream incompleto se marca como consumo parcial
o desconocido, nunca como medición exacta de cero tokens.

Al terminar el presupuesto o cancelar la ejecución, se bloquean nuevas
solicitudes y se cancela activamente la inferencia pendiente antes de esperar
el cierre del experimento. La pasarela utiliza HTTP asíncrono y un observador
del presupuesto con comprobaciones cada 50 ms: puede interrumpir la conexión,
la espera de cabeceras, un streaming silencioso, una respuesta sin streaming y
la espera entre reintentos. No espera otro fragmento ni el timeout de lectura.
La cadencia no constituye una garantía de latencia bajo cualquier carga.

`model_cancel_requested` y `model_transport_closed` registran el motivo, los
instantes de solicitud y cierre local y su latencia; el detalle se incorpora a
`requests/<id>/metadata.json`. Con suscripción de Claude se termina el cliente
oficial y se registran `native_cancel_requested` y `native_client_stopped`.
En ambos casos `upstream_cancellation_confirmed` permanece en `false`: cerrar
el transporte o el cliente no acredita que el proveedor haya dejado de generar
o facturar. El consumo disponible se conserva y, si falta el total final, se
marca como parcial o desconocido. El umbral de tokens se detecta con el consumo
reportado, que puede llegar al final de una respuesta; no es un corte por token
generado ni se reconstruye una cifra exacta tras cancelar.

Los errores transitorios tienen reintentos acotados. El agotamiento de cuota
se conserva como interrupción; no se cambia de clave, modelo ni proveedor.
También se detecta cuando ChatGPT envía el error de cuota dentro de una
respuesta HTTP 200. En `provider_wait`, la pasarela retiene el turno completo
antes de entregarlo al agente y puede repetir un intento fallido sin duplicar
herramientas; cada intento conserva su consumo conocido o incierto. Una cuota
económica explícita o una autenticación inválida no activan esta espera.
En `continuous_wall`, la pasarela no reproduce una respuesta parcialmente
entregada. En Codex, su recuperación nativa puede continuar la conversación después de un
corte de transporte: hasta `max_transient_retries` cortes consecutivos, con
esperas acotadas por `max_retry_wait_seconds`. Cada petición y su posible consumo
parcial quedan registrados; el reloj sigue avanzando. Una respuesta completa
reinicia ese contador. Los demás agentes conservan el cierre conservador del
intento ante un stream incompleto. La renovación de autenticación no reinicia el reloj.

`requests/<id>/metadata.json` incluye el cierre observado (EOF sin evento final,
excepción de transporte, evento terminal o cancelación), clase y cadena de
excepciones, identificador del proveedor, cabeceras permitidas, bytes y fragmentos
recibidos, último evento/contador SSE y tiempos sin datos. Los eventos
`model_stream_interrupted`, `model_stream_retry` y `model_stream_recovered`
relacionan la incidencia con su recuperación. Un EOF limpio no permite conocer
la causa interna del proveedor: se registra esa incertidumbre. Todo transporte
cierra con `model_transport_closed`; solo se declara cancelación si se solicitó
mientras aún estaba activo.

Para continuar explícitamente una prueba Codex o Antigravity finalizada por interrupción,
cancelación o fallo, con presupuesto restante:

```bash
gtoc resume /ruta/al/run-anterior --results results/continuaciones --detach
# Si cambió la ubicación de las credenciales o se reconstruyó la imagen:
gtoc resume /ruta/al/run-anterior --config configs/codex.yaml --detach
# Claude Code o SWE-agent: archivos y transcripción, conversación nueva.
gtoc resume /ruta/al/run-claude --workspace-only --config configs/claude-subscription.yaml --detach
# Recuperación de investigación de un cierre antes clasificado como agent_finished:
gtoc resume /ruta/al/run-anterior --recovery-reason "SDK cerró sin respuesta final" --detach
```

`resume` verifica el sellado y los hashes del workspace, resta el tiempo y los
tokens conocidos ya consumidos y crea otro `run_id` con `parent_run_id`.
Restaura la sesión nativa y los archivos regulares, incluidas instalaciones del
usuario; regenera la configuración del cliente y no copia credenciales ni sigue
enlaces. Exige el mismo agente, versión, modelo, razonamiento, proveedor, datos,
validador y recursos configurados. Una imagen corregida queda identificada en el
nuevo manifiesto. `checkpoint.json` indica si existe una sesión recuperable.
Para Claude Code y SWE-agent, `--workspace-only` recupera los archivos y una copia verificada
de la transcripción observable, que el agente puede consultar. Inicia una
conversación nueva: no reconstruye la sesión nativa ni su caché de contexto.
La opción es explícita y conserva presupuesto restante, puntuación heredada y
consumo acumulado. No se presenta como una reanudación exacta de conversación.
SWE-agent guarda su nueva trayectoria en otro directorio para conservar la del
tramo anterior. El prompt avisa del tamaño potencial del historial y recomienda
consultar fragmentos acotados para evitar volver a exceder la ventana de contexto.
Antigravity requiere una única base de conversación y su transcripción asociada;
el host verifica bytes y hashes, y el SDK abre la base dentro del contenedor en
modo `RESUME`, que falla si no existe la sesión. El identificador de otra prueba
concurrente autorizada puede actualizarse sin cambiar los recursos asignados.
`--recovery-reason` permite reabrir explícitamente un `agent_finished` con
presupuesto restante. Conserva intactos su resumen y sello, y registra la causa
de recuperación y la clasificación original en la nueva continuación.

La pausa entre intentos no cuenta como cómputo: es una continuación de
investigación, no una prueba independiente ni una ejecución continua de ranking.
Conserva puntuación previa y consumo acumulado, incluyendo solicitudes de consumo
desconocido; las nuevas evaluaciones tienen ejes de segmento y acumulados en
`exports/progress.csv`. No restaura procesos ni memoria RAM/GPU, ni puede recuperar
automáticamente un intento sin cierre y sellado completos tras una caída abrupta
del host. `gtoc run` sigue empezando con workspace y conversación nuevos.

Las llamadas consecutivas al modelo esperan hasta el máximo configurado en
`recovery.max_retry_wait_seconds` a que se cierre la anterior, con el
presupuesto activo durante la espera. Solo hay una inferencia externa a la vez.
La auditoría enlaza recepción, cola, admisión e intentos externos mediante
`client_request_id`. `provider_response` identifica el estado del proveedor;
los rechazos de admisión y errores HTTP del controlador registran su origen,
estado y motivo. Un rechazo local no se confunde con cuota del proveedor.
Si se agota esa espera local, el run se interrumpe por `infrastructure_failure`
y devuelve HTTP 503 con `local_model_queue_timeout`. Los errores explícitos de
cuota del proveedor detienen la ejecución sin reintentar ni rotar cuentas/modelos.
La salida con error del agente produce estado `failed`; una cancelación manual,
`cancelled`. `stop_reason` conserva siempre la causa concreta.

## Validar desde el agente

```sh
gtoc status --json
gtoc evaluate /workspace/solution.txt --json
gtoc docs https://docs.python.org/3/library/math.html
gtoc tools
gtoc packages
/opt/conda/bin/python -m pip install --user cma
```

El agente recibe el enunciado y las instrucciones desde `/problem`. Cada
evaluación copia los bytes al host antes de ejecutar el validador con otro UID
dentro del mismo contenedor. Se conserva cada candidato aceptado y su veredicto,
mejore o no el máximo. Solo puede haber una validación pendiente.

Para GTOC12, las instrucciones orientan a reservar unos pocos segundos por candidato pequeño
y unos 10–30 segundos para flotas mayores en el entorno emulado observado,
dependiendo del tamaño, hardware y carga. El límite del binario es 120 segundos;
el supervisor dispone de 135, el transporte de 150 y el cliente de 180.
Para GTOCX, GTOC11 y GTOC13, los límites son 600, 615, 630 y 660 segundos, respectivamente;
las trayectorias de vela pueden requerir bastante más cálculo que las balísticas.
Los límites efectivos se registran en `audit-policy.json` y el agente debe
configurar un timeout de herramienta suficiente. La validación consume tiempo.

La instantánea de código se solicita después del veredicto y se procesa en una
cola independiente y acotada. Si no puede capturarse, se declara su ausencia;
el candidato ya permanece guardado. Los eventos registran tiempos por fase.
Un timeout del transporte interrumpe el run para impedir validaciones solapadas;
los resultados tardíos se conservan, verifican y pueden corregir el error
provisional sin duplicar el candidato en las curvas. Véase la
[documentación de la corrección](docs/validation-and-tools.md).

Con `--json`, los errores de operación también se escriben como JSON en stdout:
`status: "error"`, `score: null`, `diagnostics`, `error`, `code`, `http_status`
y `retryable`. Una petición solapada recibe `code: "evaluation_busy"` y
`retryable: true`; el agente debe esperar a que termine la evaluación pendiente
y comprobar el presupuesto antes de reintentar. El CLI no reintenta por su cuenta.
Los códigos de salida son 0 para un candidato válido, 2 para un candidato
inválido, no verificable o un error del validador, y 1 para un error de operación. Sin `--json`,
los errores de operación siguen apareciendo como texto en stderr.
`gtoc status --json` usa el mismo formato de errores.

El binario heredado de GTOC12 devuelve código cero para algunos errores. Su wrapper exige
un mensaje de éxito, ausencia de errores y una masa finita no negativa. El
binario también interpreta un salto de línea final como una línea vacía adicional:
la solución debe terminar en el último número. No se normalizan sus bytes en secreto.
`gtoc validate` es una comprobación local independiente: no registra un resultado
de benchmark ni sustituye a `gtoc evaluate` dentro de una ejecución.

## Archivos de resultados

```text
results/runs/<run_id>/
  manifest.json             configuración pública, imagen y hashes
  events.jsonl              registro canónico con secuencia y tiempos
  status.json               estado actual derivado
  summary.json              estado final, consumo y mejor puntuación
  finalization.json         fases de cierre, tiempos, errores y parada confirmada
  hardware.json
  preflight.json
  provider-model.json       catálogo del modelo seleccionado, en modo ChatGPT
  codex-model-catalog.json  catálogo completo suministrado a Codex, cuando corresponde
  model-compatibility.json  resolución de modelo/razonamiento por el cliente fijado
  prompt.txt
  submissions/<id>/         solution.txt.gz, receipt.json, verdict.json, report.txt
  requests/<id>/            parámetros, consumo, solicitud original y efectiva, respuesta
  agent/                    transcript.jsonl, stdout.log y stderr.log
  native-client/            configuración MCP y tools.jsonl de Claude oficial, sin credenciales
  workspace/                código, archivos y sesión del cliente
  snapshots/                copias del texto del workspace y enlace a cada envío
  resources.jsonl           muestras de CPU, memoria, procesos, E/S, disco y GPU
  gpu-allocation.json       política GPU y dispositivos NVIDIA detectados/asignados
  workspace-manifest.json   hashes de los archivos finales legibles, con omisiones explícitas
  workspace-usage.json      medición completa al cerrar, o última completa y fallo explícito
  container-state.json      estado final de Docker, incluido OOMKilled
  audit-policy.json         alcance, límites y frecuencia de la observación
  downloads/docs/           contenido documental consultado, identificado por hash
  exports/progress.csv      puntuación y máximo acumulado por tiempo y tokens
  exports/audit.html        informe de auditoría local, sin scripts ni recursos externos
  exports/audit-summary.json recuentos completos y alcance de la vista compacta
  integrity.json
```

Los envíos usan escrituras atómicas y sincronización antes de confirmar su
aceptación. Los eventos tienen un único escritor serializado. Una cola parcial
del registro se conserva en `recovery/` al recuperarlo y no se trata como un
evento confirmado. Los identificadores de envío permiten reintentar transporte
sin duplicar resultados ni sustituir bytes ya aceptados.

Los archivos son la fuente de verdad. `analyze` reconstruye el CSV a partir de
los eventos. Esta fase no necesita SQLite. Los directorios son privados por
defecto y no deben publicarse completos sin revisar su contenido.
El manifiesto y las peticiones identifican `auth_method`, `billing_mode` y,
para las suscripciones, un identificador opaco de cuenta. Su `actual_cost` queda en `null`:
los tokens de suscripción no se convierten en coste real cero ni en facturación
API. Un cálculo futuro de precio API equivalente será una métrica separada.

## Comparar evaluaciones

El informe comparativo se genera automáticamente desde los resultados guardados:

```sh
uv run --extra analysis gtoc compare --results results --problem gtoc12 \
  --wall-seconds 28800 --output analysis/comparisons/current
```

Produce un HTML compacto, PNG/SVG y CSV con media de los máximos por par
agente–modelo y dispersión muestral; curvas por tiempo, tokens y precio; y tabla
histórica con los pares destacados. Cada puesto histórico cuenta únicamente
equipos humanos, sin desplazar otros resultados agénticos. Con n=1 no se estima
dispersión. Las continuaciones siempre forman una evaluación lógica y las
copias del mismo run se excluyen.

Todos los pares del mismo problema y presupuesto comparten gráficas, conservando
sus condiciones de máquina y pausas. Cada figura cuenta las series incluidas y
explica las ausentes; `coverage.csv` conserva ese detalle. La generación funciona
sin red ni credenciales de modelos. Para incluir GTOC 12 y GTOC 13 juntos, omite
`--problem gtoc12`.

Para modelos probados con varios harness, añade automáticamente una comparación
de la media de máximos por harness, con n, SD muestral y duración observada.
Requiere el mismo modelo, razonamiento y presupuesto; las continuaciones cuentan
una sola vez. Las tablas se exportan también en `harnesses.csv`.

Las tarifas se fijan en `analysis/prices.yaml`, con fecha y copia por informe:
son equivalentes API, no facturas de suscripción. Problemas, presupuestos,
versiones y razonamiento se distinguen. Las diferencias de hardware y las
incertidumbres de uso permanecen visibles. Véase la
[documentación del comparador](docs/comparative-analysis.md).
El informe generado se abre en `analysis/comparisons/current/index.html`.
Los experimentos privados y los informes derivados no se incluyen en Git.

## Auditar una ejecución

La auditoría se activa en todas las ejecuciones. Al terminar se genera
`exports/audit.html`; `uv run gtoc analyze ID_DE_EJECUCION` lo reconstruye junto
con el CSV. El informe enlaza el prompt inicial, configuración, actividad del
agente, herramientas, solicitudes al modelo, recursos, incidentes y copias del
código. En ejecuciones antiguas solo muestra la evidencia que ya existía: no
reconstruye retrospectivamente las nuevas trazas.

El HTML es una vista compacta: muestra hasta 200 registros por sección y enlaza
el detalle completo, sin expandir manifiestos de capturas. Los eventos y las
trazas se leen secuencialmente; las vistas previas tienen tamaño acotado. Si la
evidencia está archivada, los enlaces apuntan a `evidence.tar.gz`.

`agent/transcript.jsonl` conserva los eventos nativos de los clientes con una
envoltura común: UTC, tiempo de recepción del controlador, contador de tokens,
canal, agente y clasificación de la actividad. Codex usa `exec --json` y Claude
`stream-json`. Las salidas de herramientas pueden estar limitadas por el propio
cliente; una llamada sin resultado final se señala como pendiente al cerrar.
Los fragmentos incompletos se identifican y no se presentan como eventos JSON
completos. El límite combinado de salida del cliente es 64 MiB; alcanzarlo
detiene la ejecución con `output_limit`. Una línea mayor de 16 MiB también
detiene la ejecución y deja constancia de la omisión.

Cuando se utiliza la pasarela, cada intento conserva `client_request.json`,
`request.json` y `response.jsonl`: solicitud del agente, solicitud efectiva al
proveedor y fragmentos de respuesta con tiempo de recepción. No se guardan las
cabeceras de autenticación. La respuesta tiene un máximo de 64 MiB de traza por
intento; cualquier exceso o interrupción queda declarado en `metadata.json`.
Las credenciales conocidas y los campos habituales de autenticación se
ocultan antes de persistir contenido. Los archivos siguen siendo privados:
la eliminación de secretos no sustituye una revisión antes de publicarlos.

Con suscripción de Claude, la conversación se observa mediante la salida del
cliente oficial, sin interceptar su OAuth ni su tráfico de inferencia. El
servidor MCP escribe además `native-client/tools.jsonl` en el host: comando,
inicio, resultado, código de retorno, timeout y truncamiento. Un inicio sin
resultado también puede indicar que el presupuesto terminó durante la llamada.

El cliente nativo solicita los eventos parciales del stream para confirmar el
consumo por respuesta, sin tratar como definitivos los contadores de cada bloque.
`native-usage.json` distingue respuestas pendientes, totales y consumo sin
atribución temporal. Véase la [corrección del registro de tokens de Claude](docs/claude-token-accounting.md)
para interpretar las curvas nuevas y las limitaciones de las pruebas anteriores.

El supervisor protegido toma muestras aproximadamente cada dos segundos:
contadores de CPU y throttling, memoria y eventos OOM del cgroup, procesos del
agente, E/S, tamaño del workspace y contadores NVIDIA cuando estén disponibles.
Son medidas del contenedor, incluido el coste del supervisor y del validador;
los contadores GPU pueden ser de todo el dispositivo y se etiquetan así. Un
recurso no disponible queda indicado, no se convierte en cero uso.

Se capturan archivos de texto del workspace al inicio, aproximadamente cada diez
segundos, después de validar un envío y al cerrar. Cada envío referencia su copia observada; los
contenidos se deduplican por hash y los manifiestos posteriores guardan solo
cambios y eliminaciones. No se pausa al agente, por lo que esa copia
no constituye una instantánea atómica ni prueba qué versión produjo la solución.
Se registran omisiones: archivos que cambian al leerlos, binarios, enlaces,
archivos mayores de 256 KiB, más de 4 MiB de lectura sin caché por captura o más
de 20.000 entradas. Una captura pendiente agrupa solicitudes repetidas y conserva
hasta 256 disparadores distintos con todos los identificadores de envío. Los
envíos agrupados comparten una observación posterior; no se capturan todos los
estados intermedios. Una copia omitida por saturación o cierre queda indicada
en los eventos y su enlace. Una enumeración incompleta no infiere eliminaciones.
Se excluyen entornos instalados, caches y sesiones (`.local`, `.codex`, `.claude`,
`.git`, `.venv`, `node_modules`, `__pycache__`). El workspace final permanece
disponible; su inventario se calcula después de detener el agente, sin seguir
enlaces, con un máximo de 100.000 entradas y omisiones explícitas.

El tamaño del workspace se mide por separado: bytes lógicos de archivos
regulares, incluidas cachés e instalaciones, sin seguir enlaces ni aplicar el
límite de entradas del inventario. Solo se publica una medición completa; se
recalcula tras detener el contenedor y se conserva la última completa si falla.
La [documentación de las correcciones](docs/observability-and-model-metadata.md)
explica las garantías, los límites y la lectura de capturas actuales e históricas.

Las trazas, solicitudes, copias, inventario y estado final entran en
`integrity.json`. El agente solo puede escribir su workspace; no puede modificar
estos registros del host. Esto permite auditar la actividad observable, pero
no registra cada llamada al sistema, procesos o archivos fugaces entre muestras,
ni razonamiento interno que el proveedor no expone. La observación y sus límites
son iguales para las configuraciones comparadas y consumen recursos medidos.

## Aislamiento y alcance de esta fase

El contenedor tiene `--network none`, raíz de solo lectura, límites de memoria
y procesos, y no recibe sockets Docker, claves ni directorios personales del
operador. Un pequeño proceso protegido transmite solicitudes por stdin/stdout
de Docker. Solo ese proceso conserva las capacidades necesarias para cambiar
de UID, preparar el workspace y terminar procesos; el agente ejecuta con UID
1000 sin capacidades. El validador usa UID 1001 y un temporal privado.
El supervisor puede acceder a los directorios privados del agente para medir
el workspace, incluso cuando el usuario del host tiene otro UID. Al cerrar el
contenedor devuelve la propiedad de los archivos al propietario original del
workspace, sin seguir enlaces simbólicos. Los procesos del agente mantienen sus
capacidades vacías. Los volcados de memoria por fallo están deshabilitados.
SWE-agent dispone además de un `/root` temporal propiedad del UID 1000 porque
su distribución fija allí el estado de herramientas; esto no le concede root.
Con suscripción de Claude, solo el cliente oficial y el servidor MCP permanecen
en el host; el código propuesto por el modelo sigue ejecutándose en el contenedor.

La pasarela ofrece rutas concretas de modelos, documentación y un catálogo
reducido de paquetes PyPI. No ofrece CONNECT ni un proxy general. Se bloquean
herramientas web alojadas por los proveedores y las URL de documentación con
destinos, rutas o redirecciones no autorizados. La política exacta queda guardada
con cada ejecución. La pasarela también rechaza descargas remotas de archivos o
imágenes a través del proveedor, servidores MCP remotos e inferencia en segundo
plano. Las claves se mantienen fuera del contexto de construcción.

`gtoc packages` permite inspeccionar el catálogo antes de instalar. Los rechazos
por política tienen HTTP 403 y `code: "package_not_allowed"`; no significan que
el paquete no exista en PyPI. Pip puede mostrar un mensaje genérico, por lo que
las instrucciones explican cómo consultar la causa y los paquetes admitidos.

Esta entrega admite **ejecuciones de desarrollo**, no certifica resultados
oficiales. El límite del workspace se supervisa periódicamente y no es una
cuota dura de sistema de archivos; una campaña oficial necesitará esa cuota y
verificación del aislamiento y del hardware en su servidor de destino. Docker
comparte kernel; el preflight GPU comprueba acceso y cálculo, no certifica por sí
solo el aislamiento del controlador o la exclusividad física de las GPU.

Quedan para los incrementos siguientes: MCP de documentación versionada,
campañas con repeticiones y sustituciones, recuperación supervisada, perfiles de
inferencia Ollama, certificación de rankings oficiales, intervalos estadísticos
y precios de factura, e índice SQLite opcional. Las comparativas descriptivas
de medias, dispersión, progreso, precio API equivalente y posición histórica
están disponibles mediante `gtoc compare`.

## Pruebas

```sh
uv sync --locked --group dev
uv run ruff check src tests
uv run pytest -q -m 'not docker'
GTOC_TEST_IMAGE=gtoc-bench:phase1-cpu uv run pytest -q -m docker
GTOC_TEST_CUDA_IMAGE=gtoc-bench:phase1-cuda uv run pytest -q tests/test_gpu_runtime.py
```

Las pruebas de Docker utilizan las distribuciones reales de los cinco agentes y un
proveedor HTTP local simulado. No necesitan claves reales ni hacen llamadas
facturadas. Comprueban validación nativa, separación de permisos, red, conservación
de candidatos, servicio estándar y parada por tokens. También se prueba que el
lanzador pueda salir, el corte temporal detenga procesos secundarios y la pérdida
del controlador detenga el contenedor. Los agentes ejecutan además una
herramienta de shell que consulta el presupuesto, envía una solución y devuelve
el veredicto al modelo simulado. El caso válido tiene masa cero y procede
de la efeméride de la Tierra del enunciado; no es una solución de
un participante. Antes de campañas científicas deberán añadirse casos de
referencia de masa positiva y de las distintas restricciones físicas.
Las pruebas de autenticación simulan el navegador y el proveedor OAuth,
verifican firmas OIDC, estado, nonce, PKCE y rotación entre procesos, y comprueban
que los errores no filtren tokens. La integración con los cinco agentes también ejercita
una renovación OAuth simulada durante el ciclo de herramientas. No sustituyen
una autorización y ejecución reales con tu cuenta.

La prueba local explícita siguiente **consume cuota real** de la cuenta autorizada.
Usa exactamente `gpt-6-luna`, razonamiento `low` y servicio estándar. Ejecuta los
cinco agentes secuencialmente, con dos casos por agente: corte a los 35 segundos
y corte al superar 64 tokens, con un techo temporal de 90 segundos. El umbral
de 64 no limita el consumo a 64: la primera respuesta puede contabilizar miles
de tokens del contexto del agente. Se conserva el exceso observado.

```sh
uv run python tests/live_smoke.py
# Un caso concreto:
uv run python tests/live_smoke.py --agent codex --case time
```

Los resultados quedan en un directorio nuevo `results/live-smoke-*/`, con
`report.json`, configuraciones y todos los intentos. El caso temporal valida
el fixture de masa cero, consulta el presupuesto y deja procesos secundarios
activos para comprobar su terminación. El prompt diagnóstico se conserva y se
identifica como una sustitución explícita del prompt del problema. Estas
pruebas verifican infraestructura; no miden capacidad de optimización ni
entran en el ranking.
Para probar el perfil Claude del host, instala antes el binario con
`uv run gtoc install-agent claude_code`; esos casos se omiten si no está instalado.
Se comprueba que anuncie únicamente la herramienta MCP del contenedor, ejecute
sin credenciales ni acceso a archivos del host, registre una solución y respete
el corte por tokens. También se verifica la parada del cliente al perder el
controlador y la interrupción acotada ante una respuesta HTTP 429 del proveedor.
