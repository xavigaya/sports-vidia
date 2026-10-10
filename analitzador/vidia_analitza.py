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
  python vidia_analitza.py avalua   VIDEO ETIQUETES    -> afegeix el partit revisat al conjunt d'aprenentatge
                                                         i ajusta els paràmetres amb tots els partits
  python vidia_analitza.py estat                       -> conjunt, historial i criteris de canvi de fase
  python vidia_analitza.py apren                       -> torna a ajustar amb tot el conjunt
  python vidia_analitza.py oblida NOM_VIDEO            -> treu un partit del conjunt

Requisits: Python 3.10+, numpy, scipy, opencv-python, imageio-ffmpeg (veure requirements.txt).
"""
import argparse, json, os, shutil, subprocess, sys, time
from pathlib import Path

import numpy as np

VERSIO = "0.2.0"
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
# Model de lent (ull de peix): model de divisió amb un coeficient radial k.
# q = (p - centre) / mig_diagonal ;  q_recte = q / (1 + k·|q|²)
def _norm(p, W, H):
    c = np.array([W / 2, H / 2]); s = np.hypot(W / 2, H / 2)
    return (np.asarray(p, float) - c) / s, c, s


def lens_undistort(p, k, W, H):
    q, c, s = _norm(p, W, H)
    r2 = (q ** 2).sum(-1, keepdims=True)
    return q / (1 + k * r2) * s + c


def lens_distort(u, k, W, H):
    q, c, s = _norm(u, W, H)
    ru = np.sqrt((q ** 2).sum(-1, keepdims=True))
    if abs(k) < 1e-9:
        return np.asarray(u, float)
    disc = np.clip(1 - 4 * k * ru ** 2, 0, None)
    with np.errstate(divide="ignore", invalid="ignore"):
        rd = np.where(ru > 1e-9, (1 - np.sqrt(disc)) / (2 * k * ru), 0)
        f = np.where(ru > 1e-9, rd / ru, 1)
    return q * f * s + c


def fit_lens(lines, W, H):
    """lines: llistes de punts (píxels) que haurien d'estar en una recta. Retorna k."""
    from scipy.optimize import minimize_scalar
    lines = [np.asarray(l, float) for l in lines if len(l) >= 3]
    if not lines:
        return 0.0, None

    def res(k):
        tot, n = 0.0, 0
        for l in lines:
            u = lens_undistort(l, k, W, H)
            d = u - u.mean(0)
            _, sv, _ = np.linalg.svd(d, full_matrices=False)
            tot += sv[-1] ** 2; n += len(l)
        return tot / max(n, 1)

    o = minimize_scalar(res, bounds=(-0.8, 0.8), method="bounded")
    return float(o.x), float(np.sqrt(o.fun))


def trace_line(gray, a, b, k_guess=0.0, search=40):
    """Busca la línia fosca de la pista entre a i b (píxels) i en retorna punts."""
    H, W = gray.shape
    pts = []
    n = int(np.hypot(*(np.subtract(b, a))) / 25)
    for i in range(1, n):
        t = i / n
        x, y = (1 - t) * np.array(a) + t * np.array(b)
        xi = int(round(x))
        if not (3 <= xi < W - 3):
            continue
        ys = np.arange(max(0, int(y - search)), min(H, int(y + search)))
        if ys.size < 5:
            continue
        col = gray[ys, xi - 2:xi + 3].mean(1)
        j = int(col.argmin())
        if col[j] < np.median(col) * 0.6:      # cal un contrast clar
            pts.append((x, ys[j]))
    return pts


