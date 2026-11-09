"""Sol du monde 1:30 (09/11/2026) : couverture du sol et roche, un octet chacun par bloc.

Croise :
- ESA WorldCover 10 m (2021, v200, CC BY 4.0) : classe la plus fréquente sur la case de 30 m du bloc ;
- Macrostrat (CC BY 4.0, cartes géologiques du monde, au plus détaillé disponible) : famille de roche (ROCHES) de
  l'unité géologique qui affleure au bloc, tirée des tuiles vectorielles « carto » (zoom 12) et des définitions.

Même projection que le mod : centre du bloc x : lon = (x + 0.5) / BPD, lat = -(z + 0.5) / BPD, BPD = 111320 / 30.
Paquets : format v2 du relief (outils relief30.py : « RELB » v2, 16 x 16 régions de 512 blocs, zlib int16 en écarts
avec le voisin de gauche) ; valeur = roche * 256 + couverture ; -32768 = pas de donnée. Fichiers sol30_<bx>_<bz>.bin.

Usage : python outils/sol30.py <zones.json> <noms,séparés,virgules> <sortie>   (régions de 512 couvrant les zones)
"""
import io, json, math, os, re, struct, sys, time, urllib.request, zlib
from collections import Counter
import numpy as np

ECHELLE = 30
BPD = 111320 / ECHELLE
T, N = 512, 16
VIDE = -32768
WC = "https://esa-worldcover.s3.eu-central-1.amazonaws.com/v200/2021/map/ESA_WorldCover_10m_2021_v200_{}_Map.tif"
MS = "https://macrostrat.org/api/v2"
TUILES = "https://tiles.macrostrat.org/carto/{z}/{x}/{y}.mvt"
Z = 12

# familles de roche (le mod lit ces numéros : monde/SolFeature.java)
ROCHES = ["inconnue", "gres", "argile", "calcaire", "dolomie", "craie", "conglomerat", "evaporite", "basalte",
          "andesite", "tuf", "granite", "gabbro", "gneiss", "quartzite", "marbre", "meuble", "glaciaire", "dune"]
MOTS = [  # (mot dans le nom de la lithologie, famille) : le premier qui correspond gagne
    ("basalt", "basalte"), ("dune", "dune"), ("loess", "meuble"), ("till", "glaciaire"), ("moraine", "glaciaire"),
    ("glacial", "glaciaire"), ("alluvi", "meuble"), ("gravel", "meuble"), ("unconsolidated", "meuble"),
    ("colluvi", "meuble"), ("sand", "gres"), ("arkose", "gres"), ("wacke", "gres"), ("arenite", "gres"),
    ("conglomerate", "conglomerat"), ("breccia", "conglomerat"), ("chalk", "craie"), ("dolo", "dolomie"),
    ("limestone", "calcaire"), ("carbonate", "calcaire"), ("travertine", "calcaire"), ("marble", "marbre"),
    ("quartzite", "quartzite"), ("gypsum", "evaporite"), ("evaporite", "evaporite"), ("salt", "evaporite"),
    ("anhydrite", "evaporite"), ("eolian", "gres"), ("shale", "argile"), ("mud", "argile"), ("silt", "argile"), ("clay", "argile"),
    ("argillite", "argile"), ("marl", "argile"), ("slate", "gneiss"), ("phyllite", "gneiss"), ("schist", "gneiss"),
    ("gneiss", "gneiss"), ("migmatite", "gneiss"), ("amphibolite", "gneiss"), ("metavolcanic", "gneiss"),
    ("metasediment", "gneiss"), ("metamorphic", "gneiss"), ("basalt", "basalte"), ("mafic volcanic", "basalte"),
    ("lava", "basalte"), ("rhyolite", "tuf"), ("tuff", "tuf"), ("ignimbrite", "tuf"), ("pyroclastic", "tuf"),
    ("volcaniclastic", "tuf"), ("andesite", "andesite"), ("dacite", "andesite"), ("volcanic", "andesite"),
    ("gabbro", "gabbro"), ("diorite", "gabbro"), ("ultramafic", "gabbro"), ("peridotite", "gabbro"),
    ("mafic", "gabbro"), ("granit", "granite"), ("tonalite", "granite"), ("monzonite", "granite"),
    ("syenite", "granite"), ("pegmatite", "granite"), ("plutonic", "granite"), ("felsic", "granite"),
    ("intrusive", "granite"), ("igneous", "granite"), ("sedimentary", "argile"), ("siliciclastic", "gres")]


