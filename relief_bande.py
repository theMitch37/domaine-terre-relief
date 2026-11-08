"""Une bande de paquets (même bz, tout le tour de la Terre) : télécharge les dalles Copernicus GLO-90 paquet par paquet,
écrit relief_<bx>_<bz>.bin (format : relief_fin.py), puis efface les dalles qui ne servent plus.

Usage : python relief_bande.py <bz> <sortie>
"""
import math, os, sys, urllib.request, urllib.error
import relief_fin as R

BPD, T = R.BPD, R.T
DEM = "/tmp/dem"
SOURCE = "https://copernicus-dem-90m.s3.amazonaws.com"


def liste_dalles():
    try:
        with urllib.request.urlopen(SOURCE + "/tileList.txt", timeout=60) as r:
            noms = r.read().decode().split()
        print(f"{len(noms)} dalles dans la liste Copernicus", flush=True)
        return set(n.strip() for n in noms if n.strip())
    except Exception as e:
        print("pas de liste des dalles :", e, flush=True)
        return None


def dalle(lat, lon, connues):
    n = R.nom_dalle(lat, lon)
    p = os.path.join(DEM, n + ".tif")
    if os.path.exists(p):
        return True
    if connues is not None and n not in connues:
        return False
    for essai in range(4):
        try:
            urllib.request.urlretrieve(f"{SOURCE}/{n}/{n}.tif", p + ".part")
            os.replace(p + ".part", p)
            return True
        except urllib.error.HTTPError as e:
            if e.code in (403, 404):
                return False
        except Exception as e:
            print("nouvel essai", n, e, flush=True)
    return False


def main():
    bz, sortie = int(sys.argv[1]), sys.argv[2]
    os.makedirs(DEM, exist_ok=True)
    os.makedirs(sortie, exist_ok=True)
    connues = liste_dalles()
    total = nb_paquets = nb_regions = 0
    for bx in range(-40, 40):
        o, e = bx * 4 * T / BPD, (bx + 1) * 4 * T / BPD
        n, s = -bz * 4 * T / BPD, -(bz + 1) * 4 * T / BPD
        la0, la1 = max(math.floor(s) - 1, -90), min(math.ceil(n) + 1, 90)
        lo0, lo1 = max(math.floor(o) - 1, -180), min(math.ceil(e) + 1, 180)
        voulues = set()
        for lat in range(la0, la1):
            for lon in range(lo0, lo1):
                if dalle(lat, lon, connues):
                    voulues.add(R.nom_dalle(lat, lon) + ".tif")
        for f in os.listdir(DEM):                                    # place sur le disque
            if f.endswith(".tif") and f not in voulues:
                os.remove(os.path.join(DEM, f))
        if not voulues:
            continue
        dalles = R.dalles_de(DEM)
        rs = {}
        for k in range(16):
            tx, tz = bx * 4 + (k & 3), bz * 4 + (k >> 2)
            a = R.region(tx, tz, dalles)
            if a is not None:
                rs[k] = R.encoder(a)
        if not rs:
            continue
        b = R.paquet(bx, bz, rs)
        open(os.path.join(sortie, f"relief_{bx}_{bz}.bin"), "wb").write(b)
        total += len(b); nb_paquets += 1; nb_regions += len(rs)
        print(f"paquet {bx} {bz} : {len(rs)} régions, {len(b) / 1e6:.2f} Mo", flush=True)
    print(f"bande {bz} : {nb_paquets} paquets, {nb_regions} régions, {total / 1e6:.1f} Mo")


if __name__ == "__main__":
    main()
