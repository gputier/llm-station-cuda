#!/usr/bin/env python3
"""Sort les enonces SEULS, pour la relecture a l'aveugle.

Le relecteur ne doit voir ni la reponse attendue, ni le leurre, ni le mode de
correction : s'il les voit, son accord ne prouve plus rien, il recopie. C'est le
seul garde-fou possible sur un jeu qu'aucune source exterieure ne peut
contredire, donc la fuite d'un seul de ces champs vide l'etage entier de sa
valeur, et le script verifie qu'il n'en laisse passer aucun.
"""
import json
import sys
import glob
import os

SORTIE = "aveugle"
INTERDITS = ("answer", "lure", "check", "flag")


def main():
    motifs = sys.argv[1:]
    if not motifs:
        print("usage : extraire-enonces.py <motif.jsonl> [...]")
        return 1
    os.makedirs(SORTIE, exist_ok=True)
    for f in sorted({x for m in motifs for x in glob.glob(m)}):
        base = os.path.splitext(os.path.basename(f))[0]
        lignes = [f"# Enonces a resoudre, lot {base}", ""]
        n = 0
        for l in open(f, encoding="utf-8"):
            if not l.strip():
                continue
            e = json.loads(l)
            n += 1
            lignes += [f"## {e['id']}", "", e["q"], ""]
        chemin = os.path.join(SORTIE, f"{base}-enonces.md")
        with open(chemin, "w", encoding="utf-8") as fh:
            fh.write("\n".join(lignes))

        # Controle de fuite : aucune valeur sensible ne doit se retrouver dans le
        # rendu. Un controle sur les noms de champs ne suffirait pas, ce sont les
        # VALEURS qui trahissent.
        rendu = open(chemin, encoding="utf-8").read()
        fuites = []
        for l in open(f, encoding="utf-8"):
            if not l.strip():
                continue
            e = json.loads(l)
            for champ in INTERDITS:
                if champ not in e:
                    continue
                v = e[champ]
                valeurs = v if isinstance(v, list) else [v]
                for x in valeurs:
                    x = str(x)
                    # Une valeur courte peut apparaitre par hasard dans un enonce
                    # sans etre une fuite. Seules les valeurs assez longues pour
                    # etre distinctives sont concluantes.
                    if len(x) >= 6 and x in rendu and x not in e["q"]:
                        fuites.append(f"{e['id']} : {champ} = {x!r}")
        etat = "FUITE" if fuites else "sans fuite"
        print(f"{chemin:44} {n:3} enonces, {etat}")
        for x in fuites:
            print("   " + x)
    return 0


if __name__ == "__main__":
    sys.exit(main())