def calibra(video, t=None):
    """Demana clicar les 4 cantonades i el punt mig de tres línies, i calcula la deformació de la lent."""
    import cv2
    info = video_info(video)
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, (t if t is not None else min(30, info["durada"] / 3)) * 1000)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        sys.exit("No he pogut llegir cap imatge del vídeo.")
    noms = ["1 Cantonada PROPERA esquerra", "2 Cantonada PROPERA dreta",
            "3 Cantonada LLUNYANA dreta", "4 Cantonada LLUNYANA esquerra",
            "5 Punt MIG de la linia de fons PROPERA", "6 Punt MIG de la banda DRETA",
            "7 Punt MIG de la banda ESQUERRA"]
    escala = min(1.0, 1280 / frame.shape[1])
    img = cv2.resize(frame, None, fx=escala, fy=escala)
    punts = []

    def redibuixa():
        v = img.copy()
        for i, p in enumerate(punts):
            cv2.circle(v, p, 6, (0, 255, 255), -1)
            cv2.putText(v, str(i + 1), (p[0] + 8, p[1] - 8), 0, .7, (0, 255, 255), 2)
        if len(punts) >= 2:
            cv2.polylines(v, [np.array(punts[:4])], len(punts) >= 4, (0, 255, 255), 1)
        txt = noms[len(punts)] if len(punts) < 7 else "Fet: S desa, R torna a començar"
        if 4 <= len(punts) < 7:
            txt += "  (X: desa sense corregir la lent)"
        cv2.rectangle(v, (0, 0), (v.shape[1], 34), (0, 0, 0), -1)
        cv2.putText(v, txt, (10, 24), 0, .65, (255, 255, 255), 2)
        cv2.imshow("Calibratge Sports VidIA", v)

    def clic(ev, x, y, *_):
        if ev == cv2.EVENT_LBUTTONDOWN and len(punts) < 7:
            punts.append((x, y)); redibuixa()

    cv2.namedWindow("Calibratge Sports VidIA")
    cv2.setMouseCallback("Calibratge Sports VidIA", clic)
    redibuixa()
    while True:
        key = cv2.waitKey(50) & 0xFF
        if key in (ord("r"), ord("R")):
            punts.clear(); redibuixa()
        if key in (ord("s"), ord("S")) and len(punts) == 7:
            break
        if key in (ord("x"), ord("X")) and len(punts) >= 4:
            punts[:] = punts[:4]; break
        if key == 27:
            cv2.destroyAllWindows(); sys.exit("Calibratge cancel·lat.")
    cv2.destroyAllWindows()
    h, w = img.shape[:2]
    cal = calibratge_de_punts(punts, w, h, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
    out = Path(str(video) + ".calibratge.json")
    out.write_text(json.dumps(cal, indent=2, ensure_ascii=False), encoding="utf-8")
    prev = Path(str(video) + ".calibratge.jpg")
    cv2.imwrite(str(prev), dibuixa_calibratge(img, cal))
    print(f"Calibratge desat a {out.name}. Lent: k = {cal['lent_k']:.3f}.")
    print(f"Comprova la imatge {prev.name}: les línies grogues han de seguir les de la pista.")
    return cal


def calibratge_de_punts(punts, w, h, gray=None):
    """Construeix el calibratge (coordenades normalitzades i coeficient de lent)."""
    P = [np.array(p, float) for p in punts]
    k, err = 0.0, None
    if len(P) >= 7:
        NL, NR, FR, FL, MN, MR, ML = P[:7]
        lines = [[NL, MN, NR], [NR, MR, FR], [NL, ML, FL]]
        if gray is not None:      # afegim punts de la línia de fons propera, traçats automàticament
            tr = trace_line(gray, NL, MN) + trace_line(gray, MN, NR)
            if len(tr) >= 8:
                k0, e0 = fit_lens([tr], w, h)
                if e0 is not None and e0 < 0.004 * np.hypot(w, h):
                    lines[0] = [NL] + tr + [NR]
        k, err = fit_lens(lines, w, h)
    return {"cantonades": [[round(x / w, 4), round(y / h, 4)] for x, y in punts[:4]],
            "punts_mig": [[round(x / w, 4), round(y / h, 4)] for x, y in punts[4:7]],
            "lent_k": round(k, 4),
            "ordre": "propera esquerra, propera dreta, llunyana dreta, llunyana esquerra; mig fons proper, mig banda dreta, mig banda esquerra"}


def _poly_dist(poly_u, k, W, H, per_edge=40):
    """Densifica un polígon recte (espai corregit) i el torna a l'espai de la imatge."""
    pts = []
    n = len(poly_u)
    for i in range(n):
        a, b = poly_u[i], poly_u[(i + 1) % n]
        for t in np.linspace(0, 1, per_edge, endpoint=False):
            pts.append((1 - t) * a + t * b)
    return lens_distort(np.array(pts), k, W, H)


def zones(cal, W, H):
    """Polígons (en píxels de l'anàlisi) de la pista i de les zones de servei, seguint la corba de la lent."""
    k = float(cal.get("lent_k", 0) or 0)
    c = np.array(cal["cantonades"], dtype=float) * [W, H]
    NL, NR, FR, FL = lens_undistort(c, k, W, H)
    pista = np.array([NL, NR, FR, FL])
    prop = np.array([NL, NR, NR + (NR - FR) * 0.45, NL + (NL - FL) * 0.45])
    lluny = np.array([FL, FR, FR + (FR - NR) * 0.18, FL + (FL - NL) * 0.18])
    return tuple(_poly_dist(p, k, W, H) for p in (pista, prop, lluny))


def dibuixa_calibratge(img, cal):
    import cv2
    h, w = img.shape[:2]
    v = img.copy()
    pista, prop, lluny = zones(cal, w, h)
    over = v.copy()
    cv2.fillPoly(over, [np.round(prop).astype(np.int32)], (0, 200, 0))
    v = cv2.addWeighted(over, 0.25, v, 0.75, 0)
    cv2.polylines(v, [np.round(pista).astype(np.int32)], True, (0, 255, 255), 2)
    cv2.polylines(v, [np.round(prop).astype(np.int32)], True, (0, 200, 0), 2)
    cv2.putText(v, f"Zona de servei propera (verd) - lent k={cal.get('lent_k', 0):.3f}", (10, 28), 0, .7, (255, 255, 255), 2)
    return v


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
# Conjunt d'aprenentatge: cada partit revisat hi deixa les seves etiquetes i els senyals ja
# calculats (so i imatge). Així els paràmetres s'ajusten amb tots els partits alhora i no cal
# tornar a llegir els vídeos.
APR = AQUI / "aprenentatge"
INDEX = APR / "index.json"
HIST = APR / "historial.json"
TUNED = ("xiulet_llindar", "servei_llindar", "moviment_ratio")
GRID = {"xiulet_llindar": (0.6, 0.8, 1.0, 1.2, 1.5),
        "servei_llindar": (0.012, 0.016, 0.020, 0.026, 0.034),
        "moviment_ratio": (1.15, 1.25, 1.4, 1.6)}
CRITERIS = {"partits_sencers": 3, "trobades": 0.95, "falses": 0.05, "guanyador": 0.90,
            "sense_canvis": 2, "durada_partit_sencer_s": 40 * 60}


def _jread(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def _jwrite(path, data):
    Path(path).write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")


def _slug(nom):
    return "".join(c if c.isalnum() or c in "-_." else "_" for c in nom)


def calcula_senyals(video, cal, params):
    info = video_info(video)
    print("  · so (xiulets)…", flush=True)
    score, hop, f0 = senyal_xiulet(video, params)
    print("  · imatge (moviment i servidors)…", flush=True)
    mov, near, far, fps = senyals_video(video, cal, params)
    return {"score": score, "hop": hop, "f0": f0 or 0.0, "mov": mov, "near": near, "far": far,
            "fps": fps, "durada": info["durada"]}


def detecta_de_senyals(sig, params):
    xiulets = troba_xiulets(sig["score"], float(sig["hop"]), params)
    return detecta(xiulets, sig["mov"], sig["near"], sig["far"], int(sig["fps"]), params, float(sig["durada"]))


def compara(det, etiquetes):
    """Compara jugades detectades amb les revisades. Retorna comptadors."""
    veritat = sorted([j for j in etiquetes if not j.get("descartada")], key=lambda j: j["inici"])
    usades, trobades, enc_g, amb_g = set(), 0, 0, 0
    for v in veritat:
        best, bd = None, 4.0
        for k, d in enumerate(det):
            if k in usades:
                continue
            dd = abs(d["inici"] - v["inici"])
            if dd <= bd:
                best, bd = k, dd
        if v.get("guanya_costat"):
            amb_g += 1
        if best is not None:
            usades.add(best); trobades += 1
            if v.get("guanya_costat") and det[best].get("guanya_costat") == v["guanya_costat"]:
                enc_g += 1
    return {"reals": len(veritat), "detectades": len(det), "trobades": trobades,
            "falses": len(det) - len(usades), "amb_guanyador": amb_g, "guanyador_ok": enc_g}


def _suma(rs):
    t = {k: sum(r[k] for r in rs) for k in ("reals", "detectades", "trobades", "falses", "amb_guanyador", "guanyador_ok")}
    t["pct_trobades"] = t["trobades"] / t["reals"] if t["reals"] else 0
    t["pct_falses"] = t["falses"] / t["detectades"] if t["detectades"] else 0
    t["pct_guanyador"] = t["guanyador_ok"] / t["amb_guanyador"] if t["amb_guanyador"] else 0
    t["puntuacio"] = (2 * t["trobades"] / (t["reals"] + t["detectades"]) if t["reals"] + t["detectades"] else 0) + t["pct_guanyador"]
    return t


def carrega_conjunt():
    idx = _jread(INDEX, [])
    out = []
    for e in idx:
        f = APR / e["senyals"]
        if not f.exists():
            print(f"  Avís: falten els senyals de {e['nom']}; torna a executar avalua amb el vídeo.")
            continue
        z = np.load(f)
        sig = {k: z[k] for k in z.files}
        et = _jread(APR / e["etiquetes"], {}).get("jugades", [])
        out.append((e, sig, et))
    return out


def avalua_conjunt(conjunt, params):
    per = []
    for e, sig, et in conjunt:
        per.append(dict(compara(detecta_de_senyals(sig, params), et), nom=e["nom"]))
    return per, _suma(per)


def apren(motiu="aprenentatge", verbose=True):
    conjunt = carrega_conjunt()
    if not conjunt:
        sys.exit("El conjunt d'aprenentatge és buit. Afegeix-hi un partit amb:  avalua VIDEO ETIQUETES")
    base = load_params()
    per0, tot0 = avalua_conjunt(conjunt, base)
    millor, mp = tot0, dict(base)
    import itertools
    combos = list(itertools.product(*GRID.values()))
    print(f"Ajustant paràmetres amb {len(conjunt)} partit(s) ({len(combos)} combinacions)…", flush=True)
    for vals in combos:
        p = dict(base, **dict(zip(GRID.keys(), vals)))
        _, t = avalua_conjunt(conjunt, p)
        if t["puntuacio"] > millor["puntuacio"] + 1e-6:
            millor, mp = t, p
    canvi = any(abs(mp[k] - base[k]) > 1e-9 for k in TUNED)
    if canvi:
        cur = _jread(PARAMS_FILE, {})
        cur.update({k: mp[k] for k in TUNED})
        _jwrite(PARAMS_FILE, cur)
    per1, tot1 = avalua_conjunt(conjunt, mp)
    hist = _jread(HIST, [])
    hist.append({"data": time.strftime("%Y-%m-%d %H:%M"), "motiu": motiu, "partits": len(conjunt),
                 "partits_sencers": sum(1 for e, s, _ in conjunt if float(s["durada"]) >= CRITERIS["durada_partit_sencer_s"]),
                 "abans": {k: round(tot0[k], 4) for k in ("pct_trobades", "pct_falses", "pct_guanyador")},
                 "despres": {k: round(tot1[k], 4) for k in ("pct_trobades", "pct_falses", "pct_guanyador")},
                 "parametres": {k: mp[k] for k in TUNED}, "canvi_parametres": canvi,
                 "per_partit": [{k: r[k] for k in ("nom", "reals", "trobades", "falses", "amb_guanyador", "guanyador_ok")} for r in per1]})
    _jwrite(HIST, hist)
    if verbose:
        print()
        print(f"{'Partit':<38}{'Jugades':>9}{'Trobades':>10}{'Falses':>8}{'Guanyador':>11}")
        for r in per1:
            g = f"{r['guanyador_ok']}/{r['amb_guanyador']}" if r["amb_guanyador"] else "–"
            print(f"{r['nom'][:37]:<38}{r['reals']:>9}{r['trobades']:>10}{r['falses']:>8}{g:>11}")
        print(f"\nTotal amb els paràmetres {'nous' if canvi else 'actuals'}: "
              f"{tot1['pct_trobades']:.0%} trobades · {tot1['pct_falses']:.0%} falses · {tot1['pct_guanyador']:.0%} guanyador encertat")
        if canvi:
            print(f"Abans: {tot0['pct_trobades']:.0%} trobades · {tot0['pct_falses']:.0%} falses · {tot0['pct_guanyador']:.0%} guanyador encertat")
            print(f"Paràmetres nous desats a {PARAMS_FILE.name}: {({k: mp[k] for k in TUNED})}")
        else:
            print("Els paràmetres actuals ja són els millors per al conjunt.")
        estat_fase()
    return tot1


def estat_fase():
    hist = _jread(HIST, [])
    if not hist:
        print("Encara no hi ha cap avaluació.")
        return False
    h = hist[-1]; d = h["despres"]; C = CRITERIS
    estables = 0
    for x in reversed(hist):
        if x.get("motiu") != "partit nou":
            continue
        if x["canvi_parametres"]:
            break
        estables += 1
    checks = [
        (f"Partits sencers revisats: {h['partits_sencers']} de {C['partits_sencers']}", h["partits_sencers"] >= C["partits_sencers"]),
        (f"Jugades trobades: {d['pct_trobades']:.0%} (cal ≥ {C['trobades']:.0%})", d["pct_trobades"] >= C["trobades"]),
        (f"Jugades falses: {d['pct_falses']:.0%} (cal ≤ {C['falses']:.0%})", d["pct_falses"] <= C["falses"]),
        (f"Guanyador encertat: {d['pct_guanyador']:.0%} (cal ≥ {C['guanyador']:.0%})", d["pct_guanyador"] >= C["guanyador"]),
        (f"Partits nous seguits sense canviar paràmetres: {estables} de {C['sense_canvis']}", estables >= C["sense_canvis"]),
    ]
    print("\nCriteris per passar a la fase 2:")
    for t, ok in checks:
        print(f"  [{'x' if ok else ' '}] {t}")
    llest = all(ok for _, ok in checks)
    print("  → La fase 1 ha arribat al seu límit: es pot passar a la fase 2." if llest
          else "  → Encara a la fase 1.")
    return llest


def avalua(video, etiquetes_path, refresca=False):
    """Afegeix (o actualitza) un partit revisat al conjunt i torna a ajustar els paràmetres amb tots."""
    video = Path(video)
    et = _jread(etiquetes_path, None)
    if not et or et.get("app") != "sports-vidia-etiquetes":
        sys.exit("Aquest fitxer no és d'etiquetes de Sports VidIA (es descarrega des de Revisió auto).")
    if not [j for j in et.get("jugades", []) if not j.get("descartada")]:
        sys.exit("Les etiquetes no tenen cap jugada confirmada.")
    cp = Path(str(video) + ".calibratge.json")
    if not cp.exists():
        sys.exit(f"Falta el calibratge de {video.name}.")
    APR.mkdir(exist_ok=True)
    nom = video.name; sl = _slug(nom)
    idx = _jread(INDEX, [])
    e = next((x for x in idx if x["nom"] == nom), None)
    npz = APR / f"{sl}.senyals.npz"
    nou = e is None
    if nou or refresca or not npz.exists():
        print(f"Calculant els senyals de {nom} (només cal fer-ho un cop)…", flush=True)
        sig = calcula_senyals(video, _jread(cp, {}), load_params())
        np.savez_compressed(npz, **{k: np.asarray(v) for k, v in sig.items()})
    shutil.copy(etiquetes_path, APR / f"{sl}.etiquetes.json")
    shutil.copy(cp, APR / f"{sl}.calibratge.json")
    rec = {"nom": nom, "video": str(video.resolve()), "senyals": npz.name,
           "etiquetes": f"{sl}.etiquetes.json", "durada": round(video_info(video)["durada"], 1),
           "jugades_revisades": len([j for j in et["jugades"] if not j.get("descartada")]),
           "afegit": e["afegit"] if e else time.strftime("%Y-%m-%d %H:%M"),
           "actualitzat": time.strftime("%Y-%m-%d %H:%M")}
    idx = [x for x in idx if x["nom"] != nom] + [rec]
    _jwrite(INDEX, idx)
    print(f"{'Afegit' if nou else 'Actualitzat'} al conjunt d'aprenentatge: {nom} ({rec['jugades_revisades']} jugades revisades).")
    return apren("partit nou" if nou else "correccions actualitzades")


def mostra_estat():
    idx = _jread(INDEX, [])
    if not idx:
        print("El conjunt d'aprenentatge és buit.")
        return
    print(f"Conjunt d'aprenentatge ({APR}):")
    for e in idx:
        sencer = "sencer" if e["durada"] >= CRITERIS["durada_partit_sencer_s"] else "fragment"
        print(f"  · {e['nom']}  {e['durada'] / 60:.0f} min ({sencer}) · {e['jugades_revisades']} jugades · actualitzat {e['actualitzat']}")
    hist = _jread(HIST, [])
    if hist:
        print("\nHistorial:")
        for h in hist[-10:]:
            d = h["despres"]
            print(f"  {h['data']}  {h['motiu']:<26} {h['partits']} partits  "
                  f"{d['pct_trobades']:.0%} trobades · {d['pct_falses']:.0%} falses · {d['pct_guanyador']:.0%} guanyador"
                  f"{'  · paràmetres canviats' if h['canvi_parametres'] else ''}")
    estat_fase()


def oblida(nom):
    idx = _jread(INDEX, [])
    e = next((x for x in idx if x["nom"] == nom), None)
    if not e:
        sys.exit(f"No hi ha cap partit anomenat {nom} al conjunt. Mira'ls amb:  estat")
    for f in (e["senyals"], e["etiquetes"], e["etiquetes"].replace(".etiquetes.json", ".calibratge.json")):
        try:
            (APR / f).unlink()
        except FileNotFoundError:
            pass
    _jwrite(INDEX, [x for x in idx if x["nom"] != nom])
    print(f"{nom} tret del conjunt.")
    if len(idx) > 1:
        apren("partit tret")


def main():
    ap = argparse.ArgumentParser(description="Sports VidIA · analitzador automàtic de jugades")
    sub = ap.add_subparsers(dest="ordre", required=True)
    c = sub.add_parser("calibra", help="marca les cantonades de la pista")
    c.add_argument("video"); c.add_argument("--temps", type=float, default=None, help="segon del vídeo a mostrar")
    a = sub.add_parser("analitza", help="detecta les jugades i crea el fitxer per a l'app")
    a.add_argument("video"); a.add_argument("--sortida", default=None)
    e = sub.add_parser("avalua", help="afegeix un partit revisat al conjunt i ajusta els paràmetres amb tots")
    e.add_argument("video"); e.add_argument("etiquetes")
    e.add_argument("--refresca", action="store_true", help="torna a calcular els senyals del vídeo")
    sub.add_parser("apren", help="torna a ajustar els paràmetres amb tot el conjunt")
    sub.add_parser("estat", help="mostra el conjunt, l'historial i els criteris de canvi de fase")
    o = sub.add_parser("oblida", help="treu un partit del conjunt d'aprenentatge")
    o.add_argument("nom", help="nom del fitxer de vídeo, tal com surt a estat")
    args = ap.parse_args()
    if args.ordre == "calibra":
        calibra(args.video, args.temps)
    elif args.ordre == "analitza":
        r = analitza(args.video)
        out = Path(args.sortida or (str(args.video) + ".analisi.json"))
        out.write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
        print(f"Fet: {len(r['jugades'])} jugades detectades. Importa {out.name} a l'app (Revisió auto → Importa l'anàlisi).")
    elif args.ordre == "avalua":
        avalua(args.video, args.etiquetes, args.refresca)
    elif args.ordre == "apren":
        apren("ajust manual")
    elif args.ordre == "estat":
        mostra_estat()
    elif args.ordre == "oblida":
        oblida(args.nom)


if __name__ == "__main__":
    main()
