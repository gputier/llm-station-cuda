#!/usr/bin/env python3
"""Confronte les reponses d'un solveur aveugle aux reponses attendues du jeu.

Le solveur n'a vu que les enonces. Un desaccord designe donc l'une de trois
choses, et aucune ne se devine depuis ce script : la reponse attendue est
fausse, le solveur s'est trompe, ou l'enonce est ambigu. Ce programme ne
tranche pas, il fabrique la file de relecture humaine et la range par ordre
d'urgence.

Ce qu'il ne fait PAS, deliberement : corriger quoi que ce soit. Une reponse
attendue ne se modifie que par decision humaine, parce qu'un jeu inedit n'a
aucune source exterieure capable de contredire une correction automatique.
"""
import json
import re
import sys
import unicodedata
from pathlib import Path

SC = Path(__file__).parent


def normalise(s):
    """Meme normalisation que le correcteur PowerShell, pour que la comparaison
    ici et la notation la-bas ne divergent jamais."""
    if s is None:
        return ""
    t = unicodedata.normalize("NFD", str(s).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t).strip()
    t = re.sub(r"[\.,;:!\?]+$", "", t)
    return t


def nombres(s):
    """Les nombres d'une chaine, separateurs de milliers retires d'abord."""
    t = re.sub(r"(\d)[\s  \.](\d{3})(?!\d)", r"\1\2", str(s))
    return [x.replace(",", ".") for x in re.findall(r"-?\d+(?:[\.,]\d+)?", t)]


def concorde(attendu, rendu):
    """Vrai si les deux disent la meme chose. Tolerant sur la forme, jamais sur
    le fond : on compare des nombres a des nombres et du texte normalise a du
    texte normalise."""
    a, r = normalise(attendu), normalise(rendu)
    if a == r:
        return True
    # Une reponse attendue impossible concorde avec un solveur qui a conclu a
    # l'impossibilite, quels que soient les mots qu'il a employes autour.
    if "impossible" in a:
        return "impossible" in r
    if "impossible" in r:
        return False
    na, nr = nombres(a), nombres(r)
    if na and nr:
        if len(na) > len(nr):
            return False
        try:
            # Les nombres attendus doivent apparaitre dans l'ordre au debut de
            # ce qu'a rendu le solveur, qui peut ajouter des unites ou du texte.
            return all(abs(float(x) - float(y)) < 1e-9 for x, y in zip(na, nr))
        except ValueError:
            return False
    return a in r or r in a


def lire_solutions(chemin):
    """Decoupe le rendu d'un solveur en blocs par identifiant.

    Deux formats sont acceptes, parce que deux vagues de solveurs ont ete
    mandatees differemment et qu'il vaut mieux lire les deux que de redemander
    un travail deja fait. Le format en blocs, "### id" puis REPONSE / CONFIANCE
    / SIGNALEMENT, et le format en ligne, "id | reponse | justification | sur".
    """
    if not chemin.exists():
        return {}
    blocs, courant = {}, None
    for ligne in chemin.read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*([a-z]\d-[a-z0-9-]*\d{3})\s*\|(.*)$", ligne, re.I)
        if m:
            champs = [c.strip() for c in m.group(2).split("|")]
            conf = champs[2] if len(champs) > 2 else None
            blocs[m.group(1)] = {
                "reponse": champs[0] if champs else None,
                "confiance": conf,
                # Un solveur qui se dit douteux demande la meme relecture qu un
                # signalement explicite : c est le meme aveu.
                "signalement": ("doute du solveur" if conf and "douteux" in conf.lower() else None),
            }
            courant = None
            continue
        m = re.match(r"^###\s+(\S+)", ligne)
        if m:
            courant = m.group(1)
            blocs[courant] = {"reponse": None, "confiance": None, "signalement": None}
            continue
        if courant is None:
            continue
        for cle, motif in (
            ("reponse", r"^\s*REPONSE\s*:\s*(.+)$"),
            ("confiance", r"^\s*CONFIANCE\s*:\s*(.+)$"),
            ("signalement", r"^\s*SIGNALEMENT\s*:\s*(.+)$"),
        ):
            m = re.match(motif, ligne, re.I)
            if m and blocs[courant][cle] is None:
                blocs[courant][cle] = m.group(1).strip()
    return blocs


def main():
    attendues = {}
    # F5 est exclue : ses epreuves n ont pas UNE reponse, toute phrase
    # satisfaisant les contraintes est juste. Elle se verifie par verifier-f5.py,
    # qui fait passer la phrase du solveur au correcteur.
    for f in sorted(SC.glob("f[12346]-*.jsonl")) + sorted(SC.glob("f7-*.jsonl")):
        for ligne in f.read_text(encoding="utf-8").splitlines():
            if ligne.strip():
                e = json.loads(ligne)
                attendues[e["id"]] = e

    solutions = {}
    for f in sorted((SC / "aveugle").glob("*-solutions.md")):
        solutions.update(lire_solutions(f))

    if not solutions:
        print("aucun rendu de solveur trouve, rien a comparer")
        return 1

    accord, desaccord, signales, absents = [], [], [], []
    for eid, e in sorted(attendues.items()):
        s = solutions.get(eid)
        if s is None:
            absents.append(eid)
            continue
        sig = (s["signalement"] or "aucun").strip()
        if sig.lower() not in ("aucun", "aucune", "none", ""):
            signales.append((eid, sig, s["reponse"]))
        if concorde(e["answer"], s["reponse"]):
            accord.append(eid)
        else:
            desaccord.append((eid, e["answer"], s["reponse"], s["confiance"] or "?"))

    total = len(accord) + len(desaccord)
    print(f"epreuves confrontees : {total} sur {len(attendues)}")
    print(f"  accord     : {len(accord)}")
    print(f"  desaccord  : {len(desaccord)}   <- file de relecture humaine")
    print(f"  signalees  : {len(signales)}")
    if absents:
        print(f"  non resolues : {len(absents)} ({', '.join(absents[:8])}...)")

    if desaccord:
        print("\nDESACCORDS, a trancher a la main, jamais automatiquement :")
        for eid, att, rendu, conf in desaccord:
            fam = attendues[eid].get("family", "?")
            print(f"  {eid:<28} [{fam}] confiance={conf}")
            print(f"      attendu  : {att}")
            print(f"      solveur  : {rendu}")

    if signales:
        print("\nSIGNALEMENTS du solveur, meme quand la reponse concorde :")
        for eid, sig, rendu in signales:
            print(f"  {eid:<28} {sig[:110]}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
