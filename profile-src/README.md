# Fuentes de la portada de STABLE-uv

Esta carpeta genera las imágenes, el mapa y los perfiles del equipo de la portada de la organización ([`profile/README.md`](../profile/README.md) y [`profile/README.es.md`](../profile/README.es.md)).

## Añadir o cambiar a alguien del equipo

1. Edita `team.csv`: una fila por persona, en el orden en que aparecen (de senior a junior).
   - `id`: identificador corto y sin espacios; da nombre a la foto (`photos/<id>.jpg`).
   - `site`: institución donde trabaja (el `id` de `institutions.csv`).
   - `role_en` / `role_es`: cargo, que sale en el mapa y en el perfil.
   - `affiliation_en` / `affiliation_es`: institución tal y como aparece en el perfil.
   - `bio_en` / `bio_es`: biografía del perfil.
   - `initials`: iniciales que se muestran si no hay foto.
   - `photo_crop` (opcional): recorte de la foto como `cx cy lado`, en fracciones: centro de la cara (horizontal y vertical) y lado del cuadrado respecto al lado corto de la foto; por ejemplo `0.70 0.34 0.62`. Sin valor, se usa un cuadrado centrado.
   - `github`, `orcid`, `website` (opcionales): el nombre enlaza a su perfil de GitHub y se añaden los enlaces a ORCID y a su web.
2. Fotos: guarda una por persona en `photos/<id>.jpg` (o `.png`), mejor cuadrada y con la cara centrada. Si no hay foto se usa su avatar de GitHub (si tiene usuario en `team.csv`) y, si tampoco, sus iniciales.
3. Ejecuta `python build.py` (necesita `numpy`, `matplotlib` y `pillow`). Regenera las imágenes de `profile/img/`, el mapa interactivo (`profile/team-map.geojson`) y los perfiles del equipo de los dos README. La primera vez descarga el mapa base (Natural Earth, a través del paquete `world-atlas`) en `.cache/`.
4. Sube los cambios.

## Instituciones y mapa

- `institutions.csv` tiene una fila por institución. La primera es la coordinadora: de ella salen las líneas discontinuas hacia las demás.
- `card_x` y `card_y` son la esquina inferior izquierda de la tarjeta de cada institución, como fracción del ancho y del alto del mapa.
- Un `|` en el nombre de la institución fuerza un salto de línea en su tarjeta.
- `gradient` ordena los países del gradiente bioclimático que aparecen en la cabecera (vacío si no forma parte de él).

## Logos de financiación

`logos/` guarda los logos oficiales (Ministerio–UE–AEI y Fundación BBVA). `build.py` los coloca juntos sobre fondo blanco en `profile/img/funders.png`, que se ve bien en modo claro y oscuro. Si cambia algún logo, sustituye el archivo con el mismo nombre y vuelve a ejecutar el script.
