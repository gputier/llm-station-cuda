# Étude : rendre du débit au profil `qwen` du .99

Date : 06/09/2026. Périmètre : llama-server natif Windows sur PC-GUILLAUME (RTX 5090 32 Go), moteur inchangé. Fenêtre 262 144 minimum. Runner CI, WSL2 et .98 non touchés. Aucune modification faite pendant l'étude : lecture du dépôt llama.cpp, de Hugging Face, du web (quatre agents), et relevé en lecture seule sur la machine.

Niveau de certitude en tête de chaque piste : sûr (mesuré ici), probable (source datée et chiffrée), spéculatif (annoncé sans mesure comparable).

## 1. Ce que dit la machine aujourd'hui, relevé du 06/09

Serveur en place : build b479 (27/08), `Qwen3.8-27B-NVFP4-MTP-LOW.gguf`, fenêtre 393 216, un slot, VRAM 30,8 Go occupés au repos, 4,0 Go de mémoire partagée hôte, P1 à 2 400 MHz.

Journal depuis le dernier redémarrage, trois tâches seulement. La première est la seule longue, et elle montre où part le temps sur un vrai premier tour :

- prompt de 41 528 jetons : 3 270 tok/s jusqu'à 16 384 jetons, puis un trou de 8,5 s entre 16 384 et 20 233 jetons, puis 2 100 tok/s. Total prefill 19,7 s ;
- génération de 490 jetons : les 103 premiers sortent à 7,96 tok/s (12,9 s), les suivants à 99 tok/s. Total 16,8 s, soit 29 tok/s de moyenne ;
- acceptation MTP 36 %, longueur moyenne 2,46.

Sur un tour de 36 s, environ 21 s ne sont ni du prefill ni du décodage en régime. C'est un premier tour après démarrage du processus (capture des graphes CUDA, allocations, page-in), donc pas forcément représentatif, mais la session du 28/08 avait une médiane de 137,7 tok/s sur 351 requêtes, sans ce trou. Deux candidats plausibles, non prouvés : les points de reprise de l'état récurrent que le serveur écrit en RAM hôte (`--ctx-checkpoints`, défaut 32, tous les 8 192 jetons, plus un point de reprise spéculatif avant de générer), et le chemin de sauvegarde par cellule que llama.cpp a optimisé le 31/08 (PR 27991, « 25 à 63 s pour restaurer 40k jetons », postérieur à notre build). Une mesure tranche en dix minutes, voir plan.

Point d'attention pour le ressenti : dans nos bancs, le débit est mesuré en régime établi. Le ressenti d'une session, lui, est fait de premiers tours, de prefill et de longueur de réflexion. Les trois pistes les mieux placées ci-dessous visent ça, pas le tok/s brut.

## 2. Déjà fermé ici, ne pas rejouer

Chaque point a sa mesure dans `docs/tuning-log.md` ou `infra-llm-local` : build plus récent à modèle constant (27/08, b10643 et b10740 neutres), `GGML_CUDA_FA_ALL_QUANTS` (27/08, identique), retrait du mmproj (bruit), n-grammes (2x plus lent en réel), DFlash2 z-lab à profondeur 4 (01/09, -5 % et -33 % de prefill), `iq4_nl` (150x plus lent en prefill), `--reasoning-preserve` (aucun effet), `--cache-reuse` (désactivé en silence sur une archi hybride), palier MEDIUM (dominé), balayage `n-max` à contexte vide et plein (31/08, 4 retenu), cache KV q8_0 (18/08, la carte étrangle au delà de 29 Go et ne tient pas 393k), fenêtre sous 262k (tranché le 29/08).

Deux affirmations des agents contredites par nos mesures, à ne pas reprendre : « `GGML_CUDA_FORCE_CUBLAS=ON` coûte 5 fois le prefill » (le binaire officiel b10740, compilé sans ce drapeau, a rendu 4 260 tok/s contre 4 264 le 01/09 : neutre sur ce modèle) ; « passer le cache KV en q8_0 » (mesuré, effondrement).

## 3. Pistes retenues, par gain attendu sur le ressenti

### P1. Attribuer les 21 s du premier tour. Sûr que le trou existe, cause spéculative

