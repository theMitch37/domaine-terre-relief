"""Fond des mers (09/11/2026) : ETOPO 2022 15" (NOAA NCEI, domaine public) sur la grille du relief 1:250.

Même projection et même format que relief_fin.py (paquets RELB v1 de 4 x 4 régions de 512 blocs, altitude / 5 m,
-32768 = pas de donnée), mais seulement en mer et près des côtes (altitude <= +50 m) : le mod s'en sert pour le fond
des mers (monde 1:30 d'abord, lu en bilinéaire). Le relief terrestre reste celui de GLO-30 / GLO-90.

Une bande = tous les paquets d'un même bz. Release f-z<bz>, fichiers fond_<bx>_<bz>.bin.
Usage : python fond.py <bz> <sortie> [--publier]
"""
import math, os, re, sys, subprocess, time, urllib.request, urllib.error
from concurrent.futures import ProcessPoolExecutor
import numpy as np
import relief_fin as R

BPD = R.BPD
T = R.T
DOSSIER = "/tmp/etopo"
SOURCE = "https://www.ngdc.noaa.gov/mgg/global/relief/ETOPO2022/data/15s/15s_surface_elev_gtif/"
NOTES = ("ETOPO 2022 15 Arc-Second Global Relief Model, NOAA National Centers for Environmental Information "
         "(doi:10.25921/fd45-gt74), domaine public ; fond des mers ramené à 250 m pour Domaine d'Aubin – Terre.")
TERRE = 50.0                     # au-dessus : terre ferme, pas gardée


def nom_tuile(lat_haut, lon_gauche):
    ns = f"N{lat_haut:02d}" if lat_haut >= 0 else f"S{-lat_haut:02d}"
    ew = f"E{lon_gauche:03d}" if lon_gauche >= 0 else f"W{-lon_gauche:03d}"
    return f"ETOPO_2022_v1_15s_{ns}{ew}_surface.tif"


def liste():
    for essai in range(5):
        try:
            with urllib.request.urlopen(SOURCE, timeout=60) as r:
                noms = set(re.findall(r'ETOPO_2022_v1_15s_[NS]\d\d[EW]\d{3}_surface\.tif', r.read().decode()))
            print(f"{len(noms)} tuiles ETOPO 15\" dans le dossier NOAA", flush=True)
            return noms
        except Exception as e:
            print("liste des tuiles :", e, flush=True)
            time.sleep(10)
    return None


BAS = False                      # noms par le coin sud-ouest (S90…) plutôt que nord-ouest (N90…), vu dans la liste


def tuile(lat_haut, lon_gauche, connues):
    n = nom_tuile(lat_haut - 15 if BAS else lat_haut, lon_gauche)
    p = os.path.join(DOSSIER, n)
    if os.path.exists(p):
        return p
    if connues is not None and n not in connues:
        return None
    for essai in range(6):
        try:
            urllib.request.urlretrieve(SOURCE + n, p + ".part")
            os.replace(p + ".part", p)
            return p
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            print("nouvel essai", n, e, flush=True)
        except Exception as e:
            print("nouvel essai", n, e, flush=True)
        time.sleep(5 * (essai + 1))
    raise RuntimeError("tuile injoignable : " + n)


def une_region(args):
    tx, tz, chemins = args
    import rasterio
    from rasterio.merge import merge
    from rasterio.enums import Resampling
    from rasterio.warp import reproject
    from rasterio.transform import from_origin
    o, e = tx * T / BPD, (tx + 1) * T / BPD
    n, s = -tz * T / BPD, -(tz + 1) * T / BPD
    srcs = [rasterio.open(p) for (la, lo), p in chemins.items() if la - 15 < n and la > s and lo < e and lo + 15 > o]
    if not srcs:
        return tx, tz, None
    m = 2 / BPD
    mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=np.nan, dtype="float32")
    for r in srcs:
        r.close()
    out = np.full((T, T), np.nan, np.float32)
    reproject(mos[0], out, src_transform=tr, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=from_origin(o, n, 1 / BPD, 1 / BPD), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.bilinear)
    out[out > TERRE] = np.nan
    if not np.isfinite(out).any() or np.nanmin(out) >= 0:
        return tx, tz, None
    return tx, tz, R.encoder(out)