def famille(texte):
    t = (texte or "").lower()
    for m, f in MOTS:
        if (re.search(r"\b" + m + r"\b", t) if m == "salt" else m in t):
            return ROCHES.index(f)
    return 0


def famille_props(p):
    """Famille d'une unité d'après les propriétés de la tuile carto (lithologie principale d'abord)."""
    txt = p.get("lith") or ""
    maj = txt.split("Major:{")[1].split("}")[0] if "Major:{" in txt else txt
    for t in (maj, txt, p.get("lith_type1"), p.get("name"), p.get("descrip"), p.get("lith_class1")):
        f = famille(t)
        if f:
            return f
    return 0


def get(url, essais=5, binaire=False):
    for k in range(essais):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "DomaineTerre-sol30"})
            with urllib.request.urlopen(req, timeout=60) as r:
                b = r.read()
            return b if binaire else json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            time.sleep(2 * (k + 1))
        except Exception:
            time.sleep(2 * (k + 1))
    return None


# ---------------------------------------------------------------- WorldCover

def couverture(tx, tz, dossier):
    import rasterio
    from rasterio.merge import merge
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    o, e = tx * T / BPD, (tx + 1) * T / BPD
    n, s = -tz * T / BPD, -(tz + 1) * T / BPD
    srcs = []
    for la in range(math.floor(s / 3) * 3, math.floor(n / 3) * 3 + 1, 3):
        for lo in range(math.floor(o / 3) * 3, math.floor(e / 3) * 3 + 1, 3):
            nom = (f"N{la:02d}" if la >= 0 else f"S{-la:02d}") + (f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}")
            p = os.path.join(dossier, nom + ".tif")
            if not os.path.exists(p):
                b = get(WC.format(nom), binaire=True)
                if b is None:
                    continue
                open(p, "wb").write(b)
            srcs.append(rasterio.open(p))
    out = np.zeros((T, T), np.uint8)
    if not srcs:
        return out
    m = 2 / BPD
    mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=0)
    reproject(mos[0], out, src_transform=tr, src_crs="EPSG:4326", src_nodata=0,
              dst_transform=from_origin(o, n, 1 / BPD, 1 / BPD), dst_crs="EPSG:4326", dst_nodata=0,
              resampling=Resampling.mode)
    return out


# ---------------------------------------------------------------- Macrostrat

def tuile_xy(lon, lat, z):
    n = 2 ** z
    x = (np.asarray(lon) + 180) / 360 * n
    y = (1 - np.arcsinh(np.tan(np.radians(lat))) / np.pi) / 2 * n
    return x, y


_unites = {}


def unite(map_id):
    """Famille de roche d'une unité de carte Macrostrat (lithologie principale)."""
    if map_id in _unites:
        return _unites[map_id]
    j = get(f"{MS}/geologic_units/map?map_id={map_id}")
    f = 0
    try:
        d = j["success"]["data"][0]
        txt = d.get("lith") or ""
        maj = txt.split("Major:{")[1].split("}")[0] if "Major:{" in txt else txt
        f = famille(maj) or famille(txt) or famille(d.get("name")) or famille(d.get("descrip"))
    except Exception:
        pass
    _unites[map_id] = f
    return f