Coût : dix minutes, aucune modification du profil. Lancer une instance sonde sur un autre port avec `-lv 4` et le profil exact, rejouer un prompt de 40k, lire les lignes `created context checkpoint`, `created speculative checkpoint`, tailles et horodatages. Puis rejouer le même prompt sur le binaire officiel b10826 (voir P3) pour voir si le trou disparaît. Si les points de reprise sont en cause, deux réglages existent en ligne de commande : `-cms` pour espacer, `-ctxcp` pour plafonner. Rien à décider avant la mesure.

### P2. Borner la réflexion côté serveur. Probable, gain potentiellement le plus gros sur le ressenti

Qwen3.8 réfléchit à `xhigh` par défaut : 22 276 jetons de réflexion pour un cercle SVG, mesuré le 18/08. Sous Claude Code, rien dans nos lanceurs ne borne cet effort ; ce que Claude Code envoie comme budget de réflexion sur `/v1/messages` n'a jamais été relevé. Deux réglages existent dans notre build et n'ont jamais été essayés : `--reasoning-budget N` (plafond de jetons de réflexion, côté serveur) et `--chat-template-kwargs '{"reasoning_effort":"medium"}'` (effort par défaut). Côté client, `MAX_THINKING_TOKENS` (doc officielle Claude Code, vérifiée) fixe le budget envoyé.

Méthode : d'abord compter, sur une session réelle, le ratio réflexion sur réponse par tour (le serveur le journalise à `-lv 4`). Ensuite seulement mesurer `medium` contre le défaut sur le jeu GSM8K 60 problèmes et sur trois tâches Claude Code réelles. C'est un arbitrage qualité contre latence qui t'appartient, avec chiffres en main.

### P3. Passer sur le binaire officiel b10826. Probable, gain petit à moyen, risque faible

Ce que la fenêtre b10643 à b10826 apporte à ce profil : optimisation de la restauration des cellules KV non contiguës (PR 27991, 31/08, directement lié à P1 et au cache hôte `-cram`), tuiles flash attention à remapping XOR (PR 25635, 31/08, +7 % de prefill et +2,6 % de décodage mesurés par un tiers sur Qwen3.8-27B à 32k, cache q4_0 non couvert avec certitude), correctifs DFlash NVFP4 (PR 28000), support DSpark (voir P6). Le binaire officiel CUDA 13.3 est déjà prouvé neutre à b10740 sur ce modèle, donc pas de recompilation.

Deux changements de comportement à vérifier après bascule : `preserve_reasoning` est activé par défaut depuis b10763 (02/09), à mesurer sur la taille du prompt au tour 3 comme le 31/08 ; et le message `-lv` qui annonce un port par défaut futur, sans effet.

### P4. `GGML_CUDA_GRAPH_OPT=1`. Probable, +3 à +9 % de décodage, coût nul

Variable d'environnement, hors de tout profil, déjà dans notre build (PR 16991, fusionnée 30/11/2025) : exécute les branches Q, K, V en flux CUDA concurrents. Mesuré par l'auteur sur RTX 5090 : +5 % sur un dense 8B Q8_0, +9 % sur un MoE 30B-A3B. Sur Qwen3.8, seules les 16 couches d'attention pleine en profitent ; la PR 21897 (ouverte, 14/04) l'étend aux couches linéaires avec +4 % annoncés sur qwen35 27B. Se pose sur le processus PowerShell avant `Invoke-CimMethod`, comme `PATH`. À mesurer à contexte vide et plein, trois graines.

### P5. Balayer `--spec-draft-p-min` sur Qwen. Spéculatif, coût nul

Jamais balayé sur ce modèle (seulement sur Tiel le 03/09, où monter le seuil faisait chuter le débit). La discussion officielle Qwen (HF, fin août) recommande 0,69 avec `n-max` 2 à 4 sur llama.cpp, et un dépôt communautaire annonce qu'un seuil de 0,60 à 0,75 « rend la spéculation profonde presque gratuite ». Trois valeurs (0, 0,5, 0,7), deux longueurs de contexte, trois graines. Le critère est le débit, jamais l'acceptation.

### P6. Rejouer DFlash2 en profondeur. Probable pour la direction, incertain pour l'amplitude