def une_region_diag(args):
    import rasterio
    from rasterio.merge import merge
    tx, tz, chemins = args
    o, e = tx * T / BPD, (tx + 1) * T / BPD
    n, s = -tz * T / BPD, -(tz + 1) * T / BPD
    choisies = [k for k in chemins if k[0] - 15 < n and k[0] > s and k[1] < e and k[1] + 15 > o]
    srcs = [rasterio.open(chemins[k]) for k in choisies]
    if not srcs:
        return ("aucune tuile", (o, e, s, n))
    mos, tr = merge(srcs, bounds=(o, s, e, n), nodata=np.nan, dtype="float32")
    return (choisies, mos.shape, float(np.nanmin(mos)), float(np.nanmax(mos)), int(np.isfinite(mos).sum()))


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
    os.makedirs(DOSSIER, exist_ok=True)
    os.makedirs(sortie, exist_ok=True)
    connues = liste()
    global BAS
    if connues:
        BAS = any("_S90" in c for c in connues) and not any("_N90" in c for c in connues)
        print("noms par le coin", "sud-ouest" if BAS else "nord-ouest", sorted(connues)[:3], flush=True)
    tag = f"f-z{bz}"
    deja = set()
    if pub:
        if subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode != 0:
            subprocess.run(["gh", "release", "create", tag, "--title", f"Fond des mers 15\", bande z{bz}",
                            "--notes", NOTES], check=False)
        r = subprocess.run(["gh", "release", "view", tag, "--json", "assets", "-q", ".assets[].name"],
                           capture_output=True, text=True)
        deja = set(r.stdout.split()) if r.returncode == 0 else set()
    P = 4 * T
    n_haut, s_bas = -bz * P / BPD, -(bz + 1) * P / BPD
    if s_bas >= 90 or n_haut <= -90:
        return
    total = nb = 0
    t0 = time.time()
    bx0, bx1 = math.floor(-180 * BPD / P), math.ceil(180 * BPD / P)
    with ProcessPoolExecutor(os.cpu_count() or 4) as ex:
        for bx in range(bx0, bx1):
            if f"fond_{bx}_{bz}.bin" in deja:
                continue
            o, e = bx * P / BPD, (bx + 1) * P / BPD
            if o >= 180 or e <= -180:
                continue
            chemins = {}
            for lh in range(90, -90, -15):                      # bord nord des tuiles de 15°
                if lh - 15 >= n_haut or lh <= s_bas:
                    continue
                for lg in range(-180, 180, 15):                 # bord ouest
                    if lg >= e or lg + 15 <= o:
                        continue
                    p = tuile(lh, lg, connues)
                    if p:
                        chemins[(lh, lg)] = p
                    elif bx % 10 == 0:
                        print("  tuile absente", lh, lg, nom_tuile(lh - 15 if BAS else lh, lg), flush=True)
            if not chemins:
                continue
            if nb == 0 and not getattr(main, "vu", False):
                main.vu = True
                print("tuiles du premier paquet :", {k: os.path.getsize(v) for k, v in chemins.items()}, flush=True)
                try:
                    import rasterio
                    with rasterio.open(next(iter(chemins.values()))) as r:
                        print("  ", r.bounds, r.res, r.nodata, r.dtypes, flush=True)
                    print("  région test :", une_region_diag((bx * 4, bz * 4, chemins)), flush=True)
                except Exception as ex2:
                    import traceback; traceback.print_exc()
            taches = [(bx * 4 + (k % 4), bz * 4 + (k // 4), chemins) for k in range(16)]
            rs = {}
            for tx, tz, b in ex.map(une_region, taches):
                if b is not None:
                    rs[(tx & 3) + 4 * (tz & 3)] = b
            for (lh, lg), p in list(chemins.items()):        # tuiles finies (à l'ouest) : place sur le disque
                if lg + 15 <= e and os.path.exists(p):
                    os.remove(p)
            print(f"  bx {bx} ({o:.1f}° à {e:.1f}°) : {len(chemins)} tuiles, {len(rs)} régions de mer", flush=True)
            if not rs:
                continue
            data = R.paquet(bx, bz, rs)
            f = os.path.join(sortie, f"fond_{bx}_{bz}.bin")
            open(f, "wb").write(data)
            if pub:
                publier(tag, f)
                os.remove(f)
            total += len(data); nb += 1
            print(f"paquet {bx} {bz} : {len(rs)} régions, {len(data) / 1e3:.0f} Ko, {time.time() - t0:.0f} s", flush=True)
    print(f"bande {bz} : {nb} paquets, {total / 1e6:.1f} Mo, {time.time() - t0:.0f} s")


if __name__ == "__main__":
    main()
