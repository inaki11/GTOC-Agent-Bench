# Correcciones de la prueba Astra de GTOC13

La ejecución `20261104T132329-a802c331c1e3` completó sus ocho horas activas con
`stop_reason: time_limit` y mejor puntuación de 217,44709167273894. Sus archivos
sellados se conservan sin cambios. Esta revisión corrige tres problemas del
benchmark para las ejecuciones nuevas.

## Incertidumbre de un extremo balístico

El candidato `32a97568738347fda4204613019f3ed2` recibió `invalid` por
`CONIC_DYNAMICS`. El residuo de posición era 0,00613465641 km frente al límite
de 0,1 km; el de velocidad, 5,37779193e-8 km/s frente a 1e-7 km/s. El margen
numérico de velocidad era 2,18008331e-7 km/s: no permitía verificar el extremo,
pero tampoco demostraba una infracción física.

`Checker.endpoint` comprobaba correctamente la aceptación conservadora, pero
usaba `invalid` para todos sus rechazos. Ahora conserva exactamente el mismo
criterio de aceptación: residuo más margen dentro de ambas tolerancias.
Si residuo menos margen supera algún límite, devuelve `invalid`; si el
intervalo solapa un límite, devuelve `unverified`. Ambos rechazos mantienen
`score: null`. No se modifica el objetivo ni se amplían las tolerancias.

Los hashes del juez y de la política de validación se actualizan en la
procedencia de GTOC13. Las pruebas incluyen los residuos de este candidato,
otro candidato de Astra que sí incumplía la tolerancia y casos a ambos lados
de los límites. No se reetiquetan los veredictos de la ejecución original.

## Cachés del entorno científico

El preflight importaba bibliotecas como UID 1000 cuando el workspace todavía
pertenecía al supervisor. La preparación de su propietario ocurría más tarde,
justo antes de iniciar el agente. Matplotlib no podía crear
`/workspace/.config/matplotlib`; Fontconfig tampoco podía escribir su caché.
Matplotlib recurrió a un temporal. La prueba no se detuvo por este motivo.

El supervisor prepara ahora el workspace antes del preflight científico. Un
proceso con UID/GID `1000:1000`, sin grupos adicionales y con umask 077, crea o
repara los directorios del agente. Las rutas se fijan explícitamente:

| Variable | Ruta |
| --- | --- |
| `XDG_CONFIG_HOME` | `/workspace/.config` |
| `XDG_CACHE_HOME` | `/workspace/.cache` |
| `MPLCONFIGDIR` | `/workspace/.cache/matplotlib` |
| Caché de Fontconfig | `/workspace/.cache/fontconfig` |

Estos directorios tienen modo 0700 y pertenecen al agente. Se rechazan enlaces
simbólicos en las rutas de caché; la creación y los cambios de modo se ejecutan
con los permisos del agente. El preflight, los seis adaptadores y las
herramientas MCP de Claude comparten la misma configuración. Las cachés siguen
dentro del workspace y quedan sujetas a su guarda de espacio y aislamiento.

Las pruebas de contenedor importan Matplotlib y Fontconfig y generan un PNG
como UID 1000, con raíz de solo lectura. Cubren un workspace vacío y cachés
preexistentes propiedad de root; comprueban propietario, permisos y ausencia
de los mensajes de caché no escribible.

## Recuperación resuelta frente al cierre del experimento

La última sobrecarga ya se había recuperado. Al cancelar una petición posterior
por tiempo, el bloque de cierre reutilizaba el registro persistente del episodio
anterior y escribía `state: stopped` y `outcome: recovery_stopped`. El
`cause: server_is_overloaded` restante podía atribuirse erróneamente al cierre.
El resumen y los eventos originales sí registraban correctamente `time_limit`.

`provider-recovery.json` conserva ahora `state: recovered` y
`outcome: recovered` para un episodio completado. El cierre solo actualiza una
recuperación pendiente perteneciente a la petición actual. Tampoco sobrescribe
una recuperación si el límite vence al entregar su respuesta ya completada.

El cierre de la pasarela se registra por separado en `gateway-stop.json`, con
`scope: run_termination`, motivo, fecha y estado del último episodio. Ese archivo
se sella en `integrity.json` y queda enlazado en la auditoría. Una causa histórica
de sobrecarga ya no sustituye al motivo de terminación del experimento.

Las regresiones cubren una recuperación seguida de cancelación por tiempo,
tokens u operador, y el límite que vence durante la entrega del turno recuperado.
Son pruebas con proveedores simulados y no consumen inferencia real.

## Comprobaciones

```sh
uv run pytest tests/test_gtoc13.py tests/test_provider_wait.py tests/test_agent_caches.py
GTOC_TEST_IMAGE=gtoc-bench:astra-corrections-20261110 \
  uv run pytest tests/test_agent_caches_docker.py tests/test_gtoc13_docker.py
```

La publicación en cada servidor necesita controlador e imagen de la misma
revisión. Las ejecuciones congeladas que estuvieran en curso conservan su
código, imagen, juez y materiales originales.
