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

## Iconos de la barra superior

Insignias circulares que sustituyen a los emojis de los cuatro botones. Misma
receta que el logotipo, a 96 px (se muestran a 28, así que aguantan pantallas
de hasta 3x sin difuminarse).

| Original | Derivado en `static/img/` | Botón |
|---|---|---|
| `reconocimiento_facial.png` | `nav-reconocimiento-facial.png` | Reconocimiento Facial |
| `alpr.png` | `nav-alpr.png` | Placas (ALPR) |
| `grabaciones.png` | `nav-grabaciones.png` | Grabaciones |
| `configuracion.png` | `nav-configuracion.png` | Configuración |

Dos cosas a tener en cuenta si se rehacen o se sustituyen:

- **No bajar de 28 px en pantalla.** Llevan mucho detalle interno, incluidos
  rótulos. A 20 px la línea se pierde y las cuatro se confunden en el mismo
  círculo turquesa, justo cuando más falta hacen: al estrecharse la barra se
  ocultan las etiquetas de texto y el icono queda como única pista.
- **Están dibujadas para fondo oscuro.** Sobre el tema claro, las de trazo más
  fino resultaban casi invisibles, así que el CSS les pone un disco oscuro
  detrás. Si se rehacen con trazo oscuro, sobra ese disco.
