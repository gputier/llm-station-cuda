# Refonte du banc d'essai LLM, spec de conception

Date : 23/09/2026. Statut : spec à relire, rien n'est modifié sur le dépôt ni sur les machines.
Périmètre d'écriture futur : dépôt `Tools/LLM` et wiki du projet.

## 1. Objectif

Repasser au banc chaque LLM servi sur la 97 et la 99. Chaque modèle passe avec trois configurations
complètes, conçues pour aller chercher le maximum, sur un banc refait à partir d'épreuves publiques
reconnues et d'une épreuve maison proche de notre usage : agentique, beaucoup de code, raisonnement,
longs contextes. Rien ne vient de mémoire : chaque réglage cite sa source ou se déclare
« raisonnement, non sourcé ».

Livrables finaux :
1. un banc versionné, reproductible, lancé en conteneurs ;
2. les résultats de la campagne, avec marges d'erreur ;
3. un tableau comparatif publiable, rédigé en tant que testeur (LinkedIn ou autre) ;
4. un dépôt propre, publiable ;
5. une convention d'entrée pour chaque nouveau modèle ;
6. la doc du banc réécrite (dépôt `docs/`, README, wiki).

Hors périmètre : la station Vulkan (retirée le 07/09/2026) et la machine 98 (embedder dédié).

## 2. Constat de départ (établi le 23/09/2026, lecture seule)

### 2.1 Pourquoi les données actuelles ne sont pas viables

- MMLU et GSM8K (`bench/banc.ps1`) : jeux publics figés, contamination probable, jamais traitée.
- Une seule passe par question, décodage glouton, seed 42, aucun intervalle de confiance.
- Sampling du banc différent de celui du service (`banc.ps1` l.24-27). Réglages recopiés d'un modèle
  à l'autre pendant la campagne du 10/09 (`docs/campagne-mesures-2026-09-10.md` l.162-168).
- Consigne « sans réflexion » honorée par certains modèles et ignorée par d'autres
  (`banc-inedit.ps1` l.33-47) : protocole identique sur le papier, inégal dans les faits.
- GSM8K à 60 items : un écart pèse 1,7 point, jamais chiffré.
- Jeu inédit trop petit : un écart de 8,5 points n'y atteint pas la significativité
  (`comparer-inedit.ps1` l.9-13).
- `epreuves.jsonl` et `inedit/` ne sont pas versionnés : rien n'est auditable.
- Deux campagnes mortes sur un item mal formé (fiche `test-qui-recopie-sa-reference-ne-mesure-rien`).
- Aucun test d'agentique, d'appel d'outils ni de code exécuté. `quality.ps1` ne note rien.
- Long contexte : seul `aiguille.ps1` le teste, et seulement le rappel d'un fait isolé.

### 2.2 Ce qu'on garde

- `vitesse.ps1` : 3 passes, médiane, lecture du débordement VRAM, bons noms de champs
  (`draft_n`, `draft_n_accepted`).
