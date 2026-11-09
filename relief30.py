"""Relief du monde à l'échelle 1:30 (09/11/2026) : Copernicus DEM GLO-30 à un point par bloc (30 m).

Même projection que le mod : centre du bloc x : lon = (x + 0.5) / BPD, lat = -(z + 0.5) / BPD, BPD = 111320 / 30.
Altitude = moyenne du relief sur la case du bloc, arrondie à 5 m (le mod met ~30 m par bloc en hauteur).

Paquets v2 : 16 x 16 régions de 512 blocs (8192 blocs, ~2,2° de côté) ; bx = x >> 13, bz = z >> 13.
Fichier relief30_<bx>_<bz>.bin, petit-boutiste :
  « RELB », int32 version 2, int32 bx, int32 bz, int32 n (= 16 régions par côté), int32 échelle (30),
  puis n * n x (int32 décalage, int32 longueur), index (tx & 15) + 16 * (tz & 15) ; longueur 0 = région sans terre.
  Régions : même codage que la version 1 (relief_fin.py) : zlib(512 x 512 int16), altitude / 5 m, écart avec le
  voisin de gauche, -32768 = pas de donnée (mer).

Une bande = tous les paquets d'un même bz (le tour de la Terre). Chaque paquet est publié dès qu'il est prêt
(release v30-z<bz>), puis les dalles qui ne servent plus sont effacées (14 Go de disque sur les machines GitHub).

Usage : python relief30.py <bz> <sortie> [--publier] [--bx <min> <max>]
"""
import math, os, sys, struct, subprocess, time, urllib.request, urllib.error
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import relief_fin as R

ECHELLE = 30
BPD = 111320 / ECHELLE
T = 512
N = 16                                   # régions par côté de paquet
P = T * N                                # blocs par côté de paquet
DEM = "/tmp/dem30"
SOURCE = "https://copernicus-dem-30m.s3.amazonaws.com"
NOTES = ("Copernicus DEM GLO-30 (© DLR e.V. 2010-2014 et © Airbus Defence and Space GmbH 2014-2018, fourni dans le "
         "cadre du programme Copernicus), ramené à 1 point par bloc (échelle 1:30) pour Domaine d'Aubin – Terre.")


def nom_dalle(lat, lon):
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    return f"Copernicus_DSM_COG_10_{ns}_00_{ew}_00_DEM"


def liste_dalles():
    for essai in range(5):
        try:
            with urllib.request.urlopen(SOURCE + "/tileList.txt", timeout=60) as r:
                noms = set(n.strip() for n in r.read().decode().split() if n.strip())
            print(f"{len(noms)} dalles GLO-30 dans la liste Copernicus", flush=True)
            return noms
        except Exception as e:
            print("liste des dalles :", e, flush=True)
            time.sleep(10)
    return None


def dalle(lat, lon, connues):
    n = nom_dalle(lat, lon)
    p = os.path.join(DEM, n + ".tif")
    if os.path.exists(p):
        return p
    if connues is not None and n not in connues:
        return None
    for essai in range(5):
        try:
            urllib.request.urlretrieve(f"{SOURCE}/{n}/{n}.tif", p + ".part")
            os.replace(p + ".part", p)
            return p
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return None
            print("nouvel essai", n, e, flush=True)
        except Exception as e:
            print("nouvel essai", n, e, flush=True)
        time.sleep(3 * (essai + 1))
    raise RuntimeError("dalle injoignable : " + n)


def bornes(tx0, tz0, nb):
    """ouest, est, sud, nord d'un carré de nb régions dont la première est (tx0, tz0)."""
    return tx0 * T / BPD, (tx0 + nb) * T / BPD, -(tz0 + nb) * T / BPD, -tz0 * T / BPD


