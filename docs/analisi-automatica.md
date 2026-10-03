# Anàlisi automàtica del vídeo — pla

Estat: **fase 1 en funcionament (v0.1)**. Provada amb dos clips de 5 minuts (Curt-hospi_1 i _2).

## Decisions preses
- Vídeos: propis i d'altres. L'anàlisi automàtica es fa amb **vídeos propis** (fitxer MP4 original o descarregat des de YouTube Studio). Els vídeos d'altres canals es continuen analitzant amb el registre manual, tret que en tinguem el fitxer amb permís.
- Càmera: **fixa, elevada, darrere el fons**.
- Equip: **PC amb gràfica NVIDIA** → l'anàlisi pesada es fa en local amb Python. El vídeo no surt del centre.
- Primer objectiu: **punts i marcador** (inici i final de cada jugada, qui guanya el punt, sets, rotacions).

## Com funcionarà (fase 1)
1. **Analitzador local (Python, GPU)** processa el MP4 i genera un fitxer JSON amb les jugades.
2. Detecta l'**inici de cada jugada** (servei) i el **final** (pilota a terra, aturada del joc, xiulet).
3. Deducció del guanyador del punt: **l'equip que serveix la jugada següent ha guanyat l'anterior**. Amb càmera darrere el fons, saber si el servei és del camp proper o del llunyà és fiable. Es contrasta amb on cau la pilota.
4. L'app web importa el JSON i mostra una **pantalla de revisió**: cada jugada amb el seu moment de vídeo i la confiança. L'usuari confirma o corregeix.
5. Cada correcció queda com a **dada d'aprenentatge**.

## Informació que l'app demanarà per a cada vídeo
- Fitxer de vídeo (i l'enllaç de YouTube, opcional, per enllaçar-hi els moments).
- **Calibratge de la pista**: marcar les 4 cantonades i la xarxa en un fotograma.
- **Quin equip és al camp proper** (a prop de la càmera) al set 1. Canvi de camp automàtic a cada set i als 8 punts del 5è set.
- **Qui serveix primer** al set 1 i l'alineació inicial de cada set (ja existeix a l'app).
- **Inici de cada set**: el sistema el proposa (pauses llargues) i l'usuari el confirma.
- **Colors de samarreta** de cada equip i del lliure (opcional, millora la detecció).
- Marcador inicial, si el vídeo no comença a 0-0.

## Aprenentatge
- **Fase 2**: entrenar un detector de pilota amb fotogrames dels nostres vídeos (uns 500-1.000 fotogrames etiquetats, amb una eina inclosa). Millora el final de jugada i la zona on cau la pilota.
- **Fase 3**: tipus d'acció (servei, recepció, col·locació, atac, bloqueig, defensa) a partir dels contactes de la trajectòria. Les etiquetes surten del registre manual que ja fem, perquè cada acció registrada té el moment exacte del vídeo.
- Jugador i valoració: fase posterior. Necessita molts partits etiquetats.

## Què cal per començar
- 2-3 partits propis en MP4 amb el marcador real de cada set (per mesurar l'encert).
- Una captura de la pista tal com es veu a la càmera.
- Dades del PC: sistema operatiu, model de gràfica, i si té Python instal·lat.

## Resultats de la primera prova (clips de 5 min, 1080p 30 fps)
- Xiulet de l'àrbitre a **3.225 Hz**, detectat als dos clips. Els crits i les sabatilles fan soroll a la mateixa banda; per això es busca una banda estreta al voltant de la freqüència calculada.
- La presència del servidor darrere la línia propera és un senyal net (0 % fora de servei, 4-8 % durant el servei).
- La zona darrere la línia llunyana no serveix (hi ha gent i la paret): els serveis llunyans es dedueixen del xiulet i de la pujada de moviment.
- Clip 1: 15 jugades proposades. Clip 2: 20. Pendent de comparar amb el marcador real per mesurar l'encert.

## Lent ull de peix
- La càmera té deformació de barril: la línia de fons propera fa una corba de ~36 px al mig (vídeo 1080p).
- Es corregeix amb un model de divisió d'un coeficient (k ≈ −0,25). El calibratge demana el punt mig de tres línies i, a més, traça automàticament la línia de fons propera. Error residual ~1,4 px.

## PC de l'usuari
- NVIDIA GeForce RTX 3070 Laptop, Python 3.13.7.