def roche(tx, tz, journal):
    """Famille de roche par bloc d'une région, depuis les tuiles vectorielles carto de Macrostrat (zoom Z)."""
    import mapbox_vector_tile
    from PIL import Image, ImageDraw
    xs = (tx * T + np.arange(T) + 0.5) / BPD
    zs = -(tz * T + np.arange(T) + 0.5) / BPD
    LON, LAT = np.meshgrid(xs, zs)
    fx, fy = tuile_xy(LON, LAT, Z)
    out = np.zeros((T, T), np.uint8)
    R = 1024                                             # trame d'une tuile
    for ty in range(int(fy.min()), int(fy.max()) + 1):
        for txx in range(int(fx.min()), int(fx.max()) + 1):
            b = get(TUILES.format(z=Z, x=txx, y=ty), binaire=True)
            if not b:
                continue
            try:
                d = mapbox_vector_tile.decode(b)
            except Exception as ex:
                journal.append(f"tuile {txx} {ty} illisible : {ex}")
                continue
            img = Image.new("I", (R, R), 0)
            dr = ImageDraw.Draw(img)
            ids = {}
            for nom_couche, c in d.items():
                ext = c.get("extent", 4096)
                for f in c["features"]:
                    p = f.get("properties", {})
                    mid = p.get("map_id")
                    if mid is None:
                        continue
                    if not journal or len(journal) < 3:
                        journal.append(f"couche {nom_couche} : {sorted(p.keys())}")
                    k = ids.setdefault(mid, len(ids) + 1)
                    if mid not in _unites:
                        _unites[mid] = famille_props(p)
                    g = f["geometry"]
                    polys = [g["coordinates"]] if g["type"] == "Polygon" else g["coordinates"] if g["type"] == "MultiPolygon" else []
                    for poly in polys:
                        for i, anneau in enumerate(poly):
                            pts = [(px * R / ext, R - py * R / ext) for px, py in anneau]   # y vers le haut dans la tuile
                            if len(pts) >= 3:
                                dr.polygon(pts, fill=k if i == 0 else 0)
            a = np.array(img)
            sel = (fx >= txx) & (fx < txx + 1) & (fy >= ty) & (fy < ty + 1)
            px = np.clip(((fx[sel] - txx) * R).astype(int), 0, R - 1)
            py = np.clip(((fy[sel] - ty) * R).astype(int), 0, R - 1)
            v = a[py, px]
            inv = {k: _unites.get(m, 0) for m, k in ids.items()}
            out[sel] = np.array([inv.get(int(k), 0) for k in v], np.uint8)
    return out


# ---------------------------------------------------------------- eau (09/11/2026)

DEM = "https://copernicus-dem-30m.s3.amazonaws.com/{n}/{n}.tif"
M = 48                                                   # marge autour de la région (continuité des niveaux)


def grille(tx, tz, marge, srcs, nodata, moyenne=True):
    """Valeurs des sources sur la grille des blocs d'une région agrandie de `marge` blocs (moyenne par case)."""
    from rasterio.merge import merge
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    W = T + 2 * marge
    o, n = (tx * T - marge) / BPD, -(tz * T - marge) / BPD
    e, s = o + W / BPD, n - W / BPD
    out = np.full((W, W), np.nan, np.float32)
    if not srcs:
        return out
    m = 2 / BPD
    mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=nodata, dtype="float32")
    reproject(mos[0], out, src_transform=tr, src_crs="EPSG:4326", src_nodata=nodata,
              dst_transform=from_origin(o, n, 1 / BPD, 1 / BPD), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average if moyenne else Resampling.nearest)
    return out


def ouvrir(urls_noms, dossier):
    import rasterio
    srcs = []
    for url, nom in urls_noms:
        p = os.path.join(dossier, nom)
        if not os.path.exists(p):
            b = get(url, binaire=True)
            if b is None:
                continue
            open(p, "wb").write(b)
        srcs.append(rasterio.open(p))
    return srcs


# ---------------------------------------------------------------- ruisseaux (09/11/2026)

G = 4                       # blocs par case du calcul d'écoulement (tracés plus souples qu'à 8)
MR = 2048                   # marge (blocs) autour du paquet : bassins versants qui débordent
A_RUISSEAU = 2.0e6          # m² de bassin versant pour qu'un ruisseau commence
A_LARGE = 2.0e7             # m² au-delà desquels il fait 2 à 3 blocs de large


def ecoulement(z):
    """Remplissage des cuvettes et sens d'écoulement (Barnes 2014, priority-flood + epsilon) : l'eau part de chaque
    case vers celle qui l'a découverte. Bords et vide (mer) = exutoires. Rend (relief rempli, receveur, ordre)."""
    import heapq
    H, W = z.shape
    zz = np.where(np.isfinite(z), z, -1e9).astype(np.float64).ravel()
    vu = np.zeros(H * W, bool)
    recv = np.full(H * W, -1, np.int64)
    tas = []
    for i in range(H * W):
        y, x = divmod(i, W)
        if y == 0 or x == 0 or y == H - 1 or x == W - 1 or zz[i] < -1e8:
            heapq.heappush(tas, (zz[i], i))
            vu[i] = True
    ordre = []
    voisins = (-W - 1, -W, -W + 1, -1, 1, W - 1, W, W + 1)
    while tas:
        h, i = heapq.heappop(tas)
        ordre.append(i)
        y, x = divmod(i, W)
        for dv in voisins:
            j = i + dv
            if j < 0 or j >= H * W:
                continue
            yj, xj = divmod(j, W)
            if abs(yj - y) > 1 or abs(xj - x) > 1 or vu[j]:
                continue
            vu[j] = True
            if zz[j] <= h:
                zz[j] = h + 1e-4
            recv[j] = i
            heapq.heappush(tas, (zz[j], j))
    return zz.reshape(H, W), recv, np.array(ordre, np.int64)


