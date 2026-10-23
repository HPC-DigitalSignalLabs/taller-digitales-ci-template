# Política de seguridad

## Reportar una vulnerabilidad

No abrir un issue público. Usar **Security → Report a vulnerability**
(avisos privados de GitHub) en este repositorio. Se responderá en un plazo
razonable dentro del calendario académico.

## Modelo de amenazas del pipeline

- Los workflows usan `pull_request` y `merge_group`, nunca
  `pull_request_target`: el código de un fork no recibe secretos ni permisos
  de escritura.
- Permisos mínimos declarados (`contents: read`); solo la publicación de la
  imagen en `push` a ramas protegidas obtiene `packages: write`.
- Todas las *actions* están ancladas por SHA de commit.
- PRs de colaboradores externos requieren aprobación de un mantenedor antes de
  ejecutar workflows.
- Cambios a `.github/` requieren revisión de un propietario de código, y una
  aprobación se descarta si llega un push posterior.
- **No se conectan runners auto-hospedados a este repositorio público.**
  La estación FPGA y el acceso al clúster se operan desde un repositorio
  privado institucional, con `workflow_dispatch`, lista de operadores y
  verificación del SHA aprobado.
