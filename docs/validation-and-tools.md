# Validación y herramientas: revisión del 30 de octubre de 2026

## Evidencia y problema corregido

En la continuación exploratoria `20261030T115001-e11ce7031f5e` se observaron
27 respuestas `Validation transport timed out` durante la revisión. La copia
de auditoría se ejecutaba antes del validador, pero el controlador contaba ambas
operaciones dentro de su espera de 42 segundos. En los últimos casos revisados,
la instantánea consumió entre 14 y 29 segundos. La emulación y la carga de CPU
pueden aumentar los tiempos; no se atribuye cada incidencia a una causa interna
del binario que los registros antiguos no permiten demostrar.

El supervisor ahora envía primero el veredicto y solicita después una
instantánea asíncrona. Un trabajador independiente procesa una cola de ocho
instantáneas; las muestras de recursos tienen otro hilo. Los límites de copia
son 256 KiB por archivo y 4 MiB por instantánea. Si la cola se llena o cierra,
se registra `observation_skipped` y el enlace del envío declara la ausencia.
Estas instantáneas son observaciones posteriores a la validación, sin pausar
al agente: no representan exactamente el estado de sus programas al enviar.
Los bytes del candidato se conservan antes de validar, independientemente de
la disponibilidad de la instantánea.

## Tiempo orientativo y límites

Las instrucciones indican unos pocos segundos para candidatos pequeños y
aproximadamente 10–30 segundos para flotas mayores en el entorno exploratorio
emulado observado. No constituye una garantía ni una calibración para otra
máquina. El agente debe reservar tiempo para recibir feedback y puede consultar
`gtoc status --json`; el tiempo de validación consume su presupuesto.

| Capa | Límite |
| --- | ---: |
| Binario nativo: tiempo y CPU | 120 s |
| Proceso supervisor aislado | 135 s |
| Espera de transporte del controlador | 150 s |
| Cliente `gtoc evaluate` / `gtoc validate` | 180 s |

Los límites se centralizan en `validator.py`. El límite nativo sube de 30 a
120 segundos para dar margen a soluciones mayores y equipos más lentos.
`audit-policy.json` conserva los límites efectivos. Las instrucciones recomiendan
al agente usar una herramienta de shell con al menos 180 segundos de timeout.

`validation_requested` y `validation_phase` registran solicitud, inicio y fin;
el resultado incluye `validation_seconds` y `supervisor_seconds`. Los errores
separan `native_validator`, `validator_supervisor` y `controller_transport`.
Si falla o excede el límite el supervisor, se mata su grupo de procesos completo,
incluido el binario nativo, antes de liberar el validador.

Un timeout del transporte interrumpe el run como fallo de infraestructura e
impide admitir nuevas evaluaciones. Un resultado tardío se conserva como
`validation_late_verdict`, se verifica contra el hash del candidato y actualiza
el veredicto canónico. La respuesta provisional se archiva en
`submissions/<id>/transport-error.json`; el historial de eventos conserva ambas
observaciones. Las curvas usan el último veredicto por identificador, evitando
contar dos veces el candidato. Un resultado válido tardío conserva el instante
de aceptación original y puede mejorar la puntuación de ese intento interrumpido.

## Herramientas disponibles

Se mantienen Python científico en `/opt/conda/bin/python`, PyKEP, PyTorch,
NumPy, SciPy, NLopt e IPOPT del lock de Conda. Se añaden versiones fijadas:

| Herramienta | Versión | Función general |
| --- | --- | --- |
| CasADi | 3.8.2 | Modelado, derivadas y acceso a solvers |
| cyipopt | 1.7.0 | Interfaz Python del IPOPT ya instalado |
| Numba / llvmlite | 0.68.1 / 0.50.0 | Compilación de kernels Python |
| pybind11 | 3.1.0 | Integración C++ y Python |
| Cython | 3.3.0 | Compilación de extensiones; construcción de cyipopt |
| Zig | 0.17.0 | Compilador Zig y herramientas C/C++ ya incorporadas |

`science-tools.lock` fija hashes de los wheels Linux x86-64 para Python 3.12.
`science-source.lock` fija el archivo fuente de cyipopt: se compila durante la
construcción contra NumPy e IPOPT existentes, sin actualizar el solver ni
resolver otras dependencias. GCC/G++, CMake y `pkg-config` están disponibles;
`PKG_CONFIG_PATH=/opt/conda/lib/pkgconfig` y el PATH incluye `/usr/sbin` y `/sbin`.
Las bibliotecas IPOPT están en `/opt/conda/lib`, sus cabeceras en
`/opt/conda/include/coin-or`.

Las instrucciones oficiales de [CasADi](https://web.casadi.org/get/) y
[cyipopt](https://cyipopt.readthedocs.io/en/stable/install.html) describen sus
instalaciones y dependencias. La selección incorpora herramientas generales
observadas como necesarias; no incluye código de participantes ni estrategias
específicas de GTOC.

`gtoc tools` muestra las versiones y rutas del entorno en que se ejecuta.
El preflight guarda ese inventario en `preflight.json`; la imagen también
incluye `/opt/science-tools.json` y sus locks de instalación.

## Paquetes y documentación permitidos

El catálogo admite CasADi, cyipopt y Cython, además de Numba, pybind11 y Zig.
Las rutas oficiales de documentación de CasADi, cyipopt, IPOPT, NLopt y pybind11
se añaden a la pasarela. Se conservan las restricciones de red y de soluciones
históricas.

`gtoc packages` consulta el catálogo de paquetes y prefijos de documentación
del run. Un paquete denegado devuelve HTTP 403, `code: package_not_allowed`,
el motivo y la lista permitida. Pip puede ocultar ese cuerpo y mostrar
`No matching distribution`: las instrucciones explican cómo distinguirlo
consultando el catálogo, sin insistir con instalaciones imposibles.

## Aplicación

Los cambios requieren reconstruir la imagen y desplegar el mismo código del
controlador. La imagen local de verificación usa la etiqueta
`gtoc-bench:validation-tools`. La prueba de spark1 conserva su imagen, política
y código originales; estos cambios corresponden a futuras evaluaciones.
La revisión cambia herramientas y límites: debe conservarse su imagen y
manifiesto al comparar resultados de distintos perfiles.

## Verificación local

- 191 pruebas unitarias pasadas, incluyendo auditoría lenta, limpieza del grupo
  de procesos, diferenciación de timeouts, rechazo de nuevos envíos tras fallo
  de transporte y promoción verificada de un resultado tardío.
- 12 casos de integración pasados: validador nativo determinista, solvers y
  compiladores sin red, y envío/feedback con los clientes de agentes y protocolos
  disponibles. Los casos Codex y Claude se comprobaron de nuevo con `gtoc tools`
  y `gtoc packages` ejecutados por sus herramientas.
- CasADi y cyipopt resolvieron un problema cuadrático; Numba compiló un kernel
  y G++ compiló y ejecutó un programa como UID 1000 con filesystem de solo lectura
  y sin red. Cyipopt se construyó contra el IPOPT del entorno fijado.
- Ruff: comprobaciones y formato correctos. Los proveedores de modelos de las
  pruebas fueron simulados; no se utilizaron credenciales ni inferencia externa.

Imagen local verificada:
`gtoc-bench:validation-tools` →
`sha256:30a721da7fbb1a9efb2879015f9dfca8d52818feb64acb6ab5c102fcd7334e77`.