Notre rejet du 01/09 a été fait à profondeur 4, la profondeur du MTP. Or DFlash2 est conçu pour des blocs de 8 à 16 : deux sources indépendantes, RTX PRO 6000 (même bande passante que la 5090), le mesurent au dessus du MTP natif (140,6 contre 114,7 tok/s sur Reddit ; x2,26 contre sans spéculation chez prismix.dev, 22/08). Le fichier z-lab de 1,9 Go est déjà sur le disque, le build b10826 porte les correctifs NVFP4 du 30/08. Balayage `n-max` 8, 12, 16 avec `p-min` 0 et 0,5, à contexte vide et plein, sur b10826 uniquement.

À côté, DSpark : drafter RadixArk 1,86 milliard de paramètres, supporté en amont (`--spec-type draft-dspark`, PR 25173, 28/07), GGUF Q8_0 de 1,4 Go disponible (erlidev, magnitudedev). Son auteur n'a obtenu aucun gain et le signale « gourmand en mémoire » ; un banc tiers sur un MoE 35B donne +38 % en amont contre sans spéculation, donc sous le MTP. Il coûte 1,4 Go de VRAM que le profil à 393k n'a pas. À écarter tant que P6 n'a pas parlé.

### P7. Changer de modèle. Sûr pour Tiel, à mesurer pour les deux autres

Le plus gros gain mesuré reste sur le disque : Tiel-Coder-35B-A3B, +54 % de génération, prefill doublé, MMLU égal, GSM8K 58 contre 52, fenêtre 393k prouvée par rappel (01/09). Tu l'as essayé le 02/09 (352k jetons de session à 89,5 tok/s soutenus) puis tu es revenu sur Qwen : si c'est une raison de qualité vécue, elle prime sur le banc et il faut la nommer, sinon Tiel est la réponse à ta question.

Deux candidats neufs que les agents n'avaient pas et que le web du jour fait ressortir :

- NVIDIA Nemotron 3.5 Lightning 30B-A3B (sorti le 11/08/2026) : MoE hybride Mamba-2 + attention, 30B total, 3B actifs, 1M de contexte validé, tête MTP native, drafters DSpark et DFlash officiels, licence OpenMDW. Architecture `nemotron_h_moe` déjà dans notre build. GGUF officiels ggml-org (Q4_0 17,6 Gio + drafter MTP séparé), NVFP4 officiel NVIDIA converti en GGUF (18,4 Gio, testé par son auteur sur 32 Go de VRAM à 1M de contexte avec cache q8_0), tiers avec MTP intégré (gbuzhf, 18 à 22 Gio). Qualité annoncée par NVIDIA : MMLU-Pro 81,9, SWE-bench Verified 51,6, GPQA 75,4. Son intérêt propre : les couches Mamba ne paient pas le cache KV, donc la fenêtre longue coûte peu et le prefill devrait tenir à haut contexte, là où Qwen tombe à 800 tok/s à 233k. Fiabilité tool-calling et qualité code à prouver sur notre banc.
- Qwen3.6-35B-A3B en NVFP4 avec MTP (michaelw9999, 393 000 téléchargements) : même classe que Tiel, corps NVFP4 de 18,6 Gio, 271 tok/s annoncés en tg128 sur Blackwell, GSM8K 98 % sur 103 échantillons. Base Qwen3.6, dont Ornith 1.5 est une amélioration agentique : moins bon que Tiel sur les bancs agentiques publiés par Ornith, plus rapide en octets relus. À mesurer seulement si Tiel est retenu comme direction.

