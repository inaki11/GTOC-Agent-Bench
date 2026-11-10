# Imágenes y comprobación del juez

La incidencia de SWE-agent + Sol se debió a una actualización ad hoc: copiar
`problems/gtoc12/` volvió a poner el binario en modo `0644`. El SHA-256 seguía
siendo correcto porque no incluye permisos ni propietario. La receta principal
ya ajustaba esos permisos, pero aquella imagen derivada no lo hacía.

Se conserva una única rutina pequeña, `environments/finalize-image.py`, que
ejecutan las tres recetas después de la última copia de materiales:

- `Dockerfile`: construcción completa con versiones fijadas.
- `Dockerfile.update`: actualización del código, problemas y utilidades,
  conservando el perfil de agentes y la variante CPU/CUDA de la base.
- `Dockerfile.qemu`: perfil de emulación de desarrollo.

Los jueces pertenecen a `root:1001`; sus directorios tienen modo `0750`, los
ejecutables `0550` y los demás archivos protegidos `0440`. Los binarios del
repositorio pueden tener permisos distintos: la instalación en la imagen es
quien establece los permisos Linux efectivos. No se cambia el contenido de
los validadores ni sus hashes oficiales.

```sh
uv run gtoc build --base-image gtoc-bench:phase1-cpu --tag gtoc-bench:actualizada
```

La opción requiere una etiqueta de salida explícita. No permite cambiar
agentes o variante CUDA durante una actualización: para ello se utiliza la
construcción completa. Es necesario actualizar también el controlador; el
preflight compara el código y los materiales del host con los del contenedor.

Antes de arrancar el agente, el supervisor comprueba propietario, grupo y modos
del juez seleccionado. En GTOC 12 ejecuta además los controles `valid.txt`
(puntuación cero) e `invalid.txt` con el mismo lanzador del validador, como
UID/GID 1001, sin grupos adicionales, sin red y dentro del contenedor de solo
lectura. Son dos archivos de unos 250 bytes; cada control tiene un timeout de
diez segundos y termina antes de empezar el presupuesto o llamar al modelo.
No se añaden pruebas costosas de soluciones históricas a cada arranque.

Los montajes temporales empiezan vacíos. En Podman se usa `notmpcopyup`, igualando
el comportamiento de Docker: copiar las cachés de construcción puede llenar el
montaje, y copiar enlaces antiguos en `/problem` impide preparar los materiales.
SWE-agent recibe además un `/root` temporal de 128 MiB; el supervisor asigna ese
directorio al UID 1000. No se amplía el límite ni se expone el `/root` del host.

`validator-preflight.json` conserva el resultado, también si falla. Se incluye
en el sello de integridad y se enlaza desde la auditoría. Un fallo aborta como
`infrastructure_failure` sin iniciar el agente. Durante la evaluación, un
`validator_error` conserva primero candidato, diagnóstico y veredicto, y después
detiene el run como infraestructura; un candidato `invalid` permite continuar.

La prueba Docker de regresión construye una imagen que elimina deliberadamente
el permiso de ejecución (`0644`), selecciona SWE-agent y comprueba cero peticiones
al proveedor y cero tokens. La prueba positiva ejecuta ambos controles y el
cliente Codex contra un proveedor local simulado. No utiliza cuentas reales.

Para desplegar se preservan resultados, credenciales y copias de las revisiones
anteriores. Las imágenes nuevas se construyen y verifican antes de cambiar las
etiquetas de uso habitual. No se actualiza el código de un experimento activo.
