# Informes comparativos

Este directorio recibe los informes generados a partir de los experimentos del
operador. Los resultados privados y los informes derivados no se versionan en
Git. Una copia nueva del repositorio incluye el generador y la plantilla de
precios; las gráficas se producen cuando hay runs disponibles.

El generador integrado reconstruye las cuatro figuras comparativas y la tabla
histórica a partir de los datos canónicos:

```sh
uv run --extra analysis gtoc compare --results results \
  --wall-seconds 28800 --output analysis/comparisons/current
```

El informe se abre en `current/index.html` y contiene todos los pares disponibles
con presupuesto de ocho horas. Cada ejercicio tiene un panel
común; las políticas de pausas y las condiciones de recursos no separan pares.
Cada gráfica indica cuántas series representa y explica los datos ausentes.
La generación es local, sin credenciales ni llamadas a modelos. Cada cadena
cuenta como una evaluación; con n=1 por par, la dispersión no es estimable. La
[documentación](../../docs/comparative-analysis.md) explica las medias,
continuaciones, plantilla de precios y puestos históricos.
