# assets

Material fuente del proyecto, en su resolución original. No lo usa la
aplicación en tiempo de ejecución: sirve para regenerar los archivos derivados
si hace falta otro tamaño o formato.

## Icono.png

Logotipo original (1410x1128, PNG con transparencia).

De él salen las versiones que sí usa la aplicación, en `static/img/`:

| Archivo | Tamaño | Uso |
|---|---|---|
| `logo.png` | 256 px | Pantalla de inicio de sesión |
| `logo-64.png` | 64 px | Barra superior |
| `favicon.ico` | 16/32/48 px | Pestaña del navegador |

Para regenerarlas: se recorta el margen transparente sobrante, se cuadra sobre
lienzo transparente para que no se deforme al escalar y se reduce a paleta
conservando el canal alfa, lo que baja el peso de unos 426 KB a 22 KB sin
pérdida visible.
