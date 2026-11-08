"""Relief fin du monde : Copernicus DEM GLO-90 ramené à un point par bloc (1:250, 250 m), en dalles de 512 x 512 blocs.

Lancé dans GitHub Actions (le shell local n'a pas internet). Chantier « relief fin » (08/11/2026) : le monde actuel
vient d'une carte à 3' (~22 blocs par point) ; ces dalles donnent les vraies vallées, crêtes et gorges.

Usage : python outils/relief_fin.py <ouest> <est> <sud> <nord> <dossier_dalles_tif> <sortie>
  (degrés entiers ; les dalles GLO-90 absentes = pleine mer, ignorées)

Région (tx, tz) = blocs x de tx*512 à tx*512+511, z de tz*512 à tz*512+511 ; centre du bloc x : lon = (x + 0.5) / BPD,
lat = -(z + 0.5) / BPD. Altitude = moyenne du relief sur la case du bloc.

Fichier paquet <sortie>/relief_<bx>_<bz>.bin (4 x 4 régions, bx = tx >> 2), petit-boutiste :
  « RELB », int32 version 1, int32 bx, int32 bz, puis 16 x (int32 décalage, int32 longueur) (longueur 0 = région
  sans terre), puis les régions. Région : zlib( 512 x 512 int16 ), ligne z, colonne x, altitude / 5 m arrondie,
  écart avec le voisin de gauche (le premier de chaque ligne en valeur pleine) ; -32768 = pas de donnée (mer).
"""
import os, sys, struct, zlib, math
import numpy as np
import rasterio
from rasterio.merge import merge
from rasterio.enums import Resampling
from rasterio.warp import reproject
from rasterio.transform import from_origin

BPD = 445.28
T = 512
Q = 5
VIDE = -32768


def nom_dalle(lat, lon):
    ns = f"N{lat:02d}" if lat >= 0 else f"S{-lat:02d}"
    ew = f"E{lon:03d}" if lon >= 0 else f"W{-lon:03d}"
    return f"Copernicus_DSM_COG_30_{ns}_00_{ew}_00_DEM"


def dalles_de(dossier):
    d = {}
    for f in os.listdir(dossier):
        if f.endswith(".tif"):
            p = f.split("_")
            lat = int(p[4][1:]) * (1 if p[4][0] == "N" else -1)
            lon = int(p[6][1:]) * (1 if p[6][0] == "E" else -1)
            d[(lat, lon)] = os.path.join(dossier, f)
    return d


def region(tx, tz, dalles):
    o, e = tx * T / BPD, (tx + 1) * T / BPD
    n, s = -tz * T / BPD, -(tz + 1) * T / BPD
    srcs = [rasterio.open(p) for (la, lo), p in dalles.items()
            if la < n and la + 1 > s and lo < e and lo + 1 > o]
    if not srcs:
        return None
    # mosaïque à la résolution d'origine (avec une marge), puis moyenne sur la grille des blocs : une moyenne faite
    # dalle par dalle laisse une ligne vide le long de chaque degré entier (08/11/2026)
    m = 4 / BPD
    mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=np.nan, dtype="float32")
    for r in srcs:
        r.close()
    out = np.full((T, T), np.nan, np.float32)
    reproject(mos[0], out, src_transform=tr, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=from_origin(o, n, 1 / BPD, 1 / BPD), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    if not np.isfinite(out).any() or np.nanmax(out) <= 0:
        return None
    return out


def encoder(a):
    q = np.where(np.isfinite(a), np.round(a / Q), VIDE).astype(np.int32)
    d = q.copy()
    d[:, 1:] = q[:, 1:] - q[:, :-1]
    d[q == VIDE] = VIDE
    d[:, 1:][(q[:, :-1] == VIDE) & (q[:, 1:] != VIDE)] = q[:, 1:][(q[:, :-1] == VIDE) & (q[:, 1:] != VIDE)]
    return zlib.compress(np.clip(d, -32768, 32767).astype("<i2").tobytes(), 9)


def decoder(b):
    d = np.frombuffer(zlib.decompress(b), "<i2").reshape(T, T).astype(np.int32)
    q = np.empty_like(d)
    for z in range(T):
        prec = VIDE
        for x in range(T):
            v = d[z, x]
            if v == VIDE:
                q[z, x] = VIDE
            elif prec == VIDE:
                q[z, x] = v
            else:
                q[z, x] = prec + v
            prec = q[z, x]
    return np.where(q == VIDE, np.nan, q * Q)


def paquet(bx, bz, rs):
    """Paquet de 4 x 4 régions : rs = {index (tx & 3) + 4 * (tz & 3): région encodée}."""
    tete = bytearray(b"RELB" + struct.pack("<iii", 1, bx, bz))
    corps = bytearray()
    dec = 4 + 12 + 16 * 8
    table = []
    for k in range(16):
        b = rs.get(k, b"")
        table.append((dec + len(corps) if b else 0, len(b)))
        corps += b
    for t in table:
        tete += struct.pack("<ii", *t)
    return bytes(tete + corps)


def main():
    o, e, s, n = map(int, sys.argv[1:5])
    dalles = dalles_de(sys.argv[5])
    sortie = sys.argv[6]
    os.makedirs(sortie, exist_ok=True)
    tx0, tx1 = math.floor(o * BPD / T), math.floor(e * BPD / T - 1e-9)
    tz0, tz1 = math.floor(-n * BPD / T), math.floor(-s * BPD / T - 1e-9)
    paquets = {}
    total, nb = 0, 0
    for tz in range(tz0, tz1 + 1):
        for tx in range(tx0, tx1 + 1):
            a = region(tx, tz, dalles)
            if a is None:
                continue
            b = encoder(a)
            paquets.setdefault((tx >> 2, tz >> 2), {})[(tx & 3) + 4 * (tz & 3)] = b
            total += len(b); nb += 1
            print(f"région {tx} {tz} : {len(b) / 1024:.0f} Ko, {np.nanmin(a):.0f} à {np.nanmax(a):.0f} m", flush=True)
    for (bx, bz), rs in paquets.items():
        open(os.path.join(sortie, f"relief_{bx}_{bz}.bin"), "wb").write(paquet(bx, bz, rs))
    print(f"{nb} régions, {len(paquets)} paquets, {total / 1e6:.1f} Mo, {total / max(nb, 1) / 1024:.0f} Ko par région")


if __name__ == "__main__":
    main()
