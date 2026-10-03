# Sports VidIA Vòlei — webapp

Aplicació web instal·lable (PWA) per fer scouting de partits de vòlei a partir de vídeos de YouTube.

## Contingut de la carpeta

| Fitxer | Funció |
|---|---|
| `index.html` | L'aplicació sencera |
| `manifest.webmanifest` | Nom, colors i icones per poder-la instal·lar |
| `sw.js` | Service worker: permet obrir-la sense connexió i rebre actualitzacions |
| `icons/` | Icones de l'app (mòbil, ordinador i pestanya) |

## Publicar-la

Cal publicar la carpeta en un servidor **HTTPS**. Sense HTTPS no es pot instal·lar ni funciona sense connexió. Obrir `index.html` directament des de l'ordinador també funciona, però sense instal·lació i sovint sense el reproductor de YouTube.

### Opció 1: GitHub Pages (gratuït)
1. Crea un repositori nou a GitHub, per exemple `sports-vidia`.
2. Puja-hi el contingut d'aquesta carpeta (no la carpeta, sinó els fitxers de dins).
3. Ves a **Settings → Pages**, tria la branca `main` i la carpeta `/ (root)`, i desa.
4. Al cap d'un minut tindràs l'app a `https://<usuari>.github.io/sports-vidia/`.

### Opció 2: Netlify Drop
1. Entra a `app.netlify.com/drop` amb un compte gratuït.
2. Arrossega la carpeta descomprimida a la pàgina.
3. Netlify et dona una adreça HTTPS al moment.

### Opció 3: servidor del centre
Copia els fitxers a qualsevol espai web estàtic amb HTTPS (per exemple, el servidor web de l'institut).

## Instal·lar-la
- **Android / Chrome / Edge**: obre l'adreça i prem **Instal·la l'app** (a la capçalera) o l'opció *Instal·la* del navegador.
- **iPhone / iPad (Safari)**: botó Compartir → **Afegeix a la pantalla d'inici**.
- **Ordinador (Chrome / Edge)**: icona d'instal·lació a la barra d'adreces.

## Dades
- Les dades es guarden al navegador de cada dispositiu. No s'envien enlloc.
- Esborrar les dades del navegador esborra el partit. Exporta el **CSV** en acabar cada partit (pestanya Partit) i torna'l a importar quan calgui.
- Per passar un partit d'un dispositiu a un altre, exporta el CSV en un i importa'l a l'altre.

## Actualitzar-la
Substitueix els fitxers al servidor. Si canvies `sw.js` o les icones, augmenta el número de `VERSION` dins `sw.js` (per exemple, `vidia-v2`) perquè els dispositius descarreguin la versió nova.