def ruisseaux_paquet(bx, bz, dem_dir):
    """Petits cours d'eau d'un paquet (et de sa marge M) au fond des vraies vallées : écoulement sur le relief 30 m
    moyenné par cases de G blocs, ruisseau dès A_RUISSEAU de bassin versant, tracé bloc par bloc d'une case à sa
    receveuse. Rend (masque, niveau en m) sur la grille du paquet agrandi de M blocs."""
    x0, z0 = bx * T * N - MR, bz * T * N - MR
    W = (T * N + 2 * MR) // G
    o, n = x0 / BPD, -z0 / BPD
    e, s = o + W * G / BPD, n - W * G / BPD
    noms = []
    for la in range(math.floor(s), math.floor(n) + 1):
        for lo in range(math.floor(o), math.floor(e) + 1):
            nn = (f"Copernicus_DSM_COG_10_{'N' if la >= 0 else 'S'}{abs(la):02d}_00_"
                  f"{'E' if lo >= 0 else 'W'}{abs(lo):03d}_00_DEM")
            noms.append((DEM.format(n=nn), nn + ".tif"))
    srcs = ouvrir(noms, dem_dir)
    L = T * N + 2 * M
    masque = np.zeros((L, L), bool)
    niveau = np.full((L, L), np.nan, np.float32)
    if not srcs:
        return masque, niveau
    from rasterio.merge import merge
    from rasterio.warp import reproject
    from rasterio.enums import Resampling
    from rasterio.transform import from_origin
    mos, tr = merge(srcs, bounds=(o, s, e, n), nodata=np.nan, dtype="float32")
    z = np.full((W, W), np.nan, np.float32)
    reproject(mos[0], z, src_transform=tr, src_crs="EPSG:4326", src_nodata=np.nan,
              dst_transform=from_origin(o, n, G / BPD, G / BPD), dst_crs="EPSG:4326", dst_nodata=np.nan,
              resampling=Resampling.average)
    del mos
    z = np.where(np.isfinite(z) & (z > 0.5), z, np.nan)
    t0 = time.time()
    zr, recv, ordre = ecoulement(z)
    aire = np.ones(W * W, np.float64)
    for i in ordre[::-1]:
        r = recv[i]
        if r >= 0:
            aire[r] += aire[i]
    aire *= (G * 30.0) ** 2
    print(f"  écoulement {W}x{W} : {time.time() - t0:.0f} s", flush=True)
    # segments case -> receveuse, pour les cases de ruisseau sur terre ferme
    src = np.nonzero((aire >= A_RUISSEAU) & np.isfinite(z.ravel()) & (recv >= 0))[0]
    if src.size == 0:
        return masque, niveau
    dst = recv[src]
    ya, xa = np.divmod(src, W)
    yb, xb = np.divmod(dst, W)
    za, zb = zr.ravel()[src], zr.ravel()[dst]
    zb = np.where(zb < -1e8, za, zb)
    large = aire[src] >= A_LARGE
    decal = MR - M                                   # case -> bloc du paquet agrandi de M
    for k in range(G + 1):
        t = k / G
        bxk = ((xa + (xb - xa) * t) * G + G / 2 - decal).astype(np.int64)
        bzk = ((ya + (yb - ya) * t) * G + G / 2 - decal).astype(np.int64)
        zk = (za + (zb - za) * t).astype(np.float32)
        for dx, dz, sel in ((0, 0, None), (1, 0, large), (0, 1, large)):
            xx, yy, vv = bxk + dx, bzk + dz, zk
            if sel is not None:
                xx, yy, vv = xx[sel], yy[sel], vv[sel]
            ok = (xx >= 0) & (xx < L) & (yy >= 0) & (yy < L)
            masque[yy[ok], xx[ok]] = True
            niveau[yy[ok], xx[ok]] = np.fmin(niveau[yy[ok], xx[ok]], vv[ok])
    return masque, niveau


