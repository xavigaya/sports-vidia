#!/usr/bin/env python3
"""
Sports VidIA · servidor local

Obre l'app al navegador connectada a una carpeta de vídeos del PC:
llista els vídeos, calibra la pista des del navegador, analitza en segon pla,
serveix el vídeo i, quan acabes la revisió, aprèn de les correccions.

Només escolta a 127.0.0.1 (aquest ordinador). Ús:
  py vidia_analitza.py inicia "C:\\Videos\\Partits"
"""
import io, json, mimetypes, queue, re, sys, threading, time, traceback, webbrowser
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import numpy as np

import vidia_analitza as va

ARREL_APP = va.AQUI.parent          # carpeta del repositori (index.html, sw.js, icones…)
EXT_VIDEO = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
ESTATIC = {"index.html", "sw.js", "manifest.webmanifest"}


class Estat:
    def __init__(self, carpeta):
        self.carpeta = Path(carpeta).resolve()
        self.feines = {}          # nom -> {tipus, estat, progres, missatge, fi}
        self.cua = queue.Queue()
        self.durades = {}
        self.lock = threading.Lock()

    def video(self, nom):
        """Retorna el camí d'un vídeo de la carpeta, o None si el nom no és vàlid."""
        if not nom or "/" in nom or "\\" in nom or nom.startswith("."):
            return None
        p = self.carpeta / nom
        if p.suffix.lower() in EXT_VIDEO and p.is_file() and p.parent == self.carpeta:
            return p
        return None

    def llista(self):
        idx = {e["nom"]: e for e in va._jread(va.INDEX, [])}
        out = []
        for p in sorted(self.carpeta.iterdir(), key=lambda x: x.name.lower()):
            if p.suffix.lower() not in EXT_VIDEO or not p.is_file():
                continue
            st = p.stat()
            key = (p.name, st.st_mtime, st.st_size)
            if key not in self.durades:
                try:
                    self.durades[key] = round(va.video_info(p)["durada"], 1)
                except SystemExit:
                    self.durades[key] = None
            out.append({
                "nom": p.name, "mida_mb": round(st.st_size / 1e6, 1), "durada": self.durades[key],
                "calibrat": Path(str(p) + ".calibratge.json").exists(),
                "analitzat": Path(str(p) + ".analisi.json").exists(),
                "etiquetes": Path(str(p) + ".etiquetes.json").exists(),
                "al_conjunt": p.name in idx,
                "feina": self.feines.get(p.name),
            })
        return out


def treballador(E):
    while True:
        tipus, nom, extra = E.cua.get()
        f = E.feines[nom]
        f.update(estat="en curs", progres=0.0, inici=time.time())
        va.PROGRES = lambda x: f.update(progres=x)
        try:
            video = E.video(nom)
            if tipus == "analitza":
                va._progres(0.02)
                r = va.analitza(video)
                Path(str(video) + ".analisi.json").write_text(json.dumps(r, indent=1, ensure_ascii=False), encoding="utf-8")
                f.update(estat="fet", missatge=f"{len(r['jugades'])} jugades detectades")
            elif tipus == "avalua":
                tot = va.avalua(video, Path(str(video) + ".etiquetes.json"))
                f.update(estat="fet", missatge="Après: " + va._fmt_tot(tot))
        except SystemExit as e:
            f.update(estat="error", missatge=str(e))
        except Exception as e:
            traceback.print_exc()
            f.update(estat="error", missatge=f"{type(e).__name__}: {e}")
        finally:
            va.PROGRES = None
            f.update(fi=time.time(), progres=1.0 if f["estat"] == "fet" else f.get("progres", 0))
            E.cua.task_done()


def encua(E, tipus, nom, extra=None):
    with E.lock:
        f = E.feines.get(nom)
        if f and f["estat"] in ("a la cua", "en curs"):
            return f
        E.feines[nom] = {"tipus": tipus, "estat": "a la cua", "progres": 0.0, "missatge": "", "fi": None}
        E.cua.put((tipus, nom, extra))
        return E.feines[nom]


def fotograma(video, t):
    import cv2
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_MSEC, max(0.0, t) * 1000)
    ok, img = cap.read()
    cap.release()
    return img if ok else None