- `aiguille.ps1`, dans son périmètre déclaré.
- `tools/*.py` : fabrication et contrôle du jeu maison (fuite, cohérence, relecture à l'aveugle).
- `comparer-inedit.ps1` pour sa méthode (McNemar apparié, pouvoir discriminant).
- Le rejeu sans GPU (`-Depouiller`, journal JSONL des réponses brutes).

### 2.3 État des machines

- 99 (PC-GUILLAUME, RTX 5090 32 Go, 128 Go RAM) : `llm-ctl.ps1` déployé identique au dépôt
  (SHA-256 `67e81076…`). 13 profils, 7 builds. Aucun modèle chargé. ComfyUI occupe 26 Go de VRAM.
- 97 (PC-GUILLAUME-3, RTX 4080 SUPER 16 Go, 32 Go RAM) : `llm-ctl.ps1` identique à
  `llm-ctl-16gb.ps1` (SHA-256 `4ad98898…`). 2 profils (tiel, qwen36), un seul build BeeLlama v0.4.6
  (base b10830). Fichier de suivi périmé `D:\LLM-Setup\instances\tiel.json` (PID mort).

### 2.4 Faits vérifiés à la source qui conditionnent le banc

- PR llama.cpp #29257 (fusionnée le 22/09/2026, lue le 23/09) : modifie `common/chat.cpp` pour ne
  plus router vers le parseur Qwen3-Coder les templates qui écrivent
  `'<tool_call><function=' ~ tool_call.name ~ '>'` sans retour à la ligne. Ces modèles bouclaient
  jusqu'au plafond de jetons. Aucun de nos builds ne l'a (b10826, b10883 et BeeLlama b10830 sont
  antérieurs).
- Issue llama.cpp #29295 (ouverte le 23/09/2026) : `tool_choice: required` n'est pas imposé par
  grammaire sur Qwen3 avec `--jinja`, et un cache chaud aggrave le basculement vers du texte libre.
- PR #20171 (réarrangement des arguments optionnels) : fusionnée le 06/03/2026, donc déjà présente
  dans nos builds.
- Issue #26337 (drafter DSpark de bonsai) : toujours ouverte.
- Organisation HF `meta-models` : vérifiée, Meta Inc.
- Claude Code se branche directement sur llama-server par `ANTHROPIC_BASE_URL`, sans proxy
  (fiche `infra-llm-local`).

## 3. Décisions prises avec Guillaume

- Chaque machine teste ses propres modèles, et les deux campagnes tournent en parallèle. Tiel,
  présent sur les deux machines, sert de témoin entre elles.
- ComfyUI est arrêté pendant la campagne sur la 99, puis relancé à la fin.
- Le banc est mixte : épreuves publiques et épreuve maison.
- Trois configurations par modèle : R1 = référence éditeur, fidèle à la fiche Hugging Face ;
  R2 et R3 = deux paris d'optimisation distincts, sourcés.
- Livrables ajoutés : tableau publiable, dépôt propre, convention pour chaque nouveau modèle.

## 4. Architecture de la campagne

### Phase 0 : fiabilité, avant toute mesure

1. Compiler ou poser un build llama.cpp upstream postérieur au 22/09/2026 sur la 99 (le huitième
   répertoire de build, un par règle de la machine). Sur la 97, vérifier si BeeLlama a publié une
   version sur base postérieure. Sinon, poser le même upstream à côté de BeeLlama.
2. Pour chaque profil, lire le template réellement servi (`/props`, champ `chat_template`) et
   noter s'il déclenche le parseur Qwen3-Coder et s'il écrit les balises sans retour à la ligne.
3. Mesurer l'effet de l'issue #29295 sur un profil Qwen : même requête `tool_choice: required`,
   cache froid puis chaud, 5 passes chacune. Si le défaut se reproduit, le banc d'outils tourne
   cache vidé entre deux items, et le rapport le mentionne.
4. Poser `CLAUDE_CODE_ATTRIBUTION_HEADER=0` dans les lanceurs `~/.claude/bin/*` : l'en-tête
   variable empêche la réutilisation du cache de prompt (source : mykolaaleksandrov.dev, 06/2026).
   Contrôle : la ligne `restored context checkpoint` apparaît dans le journal du serveur.
5. Nettoyer `instances\tiel.json` sur la 97 (via `llm-ctl.ps1 -Action stop`, pas à la main).
6. Arrêter ComfyUI sur la 99. Consigner le geste dans la fiche de vie de la machine.
7. Contrôle de fumée pour chaque profil et chaque configuration : un vrai appel d'outil par
   Claude Code non interactif, avec la présence de `draft_n` dans `timings` quand une spéculation
   est posée. Une configuration qui ne passe pas ce contrôle est écartée avant la campagne, avec
   son motif.

### Phase 1 : construction du banc

Orchestrateur : le Mac, `docker compose`, un conteneur par harnais. Rien n'est installé sur le
poste. Les versions des harnais et des jeux sont épinglées (commit ou tag) dans le dépôt.

| Axe | Épreuve | Harnais | Taille retenue |
| --- | --- | --- | --- |
| Code, génération | LiveCodeBench, problèmes postérieurs à la coupure de chaque modèle | `livecodebench/livecodebench`, `code_generation_lite`, `OPENAI_BASE_URL` | 100 problèmes |
| Code, édition | Aider Polyglot | `Aider-AI/aider` `benchmark/benchmark.py` + `polyglot-benchmark`, `OPENAI_API_BASE` | 60 exercices, tirés une fois et figés |
| Agentique, outils | BFCL v4, appel natif (FC) | `bfcl-eval`, `--skip-server-setup`, `REMOTE_OPENAI_BASE_URL` | catégories simple, multiple, multi-tours |
| Raisonnement | LiveBench, dernière livraison au jour du pilote | `livebench/livebench`, `--api-base` | catégories raisonnement et maths |
| Long contexte, rappel | RULER, 13 tâches | `NVIDIA/RULER` ou `inspect_evals` | 32k, 128k, maximum servi |
| Long contexte, raisonnement | NoLiMa | jeu HF arXiv 2502.05167 | 32k et 128k |
| Maison, agentique réelle | Correction de bugs réels de nos dépôts par Claude Code | `claude -p --output-format json --max-turns N`, dépôt figé, tests = juge | 40 tâches |
| Débit | `vitesse.ps1` repris | llama-server `timings` | 3 passes, médiane |

Épreuve maison :
- Source : paires de commits de correctif avec tests dans nos dépôts.
- Clone figé au commit parent. L'historique est coupé (pas de `git log` exploitable) et l'agent n'a
  pas de réseau vers le dépôt source.
- Juge : les tests du commit de correctif, lancés dans le conteneur.
- Contrôle : le jeu passe `tools/verifier-epreuves.py` (adapté) avant le premier envoi.
- Les tâches viennent de dépôts privés : les scores sont publiables, les tâches ne le sont pas.

Mesures captées à chaque passe : `prompt_per_second`, `predicted_per_second`, `draft_n`,
`draft_n_accepted`, VRAM (`nvidia-smi`) et débordement en mémoire partagée (`Get-Counter`), temps
jusqu'au premier jeton (chronométré côté client sur le flux), jetons de réflexion
(`reasoning_content` avec `--reasoning-format deepseek`), build, hash du GGUF, ligne de commande
complète.

Méthode statistique :
- 3 passes par cellule (modèle × configuration × épreuve), sampling de la configuration testée.
- Intervalle à 95 % par bootstrap apparié sur les items (10 000 tirages, rééchantillonnage des
  items et non des passes).
- Comparaison de deux configurations : McNemar apparié sur les items communs (repris de
  `comparer-inedit.ps1`).
- Le rapport écrit noir sur blanc la taille d'écart que chaque épreuve peut trancher. Sur
  l'épreuve maison (40 tâches), un écart sous 15 à 20 points reste dans le bruit.
- Aucune mesure sur la première requête après chargement (fiche
  `premiere-requete-apres-demarrage-n-est-pas-une-mesure`). Arbre figé pendant toute la campagne
  (fiche `feedback-mesure-sur-arbre-fige`).

Stockage : un fichier JSONL par passe (réponse brute, score, mesures), un schéma commun à toutes
les épreuves, et un agrégateur qui produit le tableau.

### Phase 2 : pilote

Un modèle (tiel, présent sur les deux machines), une configuration (R1), toutes les épreuves en
taille minimale (LiveCodeBench 10, Aider 5, une catégorie BFCL, RULER 32k, 5 tâches maison), sur
chaque machine. On chronomètre séparément le chargement et l'évaluation. On extrapole la durée
de la campagne. Si elle dépasse ce qui est acceptable, les tailles de la phase 1 se réduisent avant
lancement, et le rapport indique ce que ça coûte en pouvoir de discrimination.

Le pilote vérifie aussi que chaque harnais parle réellement à llama-server : handler FC de BFCL
pour chaque famille de modèle, pointage de RULER sur un endpoint OpenAI, lecture de la réflexion.
Un harnais qui ne tient pas est remplacé avant la campagne, pas pendant.

### Phase 3 : campagne

99 : 12 modèles × 3 configurations = 36 configurations. 97 : 2 modèles × 3 = 6. Les deux machines
tournent en même temps. Pour chaque configuration : chargement, préchauffe, épreuves, relevé.
Reprise après coupure : chaque passe est idempotente et l'orchestrateur saute ce qui est déjà
journalisé.

L'embedder (nomic-embed-text-v1.5) sort du banc comparatif. Il passe une épreuve de conformité à
part : préfixes `search_document` et `search_query`, troncature Matryoshka, et fenêtre réellement
tenue (131 072 servis contre une fenêtre native à vérifier sur la fiche).

### Phase 4 : rapport, publication, convention, doc

- Rapport complet dans `docs/` : résultats, marges, écarts tranchables, configurations écartées et
  pourquoi.
- Tableau publiable (voir section 6).
- Nettoyage du dépôt (voir section 7).
- Convention pour chaque nouveau modèle (voir section 8).
- Doc réécrite via doc-copywriter, sur les deux cibles : `docs/` du dépôt et wiki
  `$WIKI_PATH/content/docs/<projet>`. Les chiffres anciens (MMLU/GSM8K du README) sont retirés ou
  marqués comme caducs.
- Les configurations gagnantes ne passent en service qu'avec le go de Guillaume.

## 5. Les trois configurations par modèle

Convention : R1 reprend la fiche éditeur. R2 et R3 sont des paris. « Non sourcé » veut dire
raisonnement à prouver par la mesure. Tous les liens ont été lus le 23/09/2026.

### 5.1 Machine 99

**qwen**, Qwen3.8-27B NVFP4 MTP LOW (actuel : ctx 393 216, KV q4_0, `-b 4096 -ub 2048`, MTP n-max 4,
template maison, T1.0/0.95/20).
- R1 : palier MEDIUM (16,4 Go), `--spec-draft-n-max 6 --spec-draft-p-min 0.75`, ctx natif 262 144,
  KV q8_0, template embarqué, sampling de la fiche selon le mode. Sources :
  huggingface.co/esatapedico/Qwen3.8-27B-NVFP4-MTP-GGUF, huggingface.co/Qwen/Qwen3.8-27B.
  Point de comparaison : MEDIUM et n-max 6 ont déjà perdu ici sur le débit.
- R2 : LOW, n-max 4, KV q4_0, template `froggeric/Qwen-Fixed-Chat-Templates`,
  `--reasoning-format deepseek`, effort de réflexion `medium`. Hypothèse : moins de tours bloqués et
  de sur-réflexion en agentique. Source : huggingface.co/froggeric/Qwen-Fixed-Chat-Templates.
- R3 : LOW, KV q4_0, n-max 3, `-ub 3072`, build postérieur au 22/09. Hypothèse : un réglage plus fin
  entre deux points déjà mesurés (non sourcé).

**qwenu**, Qwen3.8-27B Uncensored Q5_K_M (actuel : ctx 262 144, KV q4_0, MTP n-max 3 copié de qwen).
- R1 : Q6_K (20,9 Go), template embarqué, sampling non-thinking de la fiche (T0.7/0.80/20, presence
  1.5). Source : huggingface.co/Qwen/Qwen3.8-27B. Risque : le seuil d'environ 29 Go où le débit
  s'effondre sur cette carte.
- R2 : Q5_K_M, n-max 3, template froggeric, presence 1.5 en non-thinking. Hypothèse : moins de
  boucles et de refus d'appel.
- R3 : n-max 3, ctx 393 216 par override (mesure de la fenêtre pleine). Le balayage n-max 2/3/4
  déménage dans la suite de débit (la spéculation change la vitesse, pas les réponses) : voir
  arbitrage du 23/09/2026 ci-dessous. Hypothèse : combler un réglage jamais prouvé pour ce modèle
  (non sourcé).

**qwenf**, DavidAU TURBO FCF Q5_K_M (candidat, hors service).
- R1 : template de l'auteur, modes `reasoning_effort`, sampling thinking de la fiche. Source :
  huggingface.co/DavidAU/Qwen3.8-27B-TURBO-Fable-Cold-Fusion-735-882-Heretic-Uncensored-NEO-CODER-MAX-MTP-GGUF.
- R2 : sans réflexion, T0.7/top-k 64/top-p 0.95, template froggeric. Source : discussion HF n°36
  du même dépôt (banc de 320 outils, record sans réflexion).
- R3 : n-max 4 avec relevé `draft_n` (jamais fait sur ce fichier).

**qwent**, DavidAU TWIN-TURBO 709-L Q5_K_M (candidat, meilleur des trois sur l'ancien jeu inédit).
- R1 : template de l'auteur avec les modes `{REASON:xxx}`, sampling thinking de la fiche. Source :
  huggingface.co/DavidAU/Qwen3.8-27B-TWIN-TURBO-Fable-Cold-Fusion-709-L-Uncensored-NM-DAU-NEO-MTP-GGUF.
- R2 : sans réflexion, template froggeric, T0.7/top-k 64/top-p 0.95. Source : discussion HF n°18
  (qwenf repasse devant hors réflexion : c'est ce que ce banc tranche).
- R3 : ctx 393 216, `-ub` 4096 seul (R1 et R2 restent à 2048 : voir arbitrage du 23/09/2026), avec
  surveillance de la VRAM (31,6 Go à vide). Mesure réclamée par le README et jamais faite.

**bonsai2**, Ternary Bonsai 2 27B PQ2_0, fork PrismML + DFlash2.
- R1 : PTQ1_0 (5,95 Go) et drafter z-lab Q4_K_M. Source : huggingface.co/prism-ml/Ternary-Bonsai-2-27B-gguf.
- R2 : config actuelle, mesurée pour la première fois en qualité et en rappel à 150k et 250k.
- R3 : KV q4_0 et drafter z-lab ensemble, fenêtre au-delà de 262 144 si le GGUF l'accepte
  (non sourcé).

**tiel**, Tiel-Coder 35B-A3B MTP UD-Q4_K_XL (actuel : T0.3 jamais justifiée).
- R1 : T0.6, la valeur de la fiche Ornith-1.5-35B-A3B pour un usage général. Source :
  huggingface.co/ornith-ai/Ornith-1.5-35B-A3B.
- R2 : template froggeric Qwen 3.5/3.6 et effort `medium`. Risque : compatibilité avec le template
  « Sharp » dérivé, vérifiée en phase 0.
- R3 : KV q8_0. Risque : 990 Mo de marge mesurée à 393 216, avec un repli documenté (non sourcé).

**kat**, KAT-Coder V2.5 Dev 35B-A3B, KAT-Philly MTP Q4_K_M.
- R1 : sampling de la fiche (instruct T0.7/0.80, thinking T1.0 presence 1.5). Source :
  huggingface.co/Kwaipilot/KAT-Coder-V2.5-Dev.
- R2 : sur le fichier existant `KAT-Philly-MTP-Q4_K_M.gguf`, l'autre mode de la fiche Kwaipilot :
  sampling instruct (T0.7/0.80/20, presence 1.5) contre le sampling thinking de R1 (T1.0/0.95/20,
  presence 1.5), les deux sourcés sur la même fiche. `gbuzhf/KAT-Coder-V2.5-Dev-APEX-MTP-GGUF`
  (imatrix calibré sur KAT, tête MTP greffée, gain de 2,03x annoncé, HTTP 401 le 23/09) est retenté à
  la tâche 14 ; si le dépôt s'ouvre, il remplace R2. Voir arbitrage du 23/09/2026.
- R3 : template forcé par `--chat-template-file` si la phase 0 montre que le template embarqué
  déclenche le parseur Qwen3-Coder.

**nex**, Nex-N2.5-mini i1-Q4_K_M (sans spéculation, tête MTP absente des tenseurs).
- R1 : config actuelle, déjà conforme à la fiche (T0.7/0.95/40), avec `reasoning_effort` medium.
  Source : huggingface.co/nex-agi/Nex-N2.5-mini.
- R2 : `-ot` ciblé sur les experts de fin de réseau, pour libérer de la VRAM au profit du contexte.
  Source : huggingface.co/blog/Doctor-Shotgun/llamacpp-moe-offload-guide.
- R3 : cache K q4_0 et V q8_0, fenêtre au-delà de 262 144 (non sourcé ; deux variables à séparer si
  le résultat est mauvais).

**muse**, Muse Glimmer 30B UD-Q4_K_XL + DFlash + mmproj kquant.
- R1 : config actuelle, qui est déjà celle de la fiche (T1.0/0.95/64, `-ub 512`). Elle sert de témoin.
  Source : huggingface.co/meta-models/Muse-Glimmer-30B.
- R2 : ctx 1 048 576 et KV q4_0. À reprouver par `draft_n` à fenêtre pleine.
- R3 : mmproj BF16 (3,58 Go, écarté à l'origine faute de VRAM avec ComfyUI).

**spark**, Spark-X2.5-4B Q8_0 (actuel : T0.6, écart assumé à la fiche).
- R1 : T1.0/0.95/top-k 0, comme la fiche. Source : huggingface.co/XHToken/Spark-X2.5-4B.
- R2 : drafter `XHToken/Spark-X2.5-1.7B-GGUF` (environ 1,8 Go). Annoncé à +41 % avec une acceptation
  de 0,891 (discussion HF, non rejoué).
- R3 : T0.6/top-p 0.95/top-k 0/min-p 0 (le réglage réel de la station, llm-ctl.ps1, corrigé après
  ses premières heures à top-k 20 ; mesuré contre la fiche qui donne T1.0/top-k -1 en R1). Pas de
  grammaire GBNF sur les appels d'outils : voir arbitrage du 23/09/2026.

**ornith**, Ornith-1.5-9B Q5_K_M (Q5_K_M confirmé meilleur choix de quant par llm-bench.io et note.com).
- R1 : T1.0 (général) ou T0.6 (code), comme la fiche. Source : huggingface.co/ornith-ai/Ornith-1.5-9B.
- R2 : `-ub 512` et KV q8_0 (non sourcé pour ce modèle).
- R3 : ctx 393 216 par override (rappel jamais prouvé sur ce modèle).

**bonsai**, Ternary Bonsai 27B Q2_g64 (sans spéculation, DSpark cassé).
- R1 : T0.7/0.95/20, la seule valeur de la fiche GGUF, qui prime sur le `generation_config` hérité.
  Source : huggingface.co/prism-ml/Ternary-Bonsai-27B-gguf.
- R2 : drafter DFlash entraîné sur Qwen3.6-27B (`spiritbuun/Qwen3.6-27B-DFlash-GGUF`). Risque de rejet
  massif par la cible ternaire.
- R3 : KV q4_0 et ctx 393 216, avec le rappel à prouver.

### 5.2 Machine 97

**tiel**, UD-IQ3_XXS, BeeLlama, KVarN 3, `-b/-ub 512`.
- R1 : T0.6 de la fiche (même règle que sur la 99, pour la comparaison entre machines).
- R2 : KVarN 4 sur le drafter seul (non sourcé ; risque de débordement documenté sur qwen36).
- R3 : `-b/-ub 1024` (non sourcé ; un débordement de 278 Mo a déjà été vu sur qwen36).

**qwen36**, Qwen3.6-35B-A3B UD-IQ3_XXS, `--n-cpu-moe 2`.
- R1 : sampling de la fiche avec presence 1.5 (retirée aujourd'hui). Source :
  huggingface.co/Qwen/Qwen3.6-35B-A3B.
- R2 : `-ot` ciblé à la place de `--n-cpu-moe`, lancé sans le flag d'origine.
- R3 : template froggeric. Hypothèse : c'est lui qui cause la balise `</think>` parasite qui coupe
  une réponse sur deux. La requête fautive est capturée en phase 0 pour le prouver.

### Arbitrage du 23/09/2026

Guillaume a tranché quatre points de la tâche 15, appliqués ci-dessus :
- **qwent R3** : `-ub` 4096 seul, R1 et R2 restent à 2048.
- **qwenu R3** : le balayage n-max 2/3/4 part dans la suite de débit, pas dans une configuration de
  banc de réponses (la spéculation change la vitesse, pas le texte produit) ; R3 garde n-max 3 et
  mesure la fenêtre 393 216.
- **spark R3** : pas de grammaire GBNF. R3 reprend le réglage réel de la station,
  T0.6/top-p 0.95/top-k 0/min-p 0 (llm-ctl.ps1 ; top-k 20 n'était que le réglage de ses premières
  heures, corrigé depuis), mesuré contre la fiche (T1.0/top-k -1 en R1).
- **kat R2** : reste sur le fichier existant, avec l'autre mode de la fiche Kwaipilot (sampling
  instruct contre le sampling thinking de R1, les deux sourcés). APEX-MTP est retenté à la tâche 14
  et remplace R2 s'il s'ouvre.

### 5.3 Téléchargements prévus

Drafter Spark 1,7B, KAT APEX-MTP (sous réserve), drafter DFlash Qwen3.6-27B pour bonsai, Qwen3.8-27B
Q6_K pour qwenu R1, palier NVFP4 MEDIUM (déjà sur disque), PTQ1_0 de bonsai2, templates froggeric.
Chaque fichier est vérifié par son hash publié.

## 6. Tableau publiable

Une ligne par modèle × configuration × machine. Colonnes :
- modèle, éditeur, licence, quantification, taille du fichier ;
- GPU (modèle de carte et VRAM), build llama.cpp ;
- configuration (R1, R2 ou R3, avec un libellé court) ;
- scores par épreuve avec intervalle à 95 % ;
- débit de décodage, prefill, VRAM, contexte prouvé par rappel ;
- date de mesure.

Règles de publication :
- aucune IP interne, aucun nom de machine ;
- versions des harnais et des jeux indiquées ;
- licence de chaque modèle vérifiée avant de publier son score ;
- l'épreuve maison est présentée comme une épreuve interne, construite sur notre propre usage, sans
  aucun détail sur les tâches ni sur les dépôts d'où elles viennent ; seuls ses scores sont publiés ;
- chaque chiffre renvoie au journal brut qui le porte.

Un texte d'accompagnement court passe par anti-ai-patterns et linkedin-writing au moment de la
publication. Sa rédaction est un geste séparé.

## 7. Dépôt propre

Séparation tranchée le 23/09/2026 :
- Public, `llm-station-cuda/bench/` : code du banc, méthode, configurations, convention, tableau
  publié, empreinte SHA-256 de chaque jeu privé utilisé. N'importe qui peut vérifier comment on
  mesure. La règle actuelle du `.gitignore` (les questions ne sont jamais publiées) est conservée.
- Privé, nouveau dépôt `~/git_projects/Tools/Bench-LLM`, distant
  `git.shpv.work/gputier/bench-llm` en visibilité privée : jeux de questions maison, tâches
  agentiques, journaux bruts des passes. Les jeux ont un historique et sont audités sans jamais
  devenir publics.

Organisation du public :
- `bench/` restructuré : `harness/` (compose et images épinglées), `configs/` (trois configurations
  par modèle), `benchrun/` (orchestrateur), `report/` (agrégateur et tableau publié). Les jeux
  privés et les journaux sont lus depuis `Bench-LLM` par un chemin passé en variable.
- Anciens scripts : ceux qu'on garde (section 2.2) sont intégrés ; les autres sont retirés après
  archivage de leurs conclusions dans la doc.
- Aucun secret ni IP dans le dépôt, avec un contrôle automatique avant commit.
- Le README racine renvoie au tableau et à la convention.

## 8. Convention pour chaque nouveau modèle

1. Fiche d'identité : dépôt HF de base et dépôt GGUF (URL, date de lecture), licence, contexte
   natif, sampling par mode, benchmarks publiés par l'éditeur.
2. Trois configurations : R1 fidèle à la fiche, R2 et R3 sourcées ou marquées « non sourcé ».
3. Porte d'entrée (phase 0 réduite) : template servi lu, appel d'outil réel par Claude Code, `draft_n`
   présent si une spéculation est posée, rappel à la fenêtre servie.
4. Banc standard complet, 3 passes, avec les mêmes harnais épinglés.
5. Ligne ajoutée au tableau et README du modèle écrit selon le gabarit commun.
6. Mise en service seulement si le modèle bat, au-delà de la marge, le modèle en place sur son rôle.

## 9. Risques et parades

- Harnais qui ne parle pas à llama-server (BFCL sans handler, RULER) : détecté au pilote, remplacé
  avant la campagne.
- Bug #29295 qui fausse le banc d'outils : mesuré en phase 0, cache vidé entre deux items si besoin.
- Durée de campagne excessive : le pilote décide des tailles avant le lancement.
- Débordement VRAM sur les configurations R3 : repli documenté, et le débordement est lu à chaque
  passe.
- Contamination de l'épreuve maison par l'historique git : clone figé, historique coupé, pas de
  réseau.
- Modification du dépôt pendant une campagne : interdite, arbre figé.
