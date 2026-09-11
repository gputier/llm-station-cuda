#!/usr/bin/env python3
"""Passe les phrases d'un solveur aveugle au correcteur de la famille F5.

F5 ne se verifie pas comme les autres. Ses epreuves n'ont pas UNE reponse :
toute phrase satisfaisant les contraintes est juste. Confronter la phrase du
solveur a celle de l'auteur ne dirait donc rien.

Ce qui se verifie ici est le seul defaut que cette famille peut porter, et
qu'aucune relecture de code ne revele : l'ecart entre ce que l'ENONCE demande et
ce que la REGLE verifie. Le solveur n'a lu que l'enonce. Si sa phrase echoue aux
regles, l'epreuve ment a qui la lit, et elle est infaisable pour le modele
mesure comme elle l'a ete pour le solveur.

Un echec ici accuse donc l'epreuve avant d'accuser le solveur, et c'est
l'inverse de la lecture habituelle.
"""
import json
import re
import sys
import glob
import unicodedata
from collections import Counter
from pathlib import Path

SC = Path(__file__).parent


def normalise(s):
    t = unicodedata.normalize("NFD", str(s).lower())
    t = "".join(c for c in t if unicodedata.category(c) != "Mn")
    t = re.sub(r"\s+", " ", t).strip()
    return t.rstrip(".!?;:,")


def evaluer(phrase, regles):
    """Rend la liste des regles violees, vide si la phrase passe."""
    mots = [m for m in re.split(r"\s+", phrase) if m]
    echecs = []
    for r in regles:
        p = r.get("pred")
        if p == "word_count" and len(mots) != int(r["value"]):
            echecs.append(f"{len(mots)} mots au lieu de {r['value']}")
        elif p == "word_at":
            i = int(r["index"]) - 1
            if i < 0 or i >= len(mots):
                echecs.append(f"pas de mot en position {r['index']}")
            elif normalise(mots[i]) != normalise(r["value"]):
                echecs.append(f"mot {r['index']} = {mots[i]!r} au lieu de {r['value']!r}")
        elif p == "no_repeat":
            d = [w for w, c in Counter(normalise(m) for m in mots).items() if c > 1]
            if d:
                echecs.append(f"mots repetes {d}")
        elif p == "max_word_len":
            trop = [m for m in mots if len(m) > int(r["value"])]
            if trop:
                echecs.append(f"mots trop longs {trop}")
        elif p == "forbidden_chars":
            pres = sorted({c for c in str(r["value"]) if c in phrase})
            if pres:
                echecs.append(f"caracteres interdits {pres}")
    return echecs


def lire_phrases(chemin):
    """Format attendu : "identifiant | phrase | verification"."""
    out = {}
    for ligne in Path(chemin).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^\s*(f5-[a-z]+-\d{3})\s*\|(.*)$", ligne, re.I)
        if m:
            champs = [c.strip() for c in m.group(2).split("|")]
            if champs and champs[0]:
                out[m.group(1)] = champs[0]
    return out


def main():
    rendus = sys.argv[1:] or sorted(glob.glob(str(SC / "aveugle" / "f5*solutions.md")))
    phrases = {}
    for f in rendus:
        phrases.update(lire_phrases(f))
    if not phrases:
        print("aucune phrase de solveur trouvee")
        return 1

    epreuves = {}
    for f in sorted(SC.glob("f5-*.jsonl")):
        for ligne in f.read_text(encoding="utf-8").splitlines():
            if ligne.strip():
                e = json.loads(ligne)
                epreuves[e["id"]] = e

    ok, casse, absentes = [], [], []
    for eid, e in sorted(epreuves.items()):
        if eid not in phrases:
            absentes.append(eid)
            continue
        echecs = evaluer(phrases[eid], e["check"]["rules"])
        (ok if not echecs else casse).append((eid, phrases[eid], echecs))

    print(f"epreuves F5 eprouvees : {len(ok) + len(casse)} sur {len(epreuves)}")
    print(f"  phrase du solveur acceptee : {len(ok)}")
    print(f"  phrase du solveur refusee  : {len(casse)}   <- l enonce et la regle divergent")
    if absentes:
        print(f"  sans phrase : {len(absentes)} ({', '.join(absentes[:6])}...)")
    if casse:
        print("\nEPREUVES A REVOIR, l enonce ne dit pas ce que la regle verifie :")
        for eid, ph, ech in casse:
            print(f"  {eid}")
            print(f"      phrase : {ph}")
            print(f"      echecs : {'; '.join(ech)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