def une_region(args):
    tx, tz, chemins = args
    import rasterio
    from rasterio.merge import merge
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.transform import from_origin
    o, e, s, n = bornes(tx, tz, 1)
    srcs = []
    for (la, lo), p in chemins.items():
        if la < n and la + 1 > s and lo < e and lo + 1 > o:
            srcs.append(rasterio.open(p))
    if not srcs:
        return tx, tz, None
    m = 4 / BPD
    mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=np.nan, dtype="float32")
    for r in srcs:
        r.close()
    out = np.full((T, T), np.nan, np.float32)
    reproject(mos[0], out, src_transform=tr, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=from_origin(o, n, 1 / BPD, 1 / BPD), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    if not np.isfinite(out).any() or np.nanmax(out) <= 0:
        return tx, tz, None
    return tx, tz, R.encoder(out)


def paquet(bx, bz, rs):
    tete = bytearray(b"RELB" + struct.pack("<iiiii", 2, bx, bz, N, ECHELLE))
    dec = len(tete) + N * N * 8
    corps = bytearray()
    table = []
    for k in range(N * N):
        b = rs.get(k, b"")
        table.append((dec + len(corps) if b else 0, len(b)))
        corps += b
    for t in table:
        tete += struct.pack("<ii", *t)
    return bytes(tete + corps)


def publier(tag, f):
    for essai in range(8):
        r = subprocess.run(["gh", "release", "upload", tag, f, "--clobber"], capture_output=True, text=True)
        if r.returncode == 0:
            return
        print("envoi refusé, nouvel essai :", r.stderr.strip()[:300], flush=True)
        time.sleep(20 * (essai + 1))
    raise RuntimeError("envoi impossible : " + f)


def main():
    bz, sortie = int(sys.argv[1]), sys.argv[2]
    pub = "--publier" in sys.argv
    bx0, bx1 = -82, 82
    if "--bx" in sys.argv:
        i = sys.argv.index("--bx")
        bx0, bx1 = int(sys.argv[i + 1]), int(sys.argv[i + 2])
    os.makedirs(DEM, exist_ok=True)
    os.makedirs(sortie, exist_ok=True)
    connues = liste_dalles()
    tag = f"v30-z{bz}"
    if pub:
        if subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode != 0:
            subprocess.run(["gh", "release", "create", tag, "--title", f"Relief 1:30, bande z{bz}", "--notes", NOTES],
                           check=False)
    deja = set()
    if pub:                                                    # reprise : paquets déjà publiés sautés
        r = subprocess.run(["gh", "release", "view", tag, "--json", "assets", "-q", ".assets[].name"],
                           capture_output=True, text=True)
        deja = set(r.stdout.split()) if r.returncode == 0 else set()
        print(f"{len(deja)} paquets déjà publiés", flush=True)
    total = nb_p = nb_r = 0
    t0 = time.time()
    with ProcessPoolExecutor(os.cpu_count() or 4) as ex:
        for bx in range(bx0, bx1):
            if f"relief30_{bx}_{bz}.bin" in deja:
                continue
            o, e, s, n = bornes(bx * N, bz * N, N)
            if s >= 90 or n <= -90 or o >= 180 or e <= -180:
                continue
            la0, la1 = max(math.floor(s), -90), min(math.ceil(n), 90)
            lo0, lo1 = max(math.floor(o), -180), min(math.ceil(e), 180)
            chemins = {}
            for lat in range(la0, la1):
                for lon in range(lo0, lo1):
                    p = dalle(lat, lon, connues)
                    if p:
                        chemins[(lat, lon)] = p
            gardees = set(chemins.values())
            for f in os.listdir(DEM):                           # place sur le disque
                if os.path.join(DEM, f) not in gardees:
                    os.remove(os.path.join(DEM, f))
            if not chemins:
                continue
            taches = [(bx * N + (k % N), bz * N + (k // N), chemins) for k in range(N * N)]
            rs = {}
            for tx, tz, b in ex.map(une_region, taches, chunksize=4):
                if b is not None:
                    rs[(tx & (N - 1)) + N * (tz & (N - 1))] = b
            if not rs:
                continue
            data = paquet(bx, bz, rs)
            f = os.path.join(sortie, f"relief30_{bx}_{bz}.bin")
            open(f, "wb").write(data)
            if pub:
                publier(tag, f)
                os.remove(f)
            total += len(data); nb_p += 1; nb_r += len(rs)
            print(f"paquet {bx} {bz} : {len(rs)} régions, {len(data) / 1e6:.1f} Mo, {len(chemins)} dalles, "
                  f"{time.time() - t0:.0f} s", flush=True)
    print(f"bande {bz} : {nb_p} paquets, {nb_r} régions, {total / 1e9:.2f} Go, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
