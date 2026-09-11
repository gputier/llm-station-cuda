#!/usr/bin/env python3
"""Controle mecanique du jeu d'epreuves, avant tout deploiement.

Ce controle ne juge pas si une reponse attendue est JUSTE : personne ne peut le
faire par programme sur un jeu inedit. Il verifie que chaque epreuve est
COHERENTE avec elle-meme, ce qui est deja ce qui a coute le plus cher jusqu'ici :
un motif qui ne reconnait pas sa propre reponse attendue a tue deux campagnes,
et une contrainte que sa propre phrase temoin ne satisfait pas rendrait une
epreuve impossible pour tout le monde.

Il reproduit la normalisation du correcteur PowerShell, faute de quoi il
validerait des epreuves que le correcteur refuse.
"""
import json
import re
import sys
import glob
import unicodedata
from collections import Counter

MODES = {"exact_norm", "numeric", "set", "impossible", "constraints"}
PREDICATS = {"word_count", "word_at", "no_repeat", "max_word_len", "forbidden_chars"}


def normalise(s):
    """Meme normalisation que la fonction Normalise du correcteur."""
    s = str(s)
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    s = re.sub(r"\s+", " ", s).strip()
    s = s.rstrip(".!?;:,")
    return s.lower()


def verifier(chemin):
    erreurs, avertis, vus = [], [], []
    for numero, ligne in enumerate(open(chemin, encoding="utf-8"), 1):
        if not ligne.strip():
            continue
        ou = f"{chemin}:{numero}"
        try:
            e = json.loads(ligne)
        except json.JSONDecodeError as exc:
            erreurs.append(f"{ou} JSON illisible : {exc}")
            continue

        for champ in ("id", "family", "q", "answer", "check", "max_tokens"):
            if champ not in e:
                erreurs.append(f"{ou} champ absent : {champ}")
        if "id" not in e or "check" not in e:
            continue
        ident = e["id"]
        vus.append(ident)

        if e.get("max_tokens") != 3000:
            erreurs.append(f"{ident} max_tokens vaut {e.get('max_tokens')} au lieu de 3000")
        if "Answer:" not in e.get("q", ""):
            erreurs.append(f"{ident} l enonce n impose pas de ligne Answer:")

        check = e["check"]
        mode = check.get("type")
        if mode not in MODES:
            erreurs.append(f"{ident} mode de correction inconnu : {mode}")
            continue

        rep = e["answer"]

        # La reponse attendue est ce qui SUIT "Answer:", jamais la ligne entiere.
        # Un auteur qui recopie le prefixe rend l epreuve infaisable pour tout le
        # monde, et le controle a sec du correcteur ne peut pas le voir puisqu il
        # ajoute lui-meme ce prefixe avant de corriger. Trouve sur 24 epreuves
        # d un coup le 11/09/2026.
        for x in (rep if isinstance(rep, list) else [rep]):
            if str(x).strip().lower().startswith("answer"):
                erreurs.append(f"{ident} la reponse attendue contient le prefixe Answer: {x!r}")

        if mode == "numeric":
            motif = check.get("pattern")
            if motif:
                # Le motif est applique aux DEUX cotes par le correcteur. S il ne
                # reconnait pas la reponse attendue, l epreuve est morte.
                m = re.search(motif, str(rep))
                if not m:
                    erreurs.append(f"{ident} le motif ne reconnait pas sa propre reponse attendue : {rep!r}")
                else:
                    groupes = [g for g in m.groups()] or [m.group(0)]
                    for g in groupes:
                        if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", str(g).strip()):
                            erreurs.append(f"{ident} le motif capture une valeur non numerique : {g!r}")
            else:
                if not re.fullmatch(r"-?\d+(?:[.,]\d+)?", str(rep).strip()):
                    erreurs.append(f"{ident} numeric sans motif mais la reponse n est pas un nombre seul : {rep!r}")

        elif mode == "set":
            if not isinstance(rep, list):
                erreurs.append(f"{ident} mode set mais la reponse attendue n est pas une liste")

        elif mode == "impossible":
            if normalise(rep).find("impossible") < 0:
                erreurs.append(f"{ident} mode impossible mais la reponse attendue ne dit pas IMPOSSIBLE")
            if not check.get("require_elements"):
                avertis.append(f"{ident} mode impossible sans require_elements, le mot seul suffira")

        elif mode == "constraints":
            if not isinstance(rep, str):
                erreurs.append(f"{ident} mode constraints mais la reponse temoin n est pas une chaine")
                continue
            mots = [m for m in re.split(r"\s+", rep) if m]
            for r in check.get("rules", []):
                p = r.get("pred")
                if p not in PREDICATS:
                    erreurs.append(f"{ident} predicat inconnu : {p}")
                    continue
                if p == "word_count" and len(mots) != int(r["value"]):
                    erreurs.append(f"{ident} temoin a {len(mots)} mots, la regle en exige {r['value']}")
                if p == "word_at":
                    i = int(r["index"]) - 1
                    if i < 0 or i >= len(mots):
                        erreurs.append(f"{ident} position {r['index']} hors du temoin de {len(mots)} mots")
                    elif normalise(mots[i]) != normalise(r["value"]):
                        erreurs.append(f"{ident} mot {r['index']} du temoin est {mots[i]!r}, la regle exige {r['value']!r}")
                if p == "no_repeat":
                    n = [normalise(m) for m in mots]
                    doubles = [w for w, c in Counter(n).items() if c > 1]
                    if doubles:
                        erreurs.append(f"{ident} temoin repete des mots malgre no_repeat : {doubles}")
                if p == "max_word_len":
                    trop = [m for m in mots if len(m) > int(r["value"])]
                    if trop:
                        erreurs.append(f"{ident} temoin a des mots plus longs que {r['value']} : {trop}")
                if p == "forbidden_chars":
                    presents = sorted({c for c in str(r["value"]) if c in rep})
                    if presents:
                        erreurs.append(f"{ident} temoin contient des caracteres interdits : {presents}")
                    # L ecart entre l enonce et le predicat est le piege propre a
                    # cette famille : interdire "e" n interdit pas "e accentue".
                    for c in str(r["value"]):
                        if c in "eaiouEAIOU":
                            base = c.lower()
                            variantes = {"e": "éèêë", "a": "àâä", "i": "îï", "o": "ôö", "u": "ùûü"}[base]
                            manquantes = [v for v in variantes if v not in str(r["value"])]
                            if manquantes:
                                avertis.append(
                                    f"{ident} interdit {c!r} sans ses variantes accentuees {''.join(manquantes)!r}, "
                                    "l enonce et le predicat risquent de diverger")
    return erreurs, avertis, vus