Ornith 1.5 35B-A3B existe aussi en NVFP4 GGUF (TizzyT566, 22,2 Gio, sans MTP, testé par l'auteur sur 5090 à 512k) : sans tête MTP il ne battra pas Tiel. La version avec MTP (tiyuvta) exige un fork de llama.cpp : écartée.

### P8. Paliers NVFP4 plus légers. Spéculatif, gain de 2 à 4 % au mieux

COMPACT-LOW (14,12 Gio, tête Q4_K) et VERY-LOW (13,84 Gio, tête Q3_K) du même dépôt esatapedico. Seuls la tête de sortie, les embeddings et la tête MTP changent, moins de 700 Mo relus par jeton. Le gain de décodage est borné par ce ratio, la qualité de la tête est ce qui fait l'acceptation MTP. Dernier de la liste, à ne jouer que si tout le reste est fait.

### P9. Pilote 616.64 du 03/09. Spéculatif, aucune preuve dans un sens ni l'autre

Notes de version inaccessibles automatiquement. À faire un jour de maintenance, avec mesure avant et après, jamais pendant la campagne.

## 4. Écarté, avec le motif

- Passer `GGML_CUDA_FORCE_CUBLAS` à OFF : neutre, prouvé le 01/09 avec le binaire officiel.
- Cache KV q8_0 : ne tient pas 393k, étranglement mesuré.
- `--cache-reuse`, `--kv-unified`, `--swa-full`, `--no-op-offload` : sans objet sur un slot unique et une archi hybride.
- HAGS, mode TCC, mode persistance, verrouillage d'horloges : TCC et persistance n'existent pas sur GeForce Windows ; HAGS sans mesure sur l'inférence ; horloges déjà hautes au repos (P1, 2 400 MHz).
- cdiamond imatrix NVFP4 (17,1 Go) : meilleure fidélité, plus lourd, donc plus lent ici.
- FastMTP HauhauCS : patch hors amont, modèle désinhibé, aucune preuve tool-calling.
- Whittle-MoE, EXL3, MLX, NInfer, vLLM, SGLang, SparkInfer : hors moteur ou hors format.
- PR 27173 (chaînage MTP, +12,8 % mixte, +28 % sur du code répété, 2x5090) et PR 27210 (profondeur MTP adaptative) : ouvertes, non fusionnées. À surveiller, pas à compiler.
- Qwen3.8-Flash-Next (125B-A6B, 26/08) : 68 à 104 Gio de poids, ne tient pas.
- GLM-4.7-Flash (200k), gpt-oss-20b (128k) : fenêtre insuffisante. GLM-4.7, MiniMax M2, DeepSeek V4, Kimi K2/K3, Hunyuan, Step 3.5 : ne tiennent pas en 32 Go. Kimi Linear : support immature, 35 Go estimés.

## 5. Plan de campagne proposé

Chaque étape : profil de production intact, mesures sur une instance sonde ou par bascule courte, graine fixe, trois tirages, contexte vide et 150k, jeu qualité (500 MMLU + 60 GSM8K) quand le modèle ou la réflexion change. Rien ne passe en production sans un écart hors bruit et une non-régression prouvée.

1. P1, attribution des 21 s (lecture seule, sonde, dix minutes).
2. P3, binaire b10826 posé à côté (`llama-cpp-b10826`), contrôle MTP à l'identique, rejeu de P1 dessus.
3. P4 puis P5 sur b10826, variables et drapeaux seulement.
4. P2, relevé du ratio réflexion sur une session réelle, puis mesure `medium` contre défaut, puis ton arbitrage.
5. P6, DFlash2 en profondeur 8 à 16 sur b10826.
6. P7, Nemotron 3.5 Lightning sur le banc du 01/09 (débit, prefill à 150k, MMLU, GSM8K, tool-calling sous Claude Code), et ta décision sur Tiel.
7. P8 et P9 en fin de campagne, seulement si les six premières étapes sont soldées.

Preuves attendues : tableau par étape dans `docs/tuning-log.md`, valeur retenue ancrée en commentaire dans `llm-ctl.ps1`, lanceurs et skills alignés, fiche `infra-llm-local` et fiche de vie du poste à jour. Toute exécution sur la machine se fait une commande à la fois, par moi, jamais par un agent.

## 6. Avancement au soir du 06/09

- P3 fait sur le profil `tiel` : binaire b10826 posé, contrôle à protocole identique, prefill +4,9 %, décodage identique, VRAM -160 Mo. Le profil `qwen` reste sur le build du 27/08, à basculer avec la même mesure.
- Parallélisme mesuré et tranché : `--parallel 2 --kv-unified` retenu sur Tiel pour deux postes (190 à 200 tok/s chacun en génération), quatre slots rejetés (moitié du débit par flux, 580 Mo de marge VRAM en charge). Détail dans `docs/tuning-log.md`.
- Tiel passe à température 0,6 (fiche Ornith) pour un essai en usage réel, décidé par Guillaume.
- Le banc de consignes strictes ne reproduit pas les ratages signalés sur Tiel (36/36, 8/8, 7/8) : il faut un cas réel de session longue sur gros dépôt.
- P1, P2, P4, P5, P6, P7 (Nemotron) : non commencés, GPU rendu à l'usage.
