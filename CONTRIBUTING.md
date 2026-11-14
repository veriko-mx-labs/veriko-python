# Contribuir

Los issues y los pull requests son bienvenidos.

## Reporta un error o pide un cambio

Abre un issue con el formulario de [reporte de error](https://github.com/veriko-mx-labs/veriko-python/issues/new?template=bug_report.yml) o de [solicitud de mejora](https://github.com/veriko-mx-labs/veriko-python/issues/new?template=feature_request.yml). Antes, busca entre los issues abiertos.

Un reporte de error necesita las versiones que usas, los pasos que lo reproducen y, cuando la API respondió con un error, su `request_id` (`error.request_id` en el error levantado). Quita las claves de API, los comprobantes, los números de cuenta y los datos de clientes de todo lo que pegues.

No abras un issue público para una vulnerabilidad de seguridad. Sigue [SECURITY.md](SECURITY.md).

## Propón un cambio

1. Para algo más grande que una corrección, abre antes un issue y acuerden el cambio antes de que lo escribas.
2. Haz un fork del repositorio y crea una rama desde `main`.
3. Haz el cambio. Agrega o actualiza las pruebas en `tests/`.
4. Corre las verificaciones de este repositorio:

   ```bash
   pip install -e ".[dev]"
   ruff check .
   ruff format --check .
   mypy
   python -m compileall -q examples
   pytest
   ```

   `ruff format .` corrige el formato. Las pruebas nunca llegan a la API. Usa Python 3.9 o superior; el CI corre de 3.9 a 3.14.

5. Actualiza README.md y CHANGELOG.md cuando cambie el comportamiento que ven los usuarios.
6. Abre un pull request contra `main` y llena la plantilla.

Escribe los mensajes de commit en español, en imperativo, y deja un solo cambio en cada commit.

`src/veriko/_async_resources.py` se genera a partir de `src/veriko/resources.py`. Después de cambiar un método público, corre `python scripts/generate_async.py`. Una prueba falla cuando el archivo generado está desactualizado.

El paquete no tiene dependencias en tiempo de ejecución. No agregues una sin abrir antes un issue.

## El contrato de la API

El contrato de la API es el spec público en <https://docs.veriko.mx/openapi.yaml>. `spec/openapi.yaml` es una copia que sincroniza un flujo de trabajo programado. No la edites a mano. Cuando la API se comporte distinto del spec, abre un issue.