def main():
    motifs = sys.argv[1:] or ["f1-*.jsonl", "f2-*.jsonl", "f3-*.jsonl", "f4-*.jsonl",
                              "f5-*.jsonl", "f6-*.jsonl", "f7-*.jsonl"]
    fichiers = sorted({f for m in motifs for f in glob.glob(m)})
    if not fichiers:
        print("aucun fichier d epreuves trouve")
        return 1
    tous_ids, total_erreurs, total_avertis = [], [], []
    for f in fichiers:
        err, avr, ids = verifier(f)
        print(f"{f:28} {len(ids):3} epreuves, {len(err)} erreurs, {len(avr)} avertissements")
        total_erreurs += err
        total_avertis += avr
        tous_ids += ids

    doubles = [i for i, c in Counter(tous_ids).items() if c > 1]
    if doubles:
        total_erreurs.append(f"identifiants en double sur l ensemble du jeu : {doubles}")

    print(f"\ntotal : {len(tous_ids)} epreuves")
    if total_avertis:
        print(f"\nAVERTISSEMENTS ({len(total_avertis)})")
        for a in total_avertis:
            print("  " + a)
    if total_erreurs:
        print(f"\nERREURS ({len(total_erreurs)})")
        for e in total_erreurs:
            print("  " + e)
        return 1
    print("\naucune erreur")
    return 0


if __name__ == "__main__":
    sys.exit(main())