def eau(tx, tz, wc_dir, dem_dir, ruis=None):
    """Eau intérieure d'une région : niveau (altitude réelle de la surface) et distance signée à la berge.

    Valeur = round((niveau + 500) / 3) * 16 + c (niveau au pas de 3 m, jusqu'à ~5 640 m ; tient dans un int16 positif) :
    c de 0 à 7 dans l'eau, distance à la berge 1, 2, 3, 4, 6, 9, 13, 16 blocs ; c de 8 à 15 sur la terre proche,
    distance à l'eau 1, 2, 3, 4, 6, 9, 12, 15 blocs (niveau de l'eau la plus proche) ; VIDE ailleurs. La mer (relief
    <= 0,5 m) est laissée aux côtes du mod.
    """
    import rasterio
    from scipy import ndimage
    W = T + 2 * M
    o, n = (tx * T - M) / BPD, -(tz * T - M) / BPD
    e, s = o + W / BPD, n - W / BPD
    # WorldCover : part d'eau (classe 80) dans chaque case
    wc = []
    for la in range(math.floor(s / 3) * 3, math.floor(n / 3) * 3 + 1, 3):
        for lo in range(math.floor(o / 3) * 3, math.floor(e / 3) * 3 + 1, 3):
            nom = (f"N{la:02d}" if la >= 0 else f"S{-la:02d}") + (f"E{lo:03d}" if lo >= 0 else f"W{-lo:03d}")
            wc.append((WC.format(nom), nom + ".tif"))
    srcs = ouvrir(wc, wc_dir)
    part = np.zeros((W, W), np.float32)
    if srcs:
        from rasterio.io import MemoryFile
        from rasterio.merge import merge
        m = 2 / BPD
        mos, tr = merge(srcs, bounds=(o - m, s - m, e + m, n + m), nodata=0)
        masque = (mos[0] == 80).astype(np.float32)
        with MemoryFile() as mf:
            with mf.open(driver="GTiff", width=masque.shape[1], height=masque.shape[0], count=1, dtype="float32",
                         crs="EPSG:4326", transform=tr) as ds:
                ds.write(masque, 1)
            with mf.open() as ds:
                part = np.nan_to_num(grille(tx, tz, M, [ds], None))
    # relief 30 m
    noms = []
    for la in range(math.floor(s), math.floor(n) + 1):
        for lo in range(math.floor(o), math.floor(e) + 1):
            nn = (f"Copernicus_DSM_COG_10_{'N' if la >= 0 else 'S'}{abs(la):02d}_00_"
                  f"{'E' if lo >= 0 else 'W'}{abs(lo):03d}_00_DEM")
            noms.append((DEM.format(n=nn), nn + ".tif"))
    dem = grille(tx, tz, M, ouvrir(noms, dem_dir), np.nan)
    eau = (part >= 0.34) & np.isfinite(dem) & (dem > 0.5)
    niveau = np.full((W, W), np.nan, np.float32)
    lab, nb = ndimage.label(eau, structure=np.ones((3, 3)))
    if not nb:
        niveau = np.full((W, W), np.nan, np.float32)
    if nb:
        # rivières : moyenne locale du relief sur l'eau (9 x 9), sans les valeurs aberrantes des berges
        d = np.where(eau, dem, 0.0)
        som = ndimage.uniform_filter(d, 9) * 81
        cnt = ndimage.uniform_filter(eau.astype(np.float32), 9) * 81
        niveau = np.where(eau, som / np.maximum(cnt, 1e-6), np.nan)
        # lacs : grandes nappes presque plates -> un seul niveau (médiane)
        lac = np.zeros_like(eau)
        for k in range(1, nb + 1):
            sel = lab == k
            v = dem[sel]
            if v.size >= 150 and np.percentile(v, 90) - np.percentile(v, 10) < 4.0:
                niveau[sel] = np.median(v)
                lac |= sel
        # cours d'eau élargis (~×2, 09/11/2026, demande de Maxime : des fleuves plus impressionnants) : de chaque côté,
        # 0,8 fois la demi-largeur locale + 1 bloc, au niveau de l'eau la plus proche ; les lacs gardent leur rivage
        riv = eau & ~lac
        if riv.any():
            hw = ndimage.distance_transform_edt(riv)
            hwmax = ndimage.maximum_filter(hw, size=31)
            dl, (jz, jx) = ndimage.distance_transform_edt(~riv, return_indices=True)
            ajout = (~eau) & (dl <= 0.8 * hwmax + 1.0) & np.isfinite(dem) & (dem > 0.5)
            niveau = np.where(ajout, niveau[jz, jx], niveau)
            eau = eau | ajout
    # ruisseaux (trop fins pour le satellite) : au fond des vallées, jamais au-dessus du sol réel
    if ruis is not None:
        rm, rn = ruis
        nouv = rm & ~eau & np.isfinite(dem) & (dem > 0.5) & np.isfinite(rn)
        niveau = np.where(nouv, np.minimum(rn, dem - 0.5), niveau)
        eau = eau | nouv
    dans = np.searchsorted(np.array([1, 2, 3, 4, 6, 9, 13, 16]),
                           np.minimum(np.round(ndimage.distance_transform_edt(eau)), 16), side="right") - 1
    dist, (iz, ix) = ndimage.distance_transform_edt(~eau, return_indices=True)
    proche = (~eau) & (dist <= 15) & eau.any()
    niv_proche = niveau[iz, ix]
    val = np.full((W, W), VIDE, np.int32)
    L = lambda a: np.clip(np.round((a + 500.0) / 3.0), 0, 2047).astype(np.int32)
    ok = eau & np.isfinite(niveau)
    val[ok] = L(niveau[ok]) * 16 + np.clip(dans[ok], 0, 7)
    ok2 = proche & np.isfinite(niv_proche)
    cl = np.searchsorted(np.array([1, 2, 3, 4, 6, 9, 12, 15]), np.round(dist[ok2]), side="right") - 1
    val[ok2] = L(niv_proche[ok2]) * 16 + 8 + np.clip(cl, 0, 7)
    return val[M:M + T, M:M + T], int(eau[M:M + T, M:M + T].sum())


