# Analitzador automàtic de jugades (fase 1)

Programa per al PC que llegeix el vídeo d'un partit (càmera fixa darrere el fons) i proposa les jugades i qui guanya cada punt. El resultat s'importa a l'app (pestanya **Revisió auto**), on es revisa i s'aplica al partit.

## Instal·lació (una sola vegada)
1. Obre una finestra de terminal (a Windows: tecla Windows, escriu `cmd`, Intro).
2. Ves a aquesta carpeta, per exemple: `cd C:\Users\xavi\sports-vidia\analitzador`
3. Instal·la les dependències: `py -m pip install -r requirements.txt`

## Ús per a cada vídeo
1. **Calibra la pista**: `py vidia_analitza.py calibra "C:\Videos\partit.mp4"`
   S'obre una imatge del vídeo. Clica, per aquest ordre, les cantonades de la pista de vòlei: propera esquerra, propera dreta, llunyana dreta i llunyana esquerra. Prem **S** per desar o **R** per tornar a començar.
   Si la càmera no s'ha mogut, pots copiar el fitxer `.calibratge.json` d'un altre vídeo amb el nom del nou.
2. **Analitza**: `py vidia_analitza.py analitza "C:\Videos\partit.mp4"`
   Triga aproximadament 1/6 de la durada del vídeo. Crea `partit.mp4.analisi.json`.
3. A l'app, obre el partit, obre el **fitxer de vídeo** (sota el reproductor) i, a **Revisió auto**, importa el fitxer `.analisi.json`.
4. Indica quin equip era al camp proper a l'inici del vídeo, revisa les jugades i prem **Aplica al partit**.

## Aprenentatge
Després de revisar un vídeo, a l'app prem **Desa les correccions per a l'aprenentatge**. Es descarrega `partit.mp4.etiquetes.json`. Llavors:

`py vidia_analitza.py avalua "C:\Videos\partit.mp4" "partit.mp4.etiquetes.json"`

El programa mesura l'encert, prova altres ajustos i, si en troba de millors, els desa a `parametres.json`. Les anàlisis següents ja els faran servir. Com més vídeos revisats, millor s'ajusta.

## Com decideix
- **Servei del camp proper**: algú es queda quiet darrere la línia de fons propera. És el senyal més fiable.
- **Xiulet de l'àrbitre**: busca el to propi del xiulet. La freqüència es calcula sola per a cada vídeo.
- **Servei del camp llunyà**: xiulet o pujada clara de moviment sense ningú darrere la línia propera. És menys fiable i surt amb menys confiança.
- **Guanyador del punt**: l'equip que serveix la jugada següent.

## Limitacions d'aquesta versió
- Encara no fa servir la gràfica NVIDIA: tot funciona amb el processador. La gràfica s'aprofitarà a la fase 2, amb un model que segueixi la pilota.
- El guanyador de l'última jugada del vídeo i el de l'últim punt de cada set s'han d'indicar a mà.
- Funciona amb càmera fixa. Si la càmera es mou durant el partit, cal tornar a calibrar.
