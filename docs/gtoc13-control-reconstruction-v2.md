# GTOC13 v2: reconstrucción de controles y compatibilidad

Revisión `gtoc13-reconstructed-controls-v2`, 31 de octubre de 2026.

## Problema que corrige

La revisión v1 rechazaba `antipodes_submitted.csv` en la segunda fila. Usaba
una normal de vela interpolada linealmente entre los extremos de cada intervalo.
El error de velocidad del primer intervalo era 0,143375 mm/s, superior a
0,091493 mm/s, el equivalente del límite relativo 1e-4 para ese intervalo.
DOP853 y Radau reproducían el desajuste: no era simplemente un error de RK4.

El enunciado permite al host calcular el control interior minimizando el error.
Por ello la interpolación fija añadía una restricción que podía excluir una
entrega aceptada. Además, v1 exigía que un arco de vela completo, propagado sin
reinicios, cerrase con los límites absolutos de los arcos cónicos. Aplicaba esos
límites también al estado propagado al llegar a un flyby. Esa condición global
era adicional a la tolerancia relativa por intervalo publicada.

Fuentes primarias: [enunciado oficial](https://gtoc.jpl.net/static/gtoc13/gtoc13_problem_statement.pdf),
[formato de entrega](https://gtoc.jpl.net/static/gtoc13/gtoc13_submission_format.pdf)
y [resultado publicado por Antipodes](https://arxiv.org/html/2607.10150v2#S6.SS1).

## Qué hace v2

Para cada intervalo positivo de vela, los vectores unitarios enviados n0 y n1
permanecen en los extremos. El tiempo normalizado es s en [0,1]. Se consideran:

```text
n_raw(s) = (1-s)*n0 + s*n1 + 4*s*(1-s)*(a*p + b*q)
n(s) = n_raw(s) / norm(n_raw(s))
```

p y q forman una base ortonormal perpendicular a normalize(n0+n1). Se construyen
determinísticamente a partir del eje cartesiano menos alineado con ese vector.
La corrección se anula exactamente al inicio y al final. Los parámetros a y b
están acotados a [-1,1]. No se cambian los tiempos, estados, flags o normales
extremas del archivo.

Se comienza con a=b=0. Si el residual RK4 no queda por debajo de una quinta parte
de la tolerancia publicada, se minimizan los residuales escalados de posición y
velocidad usando `scipy.optimize.least_squares`. Se usa `max_nfev=48`; el contador
de SciPy excluye las evaluaciones adicionales del Jacobiano por diferencias
finitas. Todas las llamadas comprueban el presupuesto global de 600 segundos.
El umbral de una quinta parte decide si intentar un ajuste; la condición de
aceptación sigue siendo estrictamente la tolerancia publicada.

Una vez elegidos los parámetros, el perfil se fija. No se optimiza de nuevo
durante la verificación. Tiene que superar:

1. Un paso RK4 con la orientación inicial, intermedia y final del mismo perfil.
2. Propagación adaptativa DOP853 y propagación independiente Radau del mismo
   sistema físico y con los mismos controles.
3. Los límites de error relativo 1e-4, por separado para posición y velocidad.
4. La comprobación de normal no singular y ángulo de cono válido en todo el
   intervalo. Se buscan todos los extremos de los polinomios de norma y de
   iluminación; no se decide solo con muestras.
5. Las distancias mínimas interiores a Altaira, incluidos los perihelia, y las
   demás restricciones del archivo completo.

Las discrepancias entre DOP853 y Radau consumen el margen disponible; no amplían
las tolerancias. El ajuste numérico no certifica por sí solo la trayectoria.
No hay una lista de nombres, hashes o puntuaciones que conceda aceptación.

Código: [`control.py`](../src/gtoc_bench/gtoc13/control.py) y
[`judge.py`](../src/gtoc_bench/gtoc13/judge.py).

## Contrato numérico y cambio respecto a v1

Cada intervalo de vela parte del estado X0 enviado y comprueba el estado Xf
enviado con la fórmula publicada:

```text
norm(X_predicho - Xf) / norm(Xf - X0) < 1e-4
```

Se comprueban posición y velocidad separadamente, tanto con RK4 como con la
propagación de mayor orden. Un denominador cero impide aceptar el intervalo.
Xf es el nodo numérico aceptado desde el que comienza el intervalo siguiente o
se comprueba el encuentro. Las posiciones de flyby siguen debiendo coincidir
con las efemérides en 100 m y se conservan los límites de v-infinity, altitud,
continuidad, espaciado de encuentros y selección científica.

Se ha eliminado el cierre global adicional de vela de v1. En consecuencia, el
resultado certifica factibilidad bajo este contrato numérico local, no una única
órbita propagada globalmente con error absoluto de 100 m. El límite absoluto
100 m / 0,1 mm/s continúa aplicándose a los arcos cónicos.

Es una diferencia material: en Antipodes el defecto máximo de un intervalo es
**41,63 km de posición y 0,181 m/s de velocidad**, aunque sus errores relativos
son menores que 1e-4. No son los errores entre los dos integradores. Son las
diferencias entre el estado propagado y el nodo enviado. Se registran como
`max_sail_position_defect_km` y `max_sail_velocity_defect_km_s`. También se
registran los máximos errores relativos RK4 y de propagación independiente.
No se presenta ese resultado como una garantía de cierre absoluto global.

La antigua prueba que exigía rechazar la acumulación de pequeños defectos por
el cierre global ha cambiado explícitamente: ahora comprueba que los defectos
dentro de la tolerancia local quedan registrados. Se añade una prueba que supera
el límite local y debe rechazarse. Los saltos de estado entre filas duplicadas,
los impulsos imposibles y los encuentros falsificados siguen siendo rechazados.

## Evidencia con archivos reales

`tests/fixtures/gtoc13/antipodes-submitted.csv` es una copia byte a byte del archivo
suministrado en el proyecto Jack. Hash:

```text
6297881d46e0e1ea238c1cd9a9f612b15ab2e4cb27c880383c41d429a6ad89b8
```

El historial suministrado termina con un registro `pass` del 17-11-2025 a las
12:28 UTC, 133 flybys científicos, Vx 41,582 km/s, TOF 191,177 años y 337,878
puntos oficiales. Las métricas redondeadas del archivo coinciden. El historial
no contiene un hash que permita certificar identidad byte a byte con el host.
La procedencia y esta limitación se conservan en el README de las referencias.

La prueba completa de v2 comprueba los 23.931 intervalos de vela y los 252
encuentros del archivo. Se reconstruyen controles en 3.648 intervalos; los otros
20.283 conservan la interpolación lineal. El resultado es:

| Magnitud | Valor |
| --- | ---: |
| Flybys científicos | 133 |
| Suma científica | 274,4429131566 |
| Bonus de gran tour | 1,2 |
| Bonus temporal del benchmark | 1 |
| Puntuación de benchmark | 329,3314957880 |
| Máximo error relativo RK4 | 9,58435e-5 |
| Máximo error relativo de propagación independiente | 9,69015e-5 |
| Distancia mínima a Altaira | 0,05845014 AU |

La suma científica coincide por redondeo con los 274,443 publicados en el artículo.
La normalización aproximada del leaderboard da 329,33362, una diferencia de
0,00213 puntos respecto al archivo; ambas se muestran como 329,33 con dos decimales.
La fecha publicada al minuto y las métricas redondeadas no establecen por sí
solas identidad de archivo ni la convención temporal exacta del host.

KAIST sigue aceptada con 111,20732894 puntos con c=1 y PlanetX con 21,92704582.
El informe vigente contiene los veredictos completos y las comprobaciones Docker.

## Regresión, aislamiento y reproducción

Las pruebas cubren la referencia Antipodes completa, sus diez primeros intervalos,
un perfil curvo generado independientemente, preservación de extremos, cono
ilegal dentro del intervalo, impulsos artificiales de 1 m/s en las tres componentes,
defectos locales excesivos y una copia del archivo completo con velocidad inicial
corrompida. Esta última debe fallar pese a conservar el nombre y casi todo el archivo.
Se conservan las pruebas de efemérides, flybys, ciencia, perihelia y errores de formato.

```sh
uv run pytest -q -m 'not docker and not slow'
uv run pytest -q tests/test_gtoc13_controls.py -m slow
GTOC_TEST_IMAGE=gtoc-bench:gtoc13-cpu-v2 uv run pytest -q tests/test_gtoc13_docker.py
```

Las pruebas lentas pueden exportar el veredicto completo como propiedad del
informe JUnit: añadir `-o junit_family=legacy --junitxml=/tmp/gtoc13-audit.xml`.
Las referencias de participantes y los leaderboards están fuera de la imagen
del agente; el test Docker comprueba su ausencia. No se ejecutan modelos de pago.

## Versionado y límites

v1 queda archivada en `gtoc13-validation-v1.md` y
`gtoc13-reference-checks-v1.json`. v2 tiene identificador de revisión, hashes de
código y de materiales propios, y una imagen con etiqueta nueva. No se modifican
veredictos anteriores. Para comparar revisiones hay que identificarlas o volver
a evaluar los mismos bytes de cada candidato.

La reconstrucción cuadrática no reproduce el código privado de JPL ni demuestra
equivalencia para todo archivo posible. Puede no encontrar un control que sí
encuentre el host. En ese caso devuelve `unverified` sin puntuación. Una entrega
confirmada como válida que falle deberá incorporarse como regresión y motivar
una mejora del método, nunca una excepción por nombre o una puntuación concedida
sin comprobarla. La tasa de falsos aceptados frente al host sigue sin medirse.