# ---------------------------------------------------------------- paquets

def encoder(v):
    q = v.astype(np.int32)
    d = q.copy()
    d[:, 1:] = q[:, 1:] - q[:, :-1]
    d[q == VIDE] = VIDE
    d[:, 1:][(q[:, :-1] == VIDE) & (q[:, 1:] != VIDE)] = q[:, 1:][(q[:, :-1] == VIDE) & (q[:, 1:] != VIDE)]
    return zlib.compress(np.clip(d, -32768, 32767).astype("<i2").tobytes(), 9)


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


def main():
    zones = json.load(open(sys.argv[1]))
    noms = sys.argv[2].split(",")
    sortie = sys.argv[3]
    os.makedirs(sortie, exist_ok=True)
    wc = "/tmp/worldcover"
    os.makedirs(wc, exist_ok=True)
    regions = set()
    for nom in noms:
        x0, x1, z0, z1 = zones[nom]
        for tz in range(z0 >> 9, (z1 >> 9) + 1):
            for tx in range(x0 >> 9, (x1 >> 9) + 1):
                regions.add((tx, tz))
    paquets, paquets_eau, bilan = {}, {}, {}
    dem_dir = "/tmp/dem30"
    os.makedirs(dem_dir, exist_ok=True)
    ruis_paquets = {}
    journal = []
    for tx, tz in sorted(regions, key=lambda r: (r[0] >> 4, r[1] >> 4, r)):
        t0 = time.time()
        c = couverture(tx, tz, wc)
        r = roche(tx, tz, journal)
        v = np.where(c > 0, r.astype(np.int32) * 256 + c, VIDE)
        paquets.setdefault((tx >> 4, tz >> 4), {})[(tx & 15) + 16 * (tz & 15)] = encoder(v)
        cle = (tx >> 4, tz >> 4)
        if cle not in ruis_paquets:
            ruis_paquets.clear()
            ruis_paquets[cle] = ruisseaux_paquet(cle[0], cle[1], dem_dir)
        rm, rn = ruis_paquets[cle]
        lx, lz = (tx - cle[0] * N) * T, (tz - cle[1] * N) * T
        ve, ne = eau(tx, tz, wc, dem_dir, (rm[lz:lz + T + 2 * M, lx:lx + T + 2 * M], rn[lz:lz + T + 2 * M, lx:lx + T + 2 * M]))
        paquets_eau.setdefault((tx >> 4, tz >> 4), {})[(tx & 15) + 16 * (tz & 15)] = encoder(ve)
        print(f"  eau : {ne} blocs", flush=True)
        cc = Counter(c.ravel().tolist())
        rr = Counter(r.ravel().tolist())
        bilan[f"{tx},{tz}"] = {"couverture": {str(k): n for k, n in cc.most_common()},
                               "roche": {ROCHES[k]: n for k, n in rr.most_common()}}
        print(f"région {tx} {tz} : {time.time() - t0:.0f} s, couverture {cc.most_common(4)}, "
              f"roche {[(ROCHES[k], n) for k, n in rr.most_common(4)]}", flush=True)
    for (bx, bz), rs in paquets.items():
        open(os.path.join(sortie, f"sol30_{bx}_{bz}.bin"), "wb").write(paquet(bx, bz, rs))
    for (bx, bz), rs in paquets_eau.items():
        open(os.path.join(sortie, f"eau30_{bx}_{bz}.bin"), "wb").write(paquet(bx, bz, rs))
    json.dump({"bilan": bilan, "journal": journal, "unites": {str(k): ROCHES[v] for k, v in _unites.items()}},
              open(os.path.join(sortie, "bilan.json"), "w"), indent=1)
    print("\n".join(journal[:10]))


