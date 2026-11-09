# Domaine d'Aubin – Terre : relief fin

Données de relief téléchargées par le mod Minecraft **Domaine d'Aubin – Terre** pendant la génération du monde : le vrai relief de la Terre à un point par bloc (échelle 1:250, 250 m).

- Fichiers : releases `v1-z<bz>`, un fichier `relief_<bx>_<bz>.bin` par paquet de 4 × 4 régions de 512 blocs (format décrit dans `relief_fin.py`).
- Préparation : workflow « Relief du monde » (`relief_bande.py`), à partir de Copernicus DEM GLO-90.
- Échelle 1:30 (09/11/2026) : releases `v30-z<bz>`, un fichier `relief30_<bx>_<bz>.bin` par paquet de 16 × 16 régions de 512 blocs (format v2, décrit dans `relief30.py`), à partir de Copernicus DEM GLO-30 (workflow « Relief du monde 1:30 », bandes dans `bandes30.json`). Hors ligne : `<dossier du jeu>/domaine_terre/relief30/`.
- Hors ligne : poser les fichiers `.bin` dans `<dossier du jeu>/domaine_terre/relief/`.

## Source et licence

Copernicus DEM GLO-90 et GLO-30 — © DLR e.V. 2010-2014 et © Airbus Defence and Space GmbH 2014-2018, fourni dans le cadre du programme Copernicus de l'Union européenne. Données produites à partir de Copernicus DEM, utilisées selon sa licence (libre, avec mention de la source).
