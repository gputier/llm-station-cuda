# Campagne du banc LLM, septembre 2026

Depuis le 27/09/2026, les chemins bench/ de ce document vivent dans le dépôt privé bench-llm.

Relevés datés, le plus récent en tête. Le classement est la moyenne simple des taux de réussite des épreuves terminées, vitesse exclue. Une configuration ne se compare aux autres qu'une fois toutes ses épreuves finies.

## 25/09/2026, quatre niveaux et un correctif de placement

Le banc complet (`python -m benchrun run`) reste la campagne de référence, lancée de temps en temps sur un modèle ; trois niveaux plus courts servent au suivi courant, tous les trois via `python -m benchrun bench --preset mini|medium|large --machine <machine> --out <dossier>` (`--models` et `--seed` en options). Le niveau mini tire une seule question par épreuve, la même pour les trois profils R1, R2 et R3 d'un modèle : il écrit `seed.txt` avant de lancer et le relit à la reprise, pour qu'un profil rejoué tire la même question que les deux autres. Chaque réponse est plafonnée à 16 384 jetons pour LiveCodeBench, LiveBench et Aider, à 2 048 pour les autres épreuves, et le niveau écrit `rapport.md` à la fin.

Règle de placement retenue ce jour-là : un modèle tourne entièrement sur sa carte à la fenêtre de 262 144 jetons, sans déborder sur la mémoire partagée, ou il change de machine ou de quantization. `occamy`, `katapex` et `qwen36apex` débordaient de la carte de 16 Go à cette fenêtre (environ 30 jetons/s au lieu de 130) : leur version complète tient sur la carte de 32 Go avec les mêmes réglages, et la carte de 16 Go sert désormais une version allégée (NanoPlus pour `occamy` et `qwen36apex`, APEX-dynamic-v2 de Myric pour `katapex`). Le gabarit intégré de ces trois modèles levait aussi une erreur sur un message système arrivé après le début de la conversation, ce que fait Claude Code : chaque profil charge maintenant ce même gabarit avec une seule ligne remplacée.

`bench/scripts/deploy-profiles.sh` refuse désormais de déployer sur une station en plein banc (un conteneur `runner-<machine>` en cours, ou une instance nommée `bench-*` déjà servie). Le détail complet vit dans `bench/harness/README.md`.

## 24/09/2026, arrêt

Campagne arrêtée le soir même : à environ 33 heures par configuration et par passage, elle aurait occupé la 99 pendant six mois. Aucune épreuve n'était terminée, aucun résultat n'est gardé. Le format devient le FullBench, lancé de temps en temps sur un modèle ; les niveaux Mini, Medium et Large servent au suivi courant.

## 24/09/2026, lancement

La 99 commence par bonsai R1, la 97 par bonsai2 R1. Aucune épreuve terminée.
