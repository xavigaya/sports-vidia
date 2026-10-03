#!/usr/bin/env python3
"""
Sports VidIA · Analitzador automàtic de jugades (fase 1)

Detecta les jugades d'un vídeo de vòlei gravat amb càmera fixa darrere el fons:
  1. Xiulets de l'àrbitre (so, ~2-5 kHz, to pur).
  2. Moviment a la pista (diferència entre imatges).
  3. Costat que serveix: hi ha algú darrere la línia de fons propera (o llunyana)?
El guanyador de cada punt es dedueix del costat que serveix la jugada següent.

Ús:
  python vidia_analitza.py calibra  VIDEO              -> crea VIDEO.calibratge.json
  python vidia_analitza.py analitza VIDEO              -> crea VIDEO.analisi.json (per importar a l'app)
  python vidia_analitza.py avalua   VIDEO ETIQUETES    -> compara amb les correccions i ajusta paràmetres

Requisits: Python 3.10+, numpy, scipy, opencv-python, imageio-ffmpeg (veure requirements.txt).
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

import numpy as np

VERSIO = "0.1.0"
AQUI = Path(__file__).resolve().parent
PARAMS_FILE = AQUI / "parametres.json"

DEFAULT_PARAMS = {
    "xiulet_banda_hz": [2000, 5000],   # banda on busquem el xiulet
    "xiulet_llindar": 1.0,             # log10(energia a la freqüència del xiulet / energia del voltant)
    "xiulet_min_sep_s": 1.5,           # dos xiulets han d'estar separats almenys això
    "servei_finestra_s": 9.0,          # temps després del xiulet on busquem el servidor
    "servei_llindar": 0.020,           # fracció mínima de píxels en moviment a la zona de servei
    "servei_durada_s": 0.7,            # el servidor ha d'estar com a mínim això a la zona
    "moviment_ratio": 1.25,            # pujada de moviment que indica que comença una jugada
    "fusio_servei_s": 6.0,             # xiulets de servei més propers que això es fusionen
    "fps_analisi": 10,
    "amplada_analisi": 320,
}


def load_params():
    p = dict(DEFAULT_PARAMS)
    if PARAMS_FILE.exists():
        try:
            p.update(json.loads(PARAMS_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return p


def ffmpeg_exe():
    exe = shutil.which("ffmpeg")
    if exe:
        return exe
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        sys.exit("No trobo ffmpeg. Instal·la'l amb:  pip install imageio-ffmpeg")


def video_info(path):
    import cv2
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        sys.exit(f"No puc obrir el vídeo: {path}")
    w, h = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    fps = cap.get(cv2.CAP_PROP_FPS) or 30
    n = cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0
    cap.release()
    return {"amplada": w, "alcada": h, "fps": fps, "durada": n / fps if fps else 0}


# ---------------------------------------------------------------- calibratge
def calibra(video, t=None):
    """Mostra una imatge del vídeo i demana clicar les 4 cantonades de la pista."""
    import cv2
    info = video_info(video)
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, (t if t is not None else min(30, info["durada"] / 3)) * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit("No he pogut llegir cap imatge del vídeo.")
    noms = ["1 Cantonada PROPERA esquerra", "2 Cantonada PROPERA dreta",
            "3 Cantonada LLUNYANA dreta", "4 Cantonada LLUNYANA esquerra"]
    escala = min(1.0, 1280 / frame.shape[1])
    img = cv2.resize(frame, None, fx=escala, fy=escala)
    punts = []

    def redibuixa():
        v = img.copy()
        for i, p in enumerate(punts):
            cv2.circle(v, p, 6, (0, 255, 255), -1)
            cv2.putText(v, str(i + 1), (p[0] + 8, p[1] - 8), 0, .7, (0, 255, 255), 2)
        if len(punts) > 1:
            cv2.polylines(v, [np.array(punts)], len(punts) == 4, (0, 255, 255), 2)
        txt = noms[len(punts)] if len(punts) < 4 else "Fet: prem S per desar, R per tornar a començar"
        cv2.rectangle(v, (0, 0), (v.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(v, txt, (10, 24), 0, .7, (255, 255, 255), 2)
        cv2.imshow("Calibratge Sports VidIA", v)

    def clic(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and len(punts) < 4:
            punts.append((x, y))
            redibuixa()

    cv2.namedWindow("Calibratge Sports VidIA")
    cv2.setMouseCallback("Calibratge Sports VidIA", clic)
    redibuixa()
    while True:
        k = cv2.waitKey(50) & 0xFF
        if k in (ord("r"), ord("R")):
            punts.clear(); redibuixa()
        if k in (ord("s"), ord("S")) and len(punts) == 4:
            break
        if k == 27:
            cv2.destroyAllWindows(); sys.exit("Calibratge cancel·lat.")
    cv2.destroyAllWindows()
    h, w = img.shape[:2]
    cal = {"cantonades": [[round(x / w, 4), round(y / h, 4)] for x, y in punts],
           "ordre": "propera esquerra, propera dreta, llunyana dreta, llunyana esquerra"}
    out = Path(str(video) + ".calibratge.json")
    out.write_text(json.dumps(cal, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Calibratge desat a {out}")
    return cal


def zones(cal, W, H):
    """Polígons (en píxels de l'anàlisi) de la pista i de les zones de servei."""
    c = np.array(cal["cantonades"], dtype=np.float32) * [W, H]
    NL, NR, FR, FL = c
    pista = np.array([NL, NR, FR, FL])
    # zona darrere la línia propera: allarguem les bandes cap a la càmera
    prop = np.array([NL, NR, NR + (NR - FR) * 0.45, NL + (NL - FL) * 0.45])
    # zona darrere la línia llunyana: allarguem cap a la paret del fons
    lluny = np.array([FL, FR, FR + (FR - NR) * 0.18, FL + (FL - NL) * 0.18])
    return pista, prop, lluny


def mask_of(poly, W, H):
    import cv2
    m = np.zeros((H, W), np.uint8)
    cv2.fillPoly(m, [np.round(poly).astype(np.int32)], 1)
    return m.astype(bool)


# ---------------------------------------------------------------- senyals
def senyal_xiulet(video, params):
    """Puntuació de xiulet al llarg del temps: energia en una banda estreta al voltant de la
    freqüència del xiulet (es calcula sola) respecte a l'energia del voltant."""
    import scipy.signal as sg
    sr = 16000
    p = subprocess.run([ffmpeg_exe(), "-v", "error", "-i", str(video), "-ac", "1", "-ar", str(sr),
                        "-f", "s16le", "-"], capture_output=True)
    x = np.frombuffer(p.stdout, np.int16).astype(np.float32) / 32768
    if x.size < sr:
        return np.zeros(0), 0.016, None
    f, t, S = sg.stft(x, sr, nperseg=1024, noverlap=768)
    P = np.abs(S) ** 2
    lo, hi = params["xiulet_banda_hz"]
    band = (f > lo) & (f < hi)
    pk = P[band].max(0) / (np.median(P[band], 0) + 1e-12)
    fpk = f[band][P[band].argmax(0)]
    # freqüència del xiulet: la més habitual entre tons forts i estables (un xiulet dura >80 ms
    # amb freqüència constant; les sabatilles i els crits canvien de to)
    n = 5
    win = np.lib.stride_tricks.sliding_window_view(fpk, n)
    stable = np.zeros_like(pk, dtype=bool)
    stable[:len(win)] = (win.max(1) - win.min(1)) < 80
    cand = fpk[stable & (pk > np.percentile(pk, 98))]
    if params.get("xiulet_hz"):
        f0 = float(params["xiulet_hz"])
    elif cand.size:
        hist, edges = np.histogram(cand, bins=np.arange(lo, hi + 50, 50))
        f0 = float(edges[hist.argmax()] + 25)
    else:
        f0 = 3200.0
    nb = (f > f0 - 160) & (f < f0 + 160)
    side = ((f > lo) & (f < f0 - 300)) | ((f > f0 + 300) & (f < hi))
    r = P[nb].mean(0) / (P[side].mean(0) + 1e-12)
    score = sg.medfilt(np.log10(r + 1e-9), 5)      # cal que el to duri uns 80 ms
    return score, 256 / sr, f0


def troba_xiulets(score, hop, params):
    import scipy.signal as sg
    if score.size == 0:
        return []
    thr = params["xiulet_llindar"]
    pk, props = sg.find_peaks(score, height=thr, distance=max(1, int(params["xiulet_min_sep_s"] / hop)))
    return [{"t": round(float(i * hop), 2), "forca": round(float(score[i]), 2)} for i in pk]


def senyals_video(video, cal, params):
    """Recorre el vídeo a baixa resolució i calcula moviment i presència a les zones de servei."""
    W = params["amplada_analisi"]
    info = video_info(video)
    H = int(round(W * info["alcada"] / info["amplada"] / 2) * 2)
    fps = params["fps_analisi"]
    pista, prop, lluny = zones(cal, W, H)
    mP, mN, mF = mask_of(pista, W, H), mask_of(prop, W, H), mask_of(lluny, W, H)
    proc = subprocess.Popen([ffmpeg_exe(), "-v", "error", "-i", str(video), "-vf",
                             f"fps={fps},scale={W}:{H},format=gray", "-f", "rawvideo", "-"],
                            stdout=subprocess.PIPE)
    sz = W * H
    hist, prev = [], None
    mov, near, far = [], [], []
    bg = None
    k = 0
    t0 = time.time()
    while True:
        b = proc.stdout.read(sz)
        if len(b) < sz:
            break
        g = np.frombuffer(b, np.uint8).reshape(H, W).astype(np.int16)
        if k % (2 * fps) == 0:            # una mostra de fons cada 2 s
            hist.append(g)
            hist = hist[-30:]             # últim minut
            bg = np.median(np.stack(hist), axis=0)
        fg = np.abs(g - bg) > 30
        d = (np.abs(g - prev) > 18) if prev is not None else np.zeros_like(fg)
        mov.append(d[mP].mean())
        near.append(fg[mN].mean())
        far.append(fg[mF].mean())
        prev = g
        k += 1
        if k % (fps * 60) == 0:
            print(f"  … {k / fps / 60:.0f} min analitzats ({time.time() - t0:.0f} s)", flush=True)
    proc.wait()
    return np.array(mov), np.array(near), np.array(far), fps


def suau(x, n):
    if len(x) < n or n < 2:
        return x
    return np.convolve(x, np.ones(n) / n, mode="same")


# ---------------------------------------------------------------- jugades
def segments(mask, fps, min_s):
    out, k, n = [], 0, len(mask)
    while k < n:
        if mask[k]:
            j = k
            while j + 1 < n and mask[j + 1]:
                j += 1
            if (j - k + 1) / fps >= min_s:
                out.append((k / fps, (j + 1) / fps))
            k = j + 1
        else:
            k += 1
    return out


def detecta(xiulets, mov, near, far, fps, params, durada):
    """Serveis propers: algú es queda darrere la línia de fons propera.
    Serveis llunyans: xiulet seguit d'una pujada de moviment, sense servidor proper."""
    movs, nears = suau(mov, fps), suau(near, max(1, fps // 2))
    thr = params["servei_llindar"]
    serveis = []
    for a, b in segments(nears > thr, fps, params["servei_durada_s"]):
        seg = nears[int(a * fps):int(b * fps)]
        conf = min(1.0, 0.7 + float(seg.max()) / (thr * 10))
        xi = [w["t"] for w in xiulets if a - 9 <= w["t"] <= b]
        serveis.append({"servei": round(b, 1), "xiulet": xi[-1] if xi else None, "costat_servei": "proper",
                        "confianca_servei": round(conf, 2)})
    for w in xiulets:
        t = w["t"]
        if any(s["servei"] - params["servei_finestra_s"] - 1 <= t <= s["servei"] + 2 for s in serveis):
            continue
        a = int(t * fps)
        before = float(movs[max(0, a - 4 * fps):a].mean()) if a > fps else float(movs[:fps].mean())
        after = float(movs[a + 2 * fps:a + 10 * fps].mean()) if a + 3 * fps < len(movs) else 0.0
        rise = after / (before + 1e-6)
        if rise < params["moviment_ratio"]:
            continue
        conf = min(0.8, 0.45 + 0.1 * w["forca"] + 0.1 * min(rise - 1, 2))
        serveis.append({"servei": round(t + 3.0, 1), "xiulet": t, "costat_servei": "llunya",
                        "confianca_servei": round(conf, 2)})
    # jugades sense xiulet detectat: el joc passa de repòs a moviment intens
    rest_lvl = float(np.percentile(movs, 35)) if movs.size else 0
    k = 4 * fps
    while k < len(movs) - 6 * fps:
        before = movs[k - 3 * fps:k].mean(); after = movs[k + fps:k + 6 * fps].mean()
        t = k / fps
        if before < rest_lvl and after > before * params["moviment_ratio"] * 1.3 and \
                not any(abs(s["servei"] - t) < params["fusio_servei_s"] + 2 for s in serveis):
            serveis.append({"servei": round(t + 1.0, 1), "xiulet": None, "costat_servei": "llunya",
                            "confianca_servei": 0.4})
            k += 8 * fps
            continue
        k += max(1, fps // 2)
    serveis.sort(key=lambda s: s["servei"])
    fus = []
    for s in serveis:
        if fus and s["servei"] - fus[-1]["servei"] < params["fusio_servei_s"]:
            if s["confianca_servei"] > fus[-1]["confianca_servei"]:
                fus[-1] = s
            continue
        fus.append(s)
    serveis = fus
    tots = [x["t"] for x in xiulets]
    rest = float(np.percentile(movs, 30)) if movs.size else 0
    jugades = []
    for i, s in enumerate(serveis):
        seg_t = serveis[i + 1]["servei"] - 3 if i + 1 < len(serveis) else durada
        finals = [t for t in tots if s["servei"] + 2 < t < seg_t]
        if finals:
            fi = finals[0]
        else:
            a = int(s["servei"] * fps); b = int(min(seg_t, durada) * fps)
            seg = movs[a:b]
            low = np.where(seg[2 * fps:] < rest)[0] if seg.size > 3 * fps else np.array([])
            fi = s["servei"] + 2 + low[0] / fps if low.size else min(seg_t, durada)
        jugades.append({"n": i + 1, "inici": s["servei"], "final": round(float(max(fi, s["servei"] + 1)), 1),
                        "xiulet": s["xiulet"], "costat_servei": s["costat_servei"],
                        "confianca_servei": s["confianca_servei"]})
    for i, j in enumerate(jugades):
        if i + 1 < len(jugades):
            nx = jugades[i + 1]
            j["guanya_costat"] = nx["costat_servei"]
            j["confianca"] = round(min(j["confianca_servei"], nx["confianca_servei"]), 2)
        else:
            j["guanya_costat"] = None
            j["confianca"] = 0.0
    return jugades


def analitza(video, params=None, cal=None, quiet=False, cache=None):
    params = params or load_params()
    video = Path(video)
    if cal is None:
        cp = Path(str(video) + ".calibratge.json")
        if not cp.exists():
            sys.exit(f"Falta el calibratge. Executa primer:  python {Path(__file__).name} calibra \"{video}\"")
        cal = json.loads(cp.read_text(encoding="utf-8"))
    info = video_info(video)
    if cache and "senyals" in cache:
        score, hop, mov, near, far, fps = cache["senyals"]; f0 = cache.get("f0")
    else:
        if not quiet: print("1/3 Analitzant el so (xiulets)…", flush=True)
        score, hop, f0 = senyal_xiulet(video, params)
        if not quiet: print("2/3 Analitzant la imatge (moviment i servidors)…", flush=True)
        mov, near, far, fps = senyals_video(video, cal, params)
        if cache is not None:
            cache["senyals"] = (score, hop, mov, near, far, fps); cache["f0"] = f0

    xiulets = troba_xiulets(score, hop, params)
    if not quiet: print("3/3 Detectant jugades…", flush=True)
    jugades = detecta(xiulets, mov, near, far, fps, params, info["durada"])
    return {
        "app": "sports-vidia-analisi", "versio": VERSIO,
        "video": {"nom": video.name, "durada": round(info["durada"], 1),
                  "amplada": info["amplada"], "alcada": info["alcada"]},
        "calibratge": cal, "parametres": params,
        "xiulet_hz": round(f0) if f0 else None,
        "xiulets": xiulets, "jugades": jugades,
        "creat": time.strftime("%Y-%m-%d %H:%M"),
    }


# ---------------------------------------------------------------- avaluació i aprenentatge
def avalua(video, etiquetes_path):
    """Compara la detecció amb les correccions fetes a l'app i busca millors paràmetres."""
    et = json.loads(Path(etiquetes_path).read_text(encoding="utf-8"))
    veritat = [j for j in et.get("jugades", []) if not j.get("descartada")]
    if not veritat:
        sys.exit("Les etiquetes no tenen cap jugada confirmada.")
    base = load_params()
    cache = {}

    def puntua(params):
        r = analitza(video, params, quiet=True, cache=cache)
        det = r["jugades"]
        enc_j = enc_g = 0
        usades = set()
        for v in veritat:
            best = None
            for k, d in enumerate(det):
                if k in usades: continue
                if abs(d["inici"] - v["inici"]) <= 4.0:
                    best = k; break
            if best is not None:
                usades.add(best); enc_j += 1
                if v.get("guanya_costat") and det[best].get("guanya_costat") == v["guanya_costat"]:
                    enc_g += 1
        falsos = len(det) - len(usades)
        n = len(veritat)
        f1 = 2 * enc_j / (n + len(det)) if det else 0
        return {"jugades_trobades": enc_j, "jugades_reals": n, "falsos": falsos,
                "guanyador_encertat": enc_g, "f1": round(f1, 3),
                "puntuacio": f1 + enc_g / n}

    print("Analitzant amb els paràmetres actuals…")
    actual = puntua(base)
    print(f"  Actual: {actual['jugades_trobades']}/{actual['jugades_reals']} jugades, "
          f"{actual['falsos']} falses, guanyador encertat {actual['guanyador_encertat']}/{actual['jugades_reals']}")
    millor, mp = actual, dict(base)
    for k in (0.6, 0.8, 1.0, 1.2, 1.5):
        for sl in (0.012, 0.016, 0.020, 0.026, 0.034):
            for mr in (1.15, 1.25, 1.4):
                p = dict(base, xiulet_llindar=k, servei_llindar=sl, moviment_ratio=mr)
                r = puntua(p)
                if r["puntuacio"] > millor["puntuacio"] + 1e-6:
                    millor, mp = r, p
    print(f"  Millor: {millor['jugades_trobades']}/{millor['jugades_reals']} jugades, "
          f"{millor['falsos']} falses, guanyador encertat {millor['guanyador_encertat']}/{millor['jugades_reals']}")
    if millor is not actual:
        keep = {k: mp[k] for k in ("xiulet_llindar", "servei_llindar", "moviment_ratio")}
        PARAMS_FILE.write_text(json.dumps(dict(load_params(), **keep), indent=2), encoding="utf-8")
        print(f"  Paràmetres nous desats a {PARAMS_FILE.name}: {keep}")
    else:
        print("  Els paràmetres actuals ja són els millors per a aquestes etiquetes.")
    return actual, millor


def main():
    ap = argparse.ArgumentParser(description="Sports VidIA · analitzador automàtic de jugades")
    sub = ap.add_subparsers(dest="ordre", required=True)
    c = sub.add_parser("calibra", help="marca les cantonades de la pista")
    c.add_argument("video"); c.add_argument("--temps", type=float, default=None, help="segon del vídeo a mostrar")
    a = sub.add_parser("analitza", help="detecta les jugades i crea el fitxer per a l'app")
    a.add_argument("video"); a.add_argument("--sortida", default=None)
    e = sub.add_parser("avalua", help="compara amb les correccions de l'app i ajusta paràmetres")
    e.add_argument("video"); e.add_argument("etiquetes")
    args = ap.parse_args()
    if args.ordre == "calibra":
        calibra(args.video, args.temps)
    elif args.ordre == "analitza":
        r = analitza(args.video)
        out = Path(args.sortida or (str(args.video) + ".analisi.json"))
        out.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        n = len(r["jugades"])
        print(f"Fet: {n} jugades detectades. Importa {out.name} a l'app (Partit → Importa anàlisi).")
    elif args.ordre == "avalua":
        avalua(args.video, args.etiquetes)


if __name__ == "__main__":
    main()