def paquet_complet(bx, bz, sortie, publier=False):
    """Un paquet entier (16 x 16 régions) : sol30_<bx>_<bz>.bin et eau30_<bx>_<bz>.bin, publiés si demandé."""
    import subprocess
    os.makedirs(sortie, exist_ok=True)
    wc, dem_dir = "/tmp/worldcover", "/tmp/dem30"
    os.makedirs(wc, exist_ok=True)
    os.makedirs(dem_dir, exist_ok=True)
    sol, eaux, journal = {}, {}, []
    t0 = time.time()
    rm, rn = ruisseaux_paquet(bx, bz, dem_dir)
    print(f"ruisseaux : {int(rm.sum())} blocs", flush=True)
    for k in range(N * N):
        tx, tz = bx * N + (k % N), bz * N + (k // N)
        c = couverture(tx, tz, wc)
        if not c.any():
            continue
        r = roche(tx, tz, journal)
        sol[k] = encoder(np.where(c > 0, r.astype(np.int32) * 256 + c, VIDE))
        lx, lz = (tx - bx * N) * T, (tz - bz * N) * T
        ve, ne = eau(tx, tz, wc, dem_dir, (rm[lz:lz + T + 2 * M, lx:lx + T + 2 * M], rn[lz:lz + T + 2 * M, lx:lx + T + 2 * M]))
        if ne or (ve != VIDE).any():
            eaux[k] = encoder(ve)
        print(f"région {tx} {tz} ({k + 1}/256) : {time.time() - t0:.0f} s, eau {ne}", flush=True)
    for nom, rs, tag in (("sol30", sol, f"s30-z{bz}"), ("eau30", eaux, f"e30-z{bz}")):
        if not rs:
            continue
        f = os.path.join(sortie, f"{nom}_{bx}_{bz}.bin")
        open(f, "wb").write(paquet(bx, bz, rs))
        print(f, os.path.getsize(f) // 1024, "Ko", flush=True)
        if publier:
            if subprocess.run(["gh", "release", "view", tag], capture_output=True).returncode != 0:
                subprocess.run(["gh", "release", "create", tag, "--title", f"{nom} 1:30, bande z{bz}", "--notes",
                                "ESA WorldCover 10 m 2021 (© ESA WorldCover project 2021, CC BY 4.0), Macrostrat (CC BY 4.0), "
                                "Copernicus DEM GLO-30 : sol et eau à 1 point par bloc (1:30) pour Domaine d'Aubin – Terre."])
            for essai in range(6):
                if subprocess.run(["gh", "release", "upload", tag, f, "--clobber"]).returncode == 0:
                    break
                time.sleep(20 * (essai + 1))


if __name__ == "__main__":
    if sys.argv[1] == "--paquet":
        paquet_complet(int(sys.argv[2]), int(sys.argv[3]), sys.argv[4], "--publier" in sys.argv)
    else:
        main()