def fa_handler(E):
    class H(BaseHTTPRequestHandler):
        server_version = "SportsVidIA/" + va.VERSIO

        def log_message(self, fmt, *args):      # silenci excepte errors
            if args and str(args[1])[:1] in "45":
                sys.stderr.write("  %s %s\n" % (self.path, args[1]))

        # ---- respostes
        def _json(self, obj, code=200):
            b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def _err(self, code, msg):
            self._json({"error": msg}, code)

        def _bytes(self, b, ctype, cache="no-store"):
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", cache)
            self.send_header("Content-Length", str(len(b)))
            self.end_headers(); self.wfile.write(b)

        def _cos(self):
            n = int(self.headers.get("Content-Length") or 0)
            if n > 20_000_000:
                raise ValueError("massa gran")
            return json.loads(self.rfile.read(n) or b"{}")

        def _v(self, q):
            nom = (q.get("v") or [""])[0]
            p = E.video(nom)
            if not p:
                self._err(404, "No trobo aquest vídeo a la carpeta.")
            return nom, p

        # ---- vídeo amb suport de salts (Range)
        def _video(self, p):
            size = p.stat().st_size
            ctype = mimetypes.guess_type(p.name)[0] or "video/mp4"
            rng = self.headers.get("Range")
            a, b = 0, size - 1
            m = re.match(r"bytes=(\d*)-(\d*)", rng or "")
            if m:
                if m.group(1):
                    a = int(m.group(1)); b = int(m.group(2)) if m.group(2) else size - 1
                else:
                    a = size - int(m.group(2)); b = size - 1
                b = min(b, size - 1)
                self.send_response(206)
                self.send_header("Content-Range", f"bytes {a}-{b}/{size}")
            else:
                self.send_response(200)
            n = b - a + 1
            self.send_header("Content-Type", ctype)
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", str(n))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            with open(p, "rb") as f:
                f.seek(a)
                while n > 0:
                    chunk = f.read(min(1 << 20, n))
                    if not chunk:
                        break
                    try:
                        self.wfile.write(chunk)
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    n -= len(chunk)

        # ---- GET
        def do_GET(self):
            u = urlparse(self.path); q = parse_qs(u.query); path = u.path
            if path in ("/", ""):
                path = "/index.html"
            if path == "/api/estat":
                return self._json({"app": "sports-vidia-local", "versio": va.VERSIO, "carpeta": str(E.carpeta),
                                   "videos": E.llista(), "fase": va.estat_dict()})
            if path == "/api/video":
                nom, p = self._v(q)
                return p and self._video(p)
            if path == "/api/analisi":
                nom, p = self._v(q)
                if not p:
                    return
                f = Path(str(p) + ".analisi.json")
                return self._bytes(f.read_bytes(), "application/json; charset=utf-8") if f.exists() else self._err(404, "Encara no està analitzat.")
            if path == "/api/calibratge":
                nom, p = self._v(q)
                if not p:
                    return
                f = Path(str(p) + ".calibratge.json")
                return self._bytes(f.read_bytes(), "application/json; charset=utf-8") if f.exists() else self._err(404, "Sense calibratge.")
            if path == "/api/fotograma":
                import cv2
                nom, p = self._v(q)
                if not p:
                    return
                t = float((q.get("t") or ["30"])[0])
                img = fotograma(p, t)
                if img is None:
                    return self._err(404, "No he pogut llegir aquest moment del vídeo.")
                if img.shape[1] > 1280:
                    s = 1280 / img.shape[1]; img = cv2.resize(img, None, fx=s, fy=s)
                ok, buf = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 85])
                return self._bytes(buf.tobytes(), "image/jpeg")
            if path == "/api/previsualitza":
                nom, p = self._v(q)
                if not p:
                    return
                f = Path(str(p) + ".calibratge.jpg")
                return self._bytes(f.read_bytes(), "image/jpeg") if f.exists() else self._err(404, "Sense imatge.")
            # fitxers de l'app
            rel = path.lstrip("/")
            if rel in ESTATIC or re.fullmatch(r"icons/[\w.\-]+\.png", rel):
                f = ARREL_APP / rel
                if f.is_file():
                    ctype = {".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8",
                             ".webmanifest": "application/manifest+json", ".png": "image/png"}.get(f.suffix, "application/octet-stream")
                    return self._bytes(f.read_bytes(), ctype, "no-cache")
            self._err(404, "No trobat.")

        # ---- POST
        def do_POST(self):
            u = urlparse(self.path); q = parse_qs(u.query); path = u.path
            if self.headers.get("Origin") and not re.match(r"https?://(127\.0\.0\.1|localhost)(:\d+)?$", self.headers["Origin"]):
                return self._err(403, "Origen no permès.")
            nom, p = self._v(q)
            if not p:
                return
            try:
                cos = self._cos()
            except Exception:
                return self._err(400, "Dades no vàlides.")
            if path == "/api/calibra":
                import cv2
                t = float(cos.get("t", 30))
                img = fotograma(p, t)
                if img is None:
                    return self._err(400, "No he pogut llegir aquest moment del vídeo.")
                h, w = img.shape[:2]
                punts = [(float(x) * w, float(y) * h) for x, y in cos.get("punts", [])]
                if len(punts) not in (4, 7):
                    return self._err(400, "Calen 4 o 7 punts.")
                cal = va.calibratge_de_punts(punts, w, h, cv2.cvtColor(img, cv2.COLOR_BGR2GRAY))
                cal["temps_fotograma"] = t
                Path(str(p) + ".calibratge.json").write_text(json.dumps(cal, indent=2, ensure_ascii=False), encoding="utf-8")
                cv2.imwrite(str(p) + ".calibratge.jpg", va.dibuixa_calibratge(img, cal))
                va.oblida_senyals(p)
                return self._json({"ok": True, "lent_k": cal["lent_k"]})
            if path == "/api/copia-calibratge":
                src = E.video(cos.get("de", ""))
                f = Path(str(src) + ".calibratge.json") if src else None
                if not f or not f.exists():
                    return self._err(400, "Aquell vídeo no té calibratge.")
                Path(str(p) + ".calibratge.json").write_bytes(f.read_bytes())
                img = fotograma(p, float(va._jread(f, {}).get("temps_fotograma", 30)))
                if img is not None:
                    import cv2
                    cv2.imwrite(str(p) + ".calibratge.jpg", va.dibuixa_calibratge(img, va._jread(f, {})))
                va.oblida_senyals(p)
                return self._json({"ok": True})
            if path == "/api/analitza":
                if not Path(str(p) + ".calibratge.json").exists():
                    return self._err(400, "Primer cal calibrar la pista d'aquest vídeo.")
                return self._json({"ok": True, "feina": encua(E, "analitza", nom)})
            if path == "/api/etiquetes":
                if cos.get("app") != "sports-vidia-etiquetes" or not isinstance(cos.get("jugades"), list):
                    return self._err(400, "Les correccions no tenen el format esperat.")
                Path(str(p) + ".etiquetes.json").write_text(json.dumps(cos, indent=1, ensure_ascii=False), encoding="utf-8")
                return self._json({"ok": True, "feina": encua(E, "avalua", nom)})
            self._err(404, "No trobat.")

    return H


def inicia(carpeta, port=8765, obre=True):
    va._consola_utf8()
    E = Estat(carpeta)
    if not E.carpeta.is_dir():
        sys.exit(f"No trobo la carpeta {E.carpeta}")
    if not (ARREL_APP / "index.html").exists():
        sys.exit("No trobo l'app (index.html). Executa l'analitzador des de la carpeta del repositori.")
    threading.Thread(target=treballador, args=(E,), daemon=True).start()
    try:
        srv = ThreadingHTTPServer(("127.0.0.1", port), fa_handler(E))
    except OSError:
        sys.exit(f"El port {port} ja està ocupat. Prova amb:  inicia \"{carpeta}\" --port {port + 1}")
    url = f"http://localhost:{port}/"
    n = len(E.llista())
    print(f"Sports VidIA en mode local · {n} vídeo(s) a {E.carpeta}")
    print(f"Obre {url} al navegador (Chrome o Edge). Per aturar-ho, prem Ctrl+C.")
    if obre:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nAturat.")
