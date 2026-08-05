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

## Iconos de las cuatro funciones

Cada función tiene **dos versiones**, una por tema. Los originales vienen a
512x512 y de ellos salen derivados de 96 px en `static/img/` (se muestran a 28,
así que aguantan pantallas de hasta 3x sin difuminarse), con la misma receta
que el logotipo.

Ojo al nombre: los originales dicen `white`, pero en el código el tema claro se
llama `light`, y así se nombran los derivados.

| Original | Derivados en `static/img/` | Botón y título |
|---|---|---|
| `facial-recognition_dark_theme.png`<br>`facial-recognition_white_theme.png` | `nav-reconocimiento-facial-dark.png`<br>`nav-reconocimiento-facial-light.png` | Reconocimiento Facial |
| `alpr_dark_theme.png`<br>`alpr_white_theme.png` | `nav-alpr-dark.png`<br>`nav-alpr-light.png` | Placas (ALPR) |
| `grabacion_dark_theme.png`<br>`grabacion_white_theme.png` | `nav-grabaciones-dark.png`<br>`nav-grabaciones-light.png` | Grabaciones |
| `settings_dark_theme.png`<br>`settings_white_theme.png` | `nav-configuracion-dark.png`<br>`nav-configuracion-light.png` | Configuración |

Se aplican como fondo de un `<span>` vacío, no como `<img>`, para que el CSS
pueda elegir la versión según `data-theme`. Así el navegador se descarga solo
la variante que está en uso; con dos `<img>` superpuestas se traería las dos.

Para añadir o cambiar un icono basta con dejar el par en esta carpeta,
regenerar los derivados y declarar las dos reglas correspondientes en
`static/style.css`. El marcado no cambia: lleva `data-icono="<nombre>"`.

**No bajar de 28 px en pantalla.** Es el tamaño para el que están ajustados y
coincide con el del logotipo; la altura de la barra la fija la ficha de usuario,
no estos botones, así que ese tamaño no desplaza nada.

## Iconos de las pestañas de configuración

Mismo mecanismo y misma receta, pero derivados con prefijo `tab-` y mostrados a
22 px. Por debajo de 20 se emborronan los de más detalle (la lupa de detección
y el reloj de sesión); subir no cuesta ancho, porque el de la pestaña lo marca
su texto, solo añade alto una vez.

| Original | Derivados en `static/img/` | Pestaña |
|---|---|---|
| `detection_*_theme.png` | `tab-detection-{dark,light}.png` | Detección |
| `plate_*_theme.png` | `tab-placas-{dark,light}.png` | Placas |
| `visualizacion_*_theme.png` | `tab-visualizacion-{dark,light}.png` | Visualización |
| `users_*_theme.png` | `tab-usuarios-{dark,light}.png` | Usuarios |
| — | `nav-grabaciones-{dark,light}.png` | Grabaciones |
| `registro_*_theme.png` | `tab-registro-{dark,light}.png` | Registro |
| `session_*_theme.png` | `tab-sesion-{dark,light}.png` | Sesión |

Grabaciones **reutiliza** el icono del juego de la barra en lugar de duplicarlo;
por eso su `data-icono` empieza por `nav-` y no por `tab-`.

El icono se apila sobre la etiqueta. La regla se limita con `:has(.tab-icono)` a
las pestañas que llevan icono: los paneles de rostros y placas comparten la
clase `.fr-tab` y siguen como estaban.

## Cerrar sesión

| Original | Derivados en `static/img/` | Dónde |
|---|---|---|
| `logout_dark_theme.png`<br>`logout_white_theme.png` | `nav-logout-{dark,light}.png` | Ficha de usuario, barra superior |

Se muestra a 22 px: por debajo de 20 se pierde la flecha de salida, que es lo
que distingue el icono de una puerta cualquiera.

Al no llevar texto, el nombre accesible lo aporta el `aria-label` de la
plantilla. Sin él, un lector de pantalla anunciaría un enlace sin nombre.
