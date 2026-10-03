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

## Accés i dades
- El primer cop que s'obre l'app en un dispositiu es crea el compte d'**administrador**.
- L'administrador pot afegir usuaris amb tres rols: **Administrador** (ho pot fer tot), **Entrenador** (registra i edita partits) i **Consulta** (només veu partits i estadístiques).
- Els partits es desen **xifrats** (AES-256) al navegador del dispositiu. Cada usuari obre les dades amb la seva contrasenya. Sense un usuari vàlid no es poden llegir.
- La sessió es tanca sola després de 30 minuts sense activitat.
- Si un usuari oblida la contrasenya, un administrador n'hi pot posar una de nova. Si l'únic administrador oblida la seva, les dades no es poden recuperar: crea un segon administrador o fes còpies.
- **Còpia de seguretat** (a *Partits i temporada*): exporta un fitxer xifrat amb tots els partits. Serveix per guardar-los fora del dispositiu o passar-los a un altre. Per importar-lo cal un usuari i una contrasenya vàlids de la còpia.
- Els usuaris i els rols són de cada dispositiu. Els rols limiten què es pot fer des de l'app, però qualsevol usuari amb contrasenya pot llegir les dades xifrades d'aquell dispositiu.

## Actualitzar-la
Substitueix els fitxers al servidor. Si canvies `sw.js` o les icones, augmenta el número de `VERSION` dins `sw.js` (per exemple, `vidia-v2`) perquè els dispositius descarreguin la versió nova.
