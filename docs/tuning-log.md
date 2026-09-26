# Tuning log

Every campaign run on this box, including the ones that found nothing. The
negative results are the more useful half: they tell you which hypotheses are
already spent.

Unless stated otherwise, all figures are at fixed seed, median of 3 runs, on the
hardware listed in [prerequisites.md](prerequisites.md).

---

## 2026-09-26 : fenêtre unique à 262 144, réglages des auteurs, six profils de plus, et xing revient

Tous les profils écrits à la main servaient une fenêtre de 262 144 jetons, sauf `tiel`, `kat` et
`qwen`, à 393 216 avec un `--override-kv` sur la fenêtre. Guillaume a fixé une fenêtre unique de
262 144 : les trois reviennent à celle que déclare leur GGUF, et l'override disparaît.

L'échantillonnage a été réaligné sur les fiches des auteurs, modèle par modèle : `tiel` passe à une
température de 0,6 sur les deux machines (au lieu de 0,3), `kat` à 1,0 avec une pénalité de présence
de 1,5, `ornith` et `spark` à 1,0 (au lieu de 0,6), `bonsai2` reçoit un min-p de 0,05, `qwen36` une
pénalité de présence de 1,5, et `nex` un effort de réflexion `medium` désormais explicite plutôt que
laissé au défaut du serveur.

Six profils rejoignent le script après le banc du jour : `hemmingway` (Altworld Hemmingway-1),
`veriloop` (VeriLoop-E2) et `xing` (Xing 4.0 29B-A4B) sur la machine 32 Go seulement ;
`qwen36apex`, `katapex` et `occamy`, trois modèles requantisés par APEX, sur les deux machines
(MiniPlus V2.1 sur la machine 32 Go, NanoPlus ou dynamic v2 sur celle de 16 Go). Le moteur `b11156`
sert cinq des six ; `xing` a besoin du moteur compilé pour la pull request llama.cpp #29012. `xing`
avait été noté rejeté le 19/09 et ses poids supprimés le soir même (entrée du 21/09, plus bas) : les
poids ont été reposés pour ce banc et le moteur reconstruit le 24/09, il est de retour.

Le budget de sortie passe à 81 920 jetons partout dans `clients/llm-launch.sh`, contre 16 384. La
fenêtre annoncée à Claude Code devient 262 144 moins 81 920, soit 180 224. Le déclencheur de
compaction par variante (huitième argument `COMPACT_AT` de `llm_variant`, et son cas particulier
`CLAUDE_CODE_AUTO_COMPACT_WINDOW=180000` pour `qwen36`) a été retiré : lu dans le binaire 2.1.283 le
26/09, Claude Code compacte de lui-même à la fenêtre annoncée moins le plus petit de la sortie et de
20 000, moins 13 000 jetons de marge pour son résumé, soit environ 147 224 jetons ici, en dessous du
seuil que portait `qwen36`. Le réglage par variante n'avait donc plus d'effet.

`kat` reconnaît désormais son propre dossier de poids, `kat-coder-v25`, et `qwen36` le sien,
`qwen3.6-35b-a3b-mtp` : les deux versions APEX du même modèle portent aussi `kat-coder` et
`qwen3.6-35b` dans leur chemin, et le motif de correspondance devait se resserrer pour ne pas les
confondre.

### Preuve par exécution, station .99

Treize profils chargés l'un après l'autre. Pour chacun, `/props` annonçait un `n_ctx` de 262 144 et
les réglages d'échantillonnage décrits plus haut, et le modèle a répondu malgré un message système
glissé en fin de conversation. VRAM relevée au `nvidia-smi`, carte de 32 607 MiB :

| Profil       | VRAM (MiB) |
| ------------ | ---------- |
| `tiel`       | 29 198     |
| `kat`        | 26 822     |
| `qwen`       | 26 594     |
| `ornith`     | 14 872     |
| `spark`      | 12 572     |
| `bonsai2`    | 25 292     |
| `nex`        | 26 760     |
| `hemmingway` | 31 078     |
| `veriloop`   | 27 308     |
| `xing`       | 26 692     |
| `qwen36apex` | 18 612     |
| `katapex`    | 18 612     |
| `occamy`     | 19 448     |

La machine 16 Go (.97) traitait des données ce jour-là : son nouveau script a été déposé sans charger
aucun modèle, et rien de mesuré ici ne vaut pour elle. Le contrôle en charge de ses cinq profils est
suivi dans l'issue 3 du dépôt.

---

## 2026-09-21 : deux profils rejetés quittent le script, et un moteur de 5,15 Go avec

`whittle` et `xing` avaient été rejetés le 19/09 et leurs poids supprimés le soir même, mais les
deux profils restaient dans `llm-ctl.ps1` au motif qu'un nouveau téléchargement les relancerait.
Le résultat concret : le lanceur annonçait quinze noms dont treize démarraient, et les deux
autres échouaient au chargement sans que rien ne le dise à l'avance. Retirés.

Le moteur compilé pour `xing`, `llama-cpp-xing-pr29012`, a été supprimé de la station dans le
même geste : 5,15 Go mesurés, pas les 13,3 que la colonne CUDA du tableau des moteurs a pu
laisser croire. La place libre sur D est passée de 417,88 à 423,07 Go. La recette de compilation
reste dans [building-llama-cpp.md](building-llama-cpp.md), parce que c'est elle qui dit ce que
coûte un moteur pour une architecture qu'aucune version publiée ne connaît.

Ce que les deux modèles ont mesuré reste dans les entrées du 19/09, plus bas. C'est la seule
raison d'avoir mesuré.

### Le journal du serveur était effacé à chaque démarrage

Trouvé en cherchant pourquoi une session cliente avait perdu sa connexion le matin du 21/09.
`Start-LLM` appelait `Clear-Content` sur `llm-err-<profil>.log` avant chaque lancement, et la
redirection `2>` le tronquait de toute façon. Un serveur qui meurt laisse sa trace dans ce
fichier, et le redémarrage qui suit la détruit : aucune autopsie n'était possible, et celle du
21/09 n'a pas pu être faite.

Corrigé : le fichier précédent est renommé avec l'horodatage de sa dernière écriture, à la
milliseconde, et cinq archives par profil sont gardées. Éprouvé sur la station par sept
rotations successives, cinq fichiers restants, sans toucher au serveur en service.

La panne elle-même, côté client : `FailedToOpenSocket`, un code d'erreur du moteur Bun sur lequel
Claude Code est compilé, vérifié dans la table des noms d'erreur du binaire. Il dit que la
connexion TCP n'a jamais pu s'ouvrir. Reproduit à l'identique en pointant un client sur un port
fermé de la station : « Can't reach the API server, check your internet or DNS
(FailedToOpenSocket) ». Le message parle d'internet et de DNS, mais un client local ne joint
jamais Anthropic : il ne parle que de la station. Un `FailedToOpenSocket` sur un profil local se
lit donc comme « plus rien n'écoute sur 8080 », et jamais autrement.

---

## 2026-09-20 : la spéculation de Bonsai 2, rouverte et gagnée

Le 18/09 ce profil a été laissé sans brouillon, sur la foi de deux affirmations : Bonsai 2 n'en
publie aucun, et celui de Bonsai 1 ne transfère pas. La première était périmée le lendemain, la
seconde était mal mesurée. Correction : 37 % de génération en plus.

Un brouillon DFlash2 entraîné contre cette cible existe depuis le 17/09,
`Bonsai-2-27B-DFlash2-Q8_0.gguf`, 2,06 Go. Il n'embarque pas sa propre table de mots et emprunte
celle de la cible, ce qui explique sa taille. Mesuré sur le banc maison, invite complète, médiane
de trois passes :

|                             | Sans brouillon | ProCreations Q8_0 | z-lab Q4_K_M    |
| --------------------------- | -------------- | ----------------- | --------------- |
| Génération                  | 102,9 tok/s    | **141,4 tok/s**   | 139,9 tok/s     |
| Ingestion                   | 3 259 tok/s    | 2 879 tok/s       | 2 861 tok/s     |
| Acceptation                 | -              | 426/768, 55,5 %   | 420/780, 53,8 % |
| VRAM                        | 20 297 Mio     | 25 463 Mio        | 24 591 Mio      |
| Débordement en fin de passe | 1 508 Mio      | 4 132 Mio         | non relevé      |

Le ProCreations est retenu : 1,5 tok/s de mieux pour 872 Mio de plus, ce qui est dans le bruit des
deux côtés, mais la mémoire est disponible et il accepte mieux en prose. Sur une carte serrée, le
z-lab est le bon choix. L'ingestion perd 12 % dans les deux cas, et c'est le seul vrai prix.

**Le brouillon fait déborder en mémoire partagée, et le débit n'en souffre pas.** Relevé le 20/09 à
20 h : 1 648 Mio au chargement, 4 132 Mio en fin de génération, contre 1 508 Mio constants sans
brouillon. La génération médiane reste à 141,2 tok/s dans cette même passe, donc ce débordement ne
se paie pas au débit ici. Ce n'est pas un détail pour autant : il se creuse pendant la génération,
et un profil plus lourd chargé sur la même carte le ferait grossir. La ligne « débordement 0 »
publiée d'abord était fausse, elle ne lisait que l'état au chargement.

### La profondeur du brouillon, forcée à 4 et non laissée par défaut

Le profil `muse` porte la mesure inverse sur le même mécanisme `draft-dflash` : y forcer la
profondeur effondrait l'acceptation pour un débit équivalent. Vérifié sur `bonsai2` le 20/09,
même banc, trois passes chacun.

|             | Profondeur par défaut | Forcée à 4      |
| ----------- | --------------------- | --------------- |
| Génération  | 132,0 tok/s           | **141,2 tok/s** |
| Ingestion   | 2 888 tok/s           | 2 863 tok/s     |
| Acceptation | 411/639, 64,3 %       | 426/768, 55,5 % |
| VRAM        | 25 313 Mio            | 25 463 Mio      |

Le piège de `muse` ne se reproduit pas : forcer à 4 coûte bien 9 points d'acceptation, et rend
quand même 7 % de génération en plus. Le réglage est gardé. C'est la troisième fois que
l'acceptation désigne le perdant sur ce banc, ce que dit déjà l'en-tête de `vitesse.ps1` : le taux
d'acceptation renseigne, il ne tranche pas.

**Le moteur a dû être compilé.** Les deux archives publiées du fork refusent le brouillon,
`expected 81, got 58` : le support DFlash2 est arrivé en amont le 27/08 et n'a pas encore atteint
le fork. Le report du commit amont par patch échoue, 36 morceaux rejetés sur 38. L'auteur du
brouillon publie l'arbre déjà fusionné à côté de ses poids, et c'est celui-là qui est construit,
dans `D:\LLM-Setup\llama-cpp-prism-dflash2`.

**La demande d'ajout 210 du fork ne doit PAS y être appliquée**, alors qu'elle traite exactement le
défaut qui nous concerne. Cet arbre applique déjà la transformation inverse après la lecture de la
table de mots, dans `src/llama-graph.cpp`. Compilé avec le correctif en plus, la transformation
passe deux fois : acceptation de 86-91 % tombée à 6-10 %, génération de 231-257 tok/s tombée à
72-81, sur les deux brouillons. Sans le correctif, cet arbre rend les chiffres que la demande
d'ajout présente comme son gain, 190 sur 208 en code et 39 sur 172 en prose, au jeton près.

Contrôle de non-régression : sur deux des trois questions du protocole publié, la sortie est
identique octet pour octet à celle obtenue sans brouillon. La prose diffère, ce que la validation
publiée rapporte aussi et attribue au lot de calcul, pas à la spéculation.

---

## 2026-09-19 : audit des quinze profils, Ornith n'entendait pas les rappels

Tous les fichiers cités par `llm-ctl.ps1` existent sur la station, sauf les poids de `whittle`
et `xing`, supprimés exprès. Les templates embarqués de sept modèles ont été extraits des GGUF
et rendus hors ligne avec un message système en milieu de conversation, comme Claude Code en
envoie. `kat`, `tiel`, `nex`, `spark` et `muse` le gardent. Le template embarqué de `qwent` lève
l'erreur connue, sans effet puisque le profil sert le dérivé de `qwen3.8-27b`.

`ornith` était le seul vraiment faux : son template le supprime sans rien dire. Corrigé par un
dérivé câblé dans le profil, prouvé hors ligne, par `/apply-template` et par une vraie session
Claude Code avec appel d'outil. Le détail est sur
[la page du modèle](../models/ornith-1.5-9b/README.md).

---

## 2026-09-19 : Xing4.0-29B-A4B, compilé pour rien

Mandat : passer au banc le modèle récent le plus prometteur sur les chiffres de
son éditeur, Xing4.0-29B-A4B de China Telecom, sorti le 16/09. C'est un MoE de
29 milliards de paramètres, dont environ 4 actifs, avec une attention MLA.
Aucune version publiée de llama.cpp ne connaît son architecture : le moteur a été
compilé sur la machine depuis la demande d'ajout #29012 (voir
[building-llama-cpp.md](building-llama-cpp.md)). GGUF officiel IQ4_NL, 20,1 Go.

Il charge à 23 889 MiB et décode à 147 tok/s sur une invite courte. Protocole
identique à la campagne du même jour, plus bas.

Jeu public : 364/500 MMLU (72,8 %) et 48/60 GSM8K en 10,9 min, aucune réponse
vide. C'est sous `whittle` (378) et loin de `qwent` (460).

Jeu inédit, réflexion active : 123/235 (52,3 %) en 49,8 min, dont 97 réponses
vides. Sa réflexion consomme tout le budget avant de répondre, surtout en F1,
F4 et F6. Aucun leurre.

Il n'a pas été rejoué avec le réglage de son éditeur (température 1,0) :
avec 96 questions de retard sur `qwent` au jeu public, où la réflexion est coupée et où il ne
laisse aucune case vide, un meilleur échantillonnage ne comblerait pas l'écart.

Verdict : rejeté. Ses poids sont supprimés de la station, le profil et le moteur
restent.

---

## 2026-09-19 : trois candidats contre `qwenu`, un seul passe devant

Mandat : dire si l'un des trois modèles récupérés ce jour remplace `qwenu`.
Règle posée avant la campagne : il faut battre `qwenu` sur le jeu inédit, un
gain sur le jeu public seul ne décide rien. Tous tournent le même jour, sur le
même binaire, avec les scripts `banc-tous.ps1` (jeu public, réflexion coupée) et
`banc-inedit.ps1 -Reflexion` (235 épreuves, réflexion active), température 0,
graine 42.

Jeu inédit : `qwent` 225/235 (95,7 %), 5 vides. `qwenf` et `qwenu` 221/235,
avec 5 et 10 vides. `whittle` 142/235 (60,4 %), 26 vides et un leurre.

Jeu public : `qwenf` 461/500 MMLU et 56/60 GSM8K en 13,5 min, `qwent` 460/500
et 56/60 en 15,3 min, `qwenu` 450/500 et 57/60 en 33,7 min, `whittle` 378/500
et 46/60 en 12 min.

`whittle` a été rejoué avec le réglage de son auteur (température 0,7, top-p
0,8, top-k 20, repeat-penalty 1,05), puisque sa fiche prévient que le décodage
glouton fait boucler cette famille : 127/235 et 34 vides, moins bien. Le banc ne
le bridait pas. Ses poids sont supprimés de la station, le profil reste.

`qwenu` fait 90 % au jeu public contre 77,0 % le 10/09, à script, binaire, poids
et profil identiques. Les sorties brutes du 10/09 sont perdues, la cause n'est
pas établie. Contrôle le même soir : `tiel` redonne 433/500 et 53/60, ses
chiffres exacts du 10/09. Le banc n'a pas dérivé, le classement des autres
modèles tient.

Verdict : `qwent` passe devant, de 4 épreuves sur 235 en une passe chacun. Il
charge à 31 597 MiB, au-dessus des ~29 Go où le débit s'effondre sur cette
carte, et les invites du banc sont courtes : sa vitesse en contexte long n'est
pas mesurée. Il est à l'essai en usage réel, accessible par le choix 4 du
lanceur `qwen`. `qwenf`, à égalité, n'est pas retenu.

---

## 2026-09-18 : l'écart Blackwell, 97 contre 130 tok/s, expliqué et non corrigé

Mandat : rapprocher le décodage mesuré sur `bonsai2` (97,2 tok/s consigné plus
bas, 103,2 tok/s en médiane de trois passes ce jour-là, l'écart entre les deux
tenant au bruit de mesure normal) des 129,9 tok/s annoncés par la fiche PrismML
sur RTX 5090. Deux questions : le moteur porte-t-il des noyaux CUDA natifs pour
Blackwell (sm_120), et d'où vient l'écart. Aucune des deux réponses n'a mené à
changer le profil.

### Les noyaux sm_120 sont bien présents, natifs, dans le binaire déjà en service

`cuobjdump --list-elf` sur `D:\LLM-Setup\llama-cpp-prism-b10685\ggml-cuda.dll`
liste, pour chaque noyau, quatre variantes : `sm_86`, `sm_89`, `sm_120a` et
`sm_121a`. La carte annonce `compute_cap 12.0` par `nvidia-smi`, qui correspond
à `sm_120`. Le moteur en service contient donc déjà des noyaux compilés pour
cette architecture précise, avec le suffixe `a` propre aux fonctionnalités
spécifiques à la génération (les tensor cores de cinquième génération). Il n'y
avait rien à remplacer de ce côté, et aucune release plus récente n'a été
installée sur cette base : `prism-b10687-5d80cff` existe (17/09) mais ses notes
de publication ne mentionnent aucun correctif CUDA, Blackwell ou de
performance, seulement la prise en charge de `Q1_0`.

### L'écart tient à la profondeur de contexte, pas à un réglage

La fiche du modèle précise que ses 129,9 tok/s viennent de `llama-bench`, en
« batch size 1 and depth 0, no vision tower » : un décodage mesuré depuis un
contexte quasiment vide, sans le projecteur de vision chargé. Le profil
`bonsai2` de ce dépôt sert 262 144 tokens de contexte, avec le mmproj chargé, et
le banc `vitesse.ps1` mesure le décodage après ingestion d'une longue invite
réelle d'environ 56 000 caractères : l'attention complète coûte alors bien plus
cher par token.

Preuve directe : en tronquant l'invite du banc à 2 000 caractères (environ 500
tokens), le décodage remonte à **130,4 tok/s** (médiane de 3 passes), quasiment
identique au chiffre publié. Confirmation indépendante par le chemin client
réel : un appel `/v1/chat/completions` avec une invite de 105 tokens a mesuré
**129,26 tok/s** de décodage dans `timings.predicted_per_second`. L'écart n'est
donc pas un défaut de ce profil : c'est la comparaison entre deux régimes de
charge différents, et le régime que sert réellement `bonsai2` est
structurellement plus coûteux.

### Trois essais sur l'invite complète, aucun gain retenu

Chaque essai a redémarré `bonsai2` via `llm-ctl.ps1 -Extra`, trois passes,
graine fixe, sur la même invite complète que la mesure de référence.

| Réglage                     | Décodage    | Ingestion   | VRAM dédiée | Débordement |
| --------------------------- | ----------- | ----------- | ----------- | ----------- |
| Référence (profil inchangé) | 103,2 tok/s | 3 320 tok/s | 20 854 MiB  | 1 508 MiB   |
| `--no-cont-batching`        | 102,6 tok/s | 3 293 tok/s | 20 854 MiB  | 1 508 MiB   |
| `-ub 4096`                  | 102,8 tok/s | 3 191 tok/s | 22 270 MiB  | 2 612 MiB   |
| `--no-mmproj-offload`       | 102,6 tok/s | 3 260 tok/s | 19 716 MiB  | 1 508 MiB   |

Les trois écarts de décodage tiennent dans le bruit de mesure. `-ub 4096` coûte
1 416 MiB de VRAM et 1 104 MiB de débordement supplémentaires pour rien.
`--no-mmproj-offload` libère 1,1 GB de VRAM mais ne change rien au décodage : le
projecteur de vision n'est pas ce qui coûte cher pendant la génération de texte.
Le débordement de 1 508 MiB en mémoire partagée est constant sur toutes les
configurations sauf `-ub 4096`, mmproj retiré de la carte compris : il ne vient
donc pas du projecteur. Sa cause n'est pas établie, alors que 11 Go restent
libres sur la carte ; le compteur `Shared Usage` compte aussi la mémoire hôte
épinglée, ce qui en fait peut-être autre chose qu'un débordement. Le décodage
à 130,4 tok/s sur invite courte montre qu'il ne pèse pas sur la génération.
Aucun des trois réglages n'a été conservé ; le profil du dépôt reste inchangé.

### Validation client

Un appel `/v1/chat/completions` avec un tour `system` placé après un tour
`user` (le cas qui faisait échouer ce profil avant la correction de gabarit du
même jour, voir plus bas) a répondu **200**, avec un texte cohérent en
français.

---

## 2026-09-18: Bonsai 2, and two ways to spend VRAM that buy nothing

`bonsai2` was installed on PrismML's fork and benched the same day, same prompt
and same bench as everything else here. The headline is ingestion: **3,347 tok/s
against 56** for the first generation, sixty times over, with decode a wash at
97.2 against 102.6. On a client that re-reads a long conversation at every turn,
that single figure decides between the two.

Two levers the upstream demo repository advertises were tested rather than
assumed, and both were rejected.

### `BONSAI_KV4`, a 4-bit KV cache, is free memory this box does not need

| Cache      | Decode         | Prefill         | Card, dedicated |
| ---------- | -------------- | --------------- | --------------- |
| **`q8_0`** | **97.2 tok/s** | **3,347 tok/s** | 20,282 MiB      |
| `q4_0`     | 98.3 tok/s     | 3,349 tok/s     | 16,184 MiB      |

Both deltas sit inside the noise; the only real effect is 4,098 MiB freed. The
32 GB box has 12 GB spare either way and 262,144 is already the model's own
ceiling, so there is nothing to buy with that memory. Against it, the demo's own
page says the K cache loses a little accuracy at 4 bits unless a calibration
bias is built with `llama-kv-mean-center` and kept in step with the weights.
Paying in quality and in maintenance for headroom nobody needs is the wrong
trade. On the 16 GB box the answer would invert.

### A drafter does not transfer between two models of the same family

Bonsai 2 publishes no drafter. Bonsai 1 publishes a dspark one, in a
pre-migration packing that no current binary loads, which is the real cause of
llama.cpp issue 26337 and of the failure logged on 2026-09-10. The fork's
`gguf-dspark-to-dflash` repacks it, taking the target model as tokenizer donor,
so the first generation's drafter can be pointed at the second. It converts, it
quantises to 592 MiB, the server loads it, speculation engages.

|                 | Without     | With the converted drafter   |
| --------------- | ----------- | ---------------------------- |
| Decode          | 98.3 tok/s  | **44.9 tok/s**               |
| Prefill         | 3,349 tok/s | 2,901 tok/s                  |
| Acceptance      |             | **6 of 2,028 tokens, 0.3 %** |
| Card, dedicated | 16,184 MiB  | 24,741 MiB                   |
| Spilled         | 1,508 MiB   | 10,780 MiB                   |

Both runs on a `q4_0` cache, which is why the left column is the one above and
not the profile. **Decode halves.** A drafter is trained against one target's
output distribution: same family, same parameter count and one generation apart
is not close enough for a single token in three hundred. The upstream claim that
drafters are target-specific was correct, and it cost an hour to confirm.

### The chat template, a defect that had been live for eight days

Claude Code puts system turns in the middle of `messages`. The Qwen-family
templates both generations ship raise `System message must be at the beginning.`
and the server answers 500 before generating a token. So `bonsai` had never once
been able to serve an agentic client since it was installed on 2026-09-10, and
nobody saw it: the campaign of that day drove the raw endpoint, never a client.

Each model now carries a `chat-template-system-anywhere.jinja` next to its
weights, its own template with the raising line replaced by an emitted system
block, wired through `--chat-template-file`. Both were proved end to end, in
both directions, on a real tool call. A profile that is never driven the way it
will be used is not a profile that works.

---

## 2026-09-15: Qwen3.8-Flash-Next, the first model that does not fit on the card

A 125B-A6B model in UD-Q4_K_XL, 103.7 GiB, served with the experts of 42 of its
48 layers in host RAM, from `unsloth/Qwen3.8-Flash-Next-GGUF`, with its MTP head
`MTP/mtp-Qwen3.8-Flash-Next-shared-Q8_0.gguf`. It was chosen as the largest
recent coding model the box can hold whole; an earlier pick of the
same session, Qwen3-Coder-30B-A3B, was dropped before any weight arrived once
its release date was read: 2025-07-31, older than every model already here.

### The speed bench measured one cold run and called it a protocol

`bench\vitesse.ps1` printed `cache d invite desactive` and never sent
`cache_prompt: false`. Runs 2 and 3 read the prompt cache, so ingestion was only
ever run 1, which is also the run that pays for cold weights. On a model whose
experts are read from a memory-mapped file, that run measured the disk. The
field is now sent, ingestion is the median of all runs with run 1 alongside, and
ingestion figures logged before this date compare with that second number.

The copy of the script on the box was also behind the repository: it had neither
`-Chars` nor the spill reading. It was replaced, backup
`bench\vitesse.ps1.bak-20260915`.

### The split, the load mode, the draft depth

Same prompt, 400 tokens, three runs, median, the profile otherwise as committed.

| Configuration                                 | Decode      | Prefill     | MTP accepted | Card, dedicated                   |
| --------------------------------------------- | ----------- | ----------- | ------------ | --------------------------------- |
| `--n-cpu-moe 38`, mmap                        | not benched | not benched |              | 31,719 MiB and 7,838 MiB spilled  |
| `--n-cpu-moe 42`, mmap, depth 2               | 28.9 tok/s  | 452 tok/s   | 74.4%        | 30,739 MiB                        |
| same, `-b 8192 -ub 4096`                      | 23.6        | 251         | 71.7%        | 31,747 MiB and 13,656 MiB spilled |
| `--n-cpu-moe 42`, `--load-mode none`, depth 2 | 30.6        | 788         | 78.1%        | 31,066 MiB                        |
| same, depth 3                                 | 27.7        | 790         | 61.1%        | 31,180 MiB                        |
| **the committed profile, reloaded**           | **29.1**    | **783**     | 69.7%        | 31,066 MiB                        |

`--load-mode none` is the one lever that paid: +74% prefill, the card unchanged.
The process then holds about 67 GB of host RAM, which Windows reports as shared
GPU memory because the experts sit in pinned buffers. As on the 16 GB box, that
counter is a fixed allocation here and not a spill; the spill readings in the
table are the ones where the shared figure grew with the change, and where
speed fell with it.

Two failures before the first token. `-ngl 99` disables `--fit`, which aborts
and lets everything head for the card. And the MTP head dies with `invalid
vector subscript` unless `--tensor-split 1` is given, ggml-org/llama.cpp issues
27454 and 27717: once the target fills the card the draft's layer split is
computed from zero free memory.

For scale: `tiel` reads the same prompt at about 8,700 tok/s and decodes near 200. This model is an order of magnitude slower on both, and the prefill is the
figure that will be felt: a fresh 45,000-token Claude Code prompt waits close to
a minute.

### Quality, interrupted, and the verdict

`bench\banc.ps1 -Label flash` was stopped at 23:12 on Guillaume's call, after
350 of the 500 MMLU questions: 328 right, 93.7%. GSM8K never ran. A partial
score covers the first 350 lines of the set only, not all 25 subjects, so it
does not rank against the full-set 86.6% of `tiel` and `nex`; it says the model
is strong, not by how much.

Judged unusable for daily work at 29 tok/s decode and 783 tok/s prefill. `tiel`
was put back the same evening, and the weights, the Unsloth engine and the
`flash` profile were removed. The profile as it ran, for anyone retrying on
other hardware: Unsloth prebuild b10909-mix-bea84f7 cuda13-newer (the only build
with MTP for `qwen4exp`, ggml-org/llama.cpp#28243 being unmerged),
`--n-gpu-layers 99 --n-cpu-moe 42 --tensor-split 1 --load-mode none`,
`--spec-type draft-mtp --spec-draft-n-max 2`, `--ctx-size 262144`,
`-b 4096 -ub 2048`, cache `q8_0`, `-cram 24576`, temperature 1.0, top-p 0.95,
top-k 20, min-p 0.

Why there is no cheaper split. llama.cpp does not page the experts a token needs
into VRAM: the 10 experts chosen per layer change at every token, and on 42
layers they weigh about 1.2 GB per token at this quant (10 experts x 3 matrices
x 640 x 2,560 weights, 4.5 bits, computed from the header, not measured). Moving
that over PCIe 5.0 x16, about 63 GB/s, is slower than computing it where it
sits, in dual-channel DDR5-5200 (four 32 GB DIMMs, read the same evening) at
about 83 GB/s. That bandwidth, not the card, sets the ceiling: roughly 65 tok/s
before compute and before the n-gram table, halved in practice, and MTP is what
brings it back to 30. Only prefill borrows the card, by shipping whole batches
of experts to it. The one way to go faster on this box is a model whose experts
fit on the card.

## 2026-09-14, on the 16 GB box: what 29 hours of real use say that the bench did not

Read-only pass over the running `qwen27` instance, started 2026-09-13 at 02:01
and left in service. Nothing was restarted. The bench of the day before said
63.2 tok/s at short context; the question was why the box felt as slow as the
8 GB Vulkan card next to it.

### The log, 924 requests

|                                                                           | Value                                                             |
| ------------------------------------------------------------------------- | ----------------------------------------------------------------- |
| Decode, median / p10 / p90                                                | **45.1** / 37.0 / 57.1 tok/s                                      |
| Decode by total context: under 10k, 10k-30k, 30k-60k, 60k-100k, over 100k | 43.2, 47.1, 52.0, 50.7, 43.3 tok/s                                |
| Prompt tokens read / time                                                 | 23.2 M in 8.0 h                                                   |
| Tokens generated / time                                                   | 2.34 M in 14.7 h                                                  |
| **Share of machine time spent in prefill**                                | **35%**                                                           |
| Prefill on turns adding under 200 tokens, median / p10                    | 153 / 59 tok/s                                                    |
| Prefill on blocks of 20k to 100k new tokens, median                       | 1,219 tok/s                                                       |
| Prompt-cache evictions                                                    | **350**, 670 GiB churned, median entry 1.76 GiB, largest 6.25 GiB |
| Requests that re-read more than 20k tokens                                | 206 between 20k and 100k, 72 above 100k                           |
| MTP acceptance, median                                                    | 0.74, mean draft length 2.48, active on every request             |
| Largest context reached                                                   | 179,200 tokens                                                    |

Three readings. **Short contexts decode slower than long ones**, which is the
signature of a fixed cost per request, not of attention. **The draft head earns
nothing measurable in real use**: 44.7 tok/s at acceptance 0.5-0.7 against 46.8
above 0.85, the same lesson as the 5090 in a third form. And the **278 re-reads
of contexts already seen** are the bench's blind spot: a single-prompt bench
never evicts anything.

### The host RAM, which nobody had looked at

This box has 32 GB, the 5090 box 128. The profile was copied with
`--load-mode mlock` and `-cram 12288`:

|                            | Value                              |
| -------------------------- | ---------------------------------- |
| llama-server private bytes | 29,955 MiB                         |
| llama-server resident set  | **11,792 MiB**, peak 18,496        |
| Free RAM                   | 6.8 GiB of 31.7                    |
| Page file                  | 996 MiB in use, **peak 9,965 MiB** |

The locked weights (10 GiB) and the prompt cache (12 GiB) do not both fit, and
the one Windows paged out is the cache. A return to an evicted conversation
therefore reads from disk before it reads from the card. The loader source
settles what mlock buys here: with plain mmap, every fragment is unmapped once
it sits on the card (`llama-model-loader.cpp`, `unmap_fragment` after load), so
without the flag the host RAM goes to the cache. The effect is not measured yet:
the flag comes out at the next restart, and `llm-ctl-16gb.ps1` now refuses it
in `Start-LLM` as a constraint of the machine rather than of a profile. Then
`vitesse.ps1` and ten real turns decide whether `-cram` can go up.

### The GPU spill grew

15,506 MiB dedicated plus **1,322 MiB in shared memory**, against 1,048 the day
before under the same profile. Two display processes hold 304 and 151 MiB.
Decode did not collapse the way the 5090 does past its cliff, so the spill is
noted, not blamed.

### What transposes from the 5090 box, and what does not

The 5090 moved from this same dense 27B to a 35B-A3B on 2026-09-01: +54%
decode, x2 prefill, +8 MMLU points on the 2026-09-10 re-run. The gain is
structural and does transpose: about 3B parameters work per token, and the
attention cache is 3.2x smaller per token (10 full-attention layers x 2 KV
heads x 256, against 16 x 4 x 256, from both `config.json`). NVFP4 does not
transpose, its kernels are sm_120 only. Two candidates were fetched the same
day, `Tiel-Coder-35B-A3B-MTP-UD-IQ3_XXS` (13.6 GB) and
`Qwen3.6-35B-A3B-UD-IQ3_XXS` with MTP head (14.1 GB), with profiles `tiel` and
`qwen36` in `llm-ctl-16gb.ps1` on the `qwen27` recipe. Nothing measured yet;
the quality of the served 27B at IQ3_XXS is not measured either, and it is the
first thing to run when the box is free, so that the candidates have a control.

The fetch itself: this box has no `hf` client and no Python, and its Docker
Desktop and WSL are the owner's, not to be borrowed for a download. The two
weights were fetched once through a throwaway container before that rule was
restated, the container and its image removed the same hour; the binaries
went through the native `curl.exe`. Both GGUF headers were read before any
load: `qwen35moe`, 41 blocks, 2 KV heads, key length 256, full-attention
interval 4, one `nextn` layer, four `blk.40.nextn` tensors, on both files.

### The same evening, the box freed: three models, one bench

`bench/vitesse.ps1 -Runs 3 -Chars 30000 -Predict 150`, the 6,018-token prompt,
seed 42, decode as the median of three runs and prefill from the first, the
later ones reading the prompt cache. The reference line is the run of
2026-09-13 on the same prompt, `vitesse-qwen27-final-court`.

|                                | Decode     | Prefill     | VRAM       | Spill, end | Acceptance |
| ------------------------------ | ---------- | ----------- | ---------- | ---------- | ---------- |
| qwen27, 2026-09-13, with mlock | 72.8 tok/s | 1,464 tok/s | 15,815 MiB | 1,048 MiB  | 82.1%      |
| qwen27, no mlock               | 72.1       | 1,394       | 15,851     | 650        | 79.8%      |
| **tiel** (Tiel-Coder-35B-A3B)  | **139.3**  | **2,893**   | 15,413     | 620        | 71.3%      |
| qwen36 (Qwen3.6-35B-A3B)       | 137.1      | 2,684       | 15,861     | 620        | 82.1%      |

Three readings, and the first one corrects this file. The 63.2 tok/s quoted in
the 2026-09-13 entry above was an intermediate figure; the final run of that
night, after the launcher was cleaned up, read 72.8 on this prompt, and that is
the number the A3B candidates have to beat.

Dropping `mlock` changes decode by nothing, 72.1 against 72.8, inside the noise
on a bench that loads cold and never returns to a context. It was never meant to
show there. What it changes is the host RAM, read on the same process before and
after: private bytes **28,322 MiB down to 18,787**, peak working set 18,496 down
to 11,918, page file in use 1,370 MiB down to 1,108. Close to ten gigabytes
handed back to the prompt cache, which is where the 350 evictions came from.
The GPU spill also fell from 1,048 to 650 MiB, unexplained and not chased.

**Both A3B candidates roughly double the box.** Decode 139.3 and 137.1 against
72.1, prefill 2,893 and 2,684 against 1,394. They fit the card at the full
262,144 window with no `--n-cpu-moe` and no window cut: 15,413 and 15,861 MiB,
spill 620 MiB, the same order as the dense 27B they replace. That was read on a
6,018-token prompt: in real use qwen36 dropped at 200k tokens, see the section
on real use below. The prediction
made from the 5090 box transposed, and the reason is the one written there: 3B
of 35B parameters work per token, and the attention cache is 3.2x smaller per
token.

The two candidates are level on speed, 139.3 against 137.1 being inside the
spread of a three-run median. Tiel is 448 MiB lighter on the card. Acceptance
splits them, 71.3% against 82.1%, and this repository has said three times now
that acceptance does not decide anything: throughput does, and throughput calls
them equal. Quality has to break the tie.

### Quality, and what stays on the box

`bench/banc.ps1`, the 500 MMLU questions and 60 GSM8K problems every campaign in
this file uses, temperature 0, seed 42, thinking disabled.

|        | MMLU  | GSM8K | Empty | Bench time |
| ------ | ----- | ----- | ----- | ---------- |
| qwen36 | 90.2% | 56/60 | 0     | 27.2 min   |
| tiel   | 85.8% | 55/60 | 0     | 10.9 min   |

The 27B control was stopped at 303 of its 560 requests: its model left the box
the same hour, so the figure would have served no decision.

The 4.4-point MMLU gap sits just under the 4.5 points needed to separate two
models on 500 questions, on a public set this repository already caught
flattering two 9B models by fifteen points (`jeu-inedit-2026-09-11.md`). Bench
time does separate them: qwen36 took two and a half times longer, which at
equal decode speed means much longer answers with thinking disabled. Tiel at
IQ3_XXS scores 85.8% here against 86.6% for the UD-Q4_K_XL build on the 5090 box
on 2026-09-10, so the 3-bit tier costs no measurable quality.

Both stay, since neither wins on everything, and the launchers now ask for the
model and the machine at start-up. The dense 27B left the box: its weights went
to the Windows Recycle Bin on D:, whose quota was read first (46,424 MiB,
`NukeOnDelete` 0). Qwen3.6's embedded template accepts a system message after
the first user turn, checked with a direct request and then through Claude
Code, so it needs no derived template where Qwen3.8-27B's raised on the late
system messages Claude Code injects.

### In real use: a step drop at 200k tokens, and two layers of experts moved to RAM

A Claude Code agent ran on qwen36 for ninety minutes. Two effects show in the
log of its 197 requests, and they are not the same thing.

The wait before the first prefill block grows smoothly with the context: 5.7 s
at 93k tokens, 13.6 s at 150k, 26 s at 200k, then about 1,400 tok/s per block
once it starts. It did not change when decode dropped, so it is a cost of depth,
probably the KVarN cache being unpacked at the start of each prompt. Not proven.

Decode held 74 to 87 tok/s up to 200,099 tokens, then read 20.9 on the very
next request at 200,166, and stayed between 20 and 25 until restart. After the
client compacted to 56k tokens it still read 58 to 66, against 100 to 103 at the
same depth before the drop. The context barely moved across the drop, so an
event caused it, not depth. Ruled out: thermal or power throttling (counters at
zero), any system or display-driver event in the ten minutes around it, any
interactive session on the box. The card sat at 16,084 MiB dedicated of 16,376.
The probable cause, not proven, is Windows moving part of the server's
allocations to system memory once the card was full.

Measured after a fresh restart, same bench, long prompt 112,724 tokens:

| `--n-cpu-moe` | Card after 112k | Decode short | Decode 112k | Prefill 112k |
| ------------- | --------------- | ------------ | ----------- | ------------ |
| 0             | 15,866 MiB      | 145.3 tok/s  | 107.6       | 2,523 tok/s  |
| 2             | 15,424          | 135.9        | 101.9       | 2,154        |
| 3             | 15,146          | 127.1        | 97.3        | 1,977        |

The plan was to keep the smallest setting that emptied the shared GPU memory.
That criterion does not hold: the shared counter read 610 to 620 MiB in all
three runs, fresh or deep, so it counts a fixed allocation and says nothing
about overflow. Margin is read on dedicated memory instead. Two layers buy 442
MiB for 5 to 7% of decode and 15% of prefill, three lose more than 10% of decode
at short context, and qwen36 now runs with `--n-cpu-moe 2`. Whether that ends
the step drop is proven only by a long session that does not repeat it.

Two alternatives were set aside with evidence. `--no-kv-offload` is the dead
end of the 2026-09-13 entry below, host-RAM cache row. Cutting the window to
196,608 frees an estimated 300 MiB, computed from the attention geometry and
not measured, and would make the client compact earlier. The 5090 box's
`-cram 24576` is not a way to unload a card either: it keeps copies of past
conversations in host RAM, while the active one stays on the GPU. `--fit on` was
measured afterwards, in the next section.

### The same evening, benched at depth: the drop does not reproduce

Prompts of 196,613 and 237,502 tokens cut from the llama.cpp sources with
`/tokenize`, same bench otherwise. The card figure is the whole card, of 16,376
MiB.

| qwen36                       | Card after load | Decode short | Decode 196.6k | Prefill 196.6k | Card after 196.6k | Decode 237.5k | Card after 237.5k |
| ---------------------------- | --------------- | ------------ | ------------- | -------------- | ----------------- | ------------- | ----------------- |
| no offload                   | 15,694 MiB      | 145.3 tok/s  | 79.2          | 2,007 tok/s    | 15,890            | 72.5          | 15,918            |
| `--n-cpu-moe 2`              | 15,232          | 135.9        | 68.9          | 1,788          | 15,428            | 62.4          | 15,456            |
| `--fit on --fit-target 1024` | 14,488          | 127.3        | 70.1          | 1,499          | 14,686            | not run       | not run           |

No configuration dropped. Without offload the card still kept 458 MiB at
237.5k tokens and decoded at 72.5 tok/s, where real use had fallen to 21 at
200k: the bench does not reproduce the event, so it neither proves nor rules
out the full-card explanation. What it measures is the price of margin at
depth. Two layers of experts double the margin, 948 MiB against 486 at 196.6k,
for 13% of decode there, more than the 5% read at 112k. `--fit` with a 1 GB
target offloads about 1.2 GB of weights, keeps 1,690 MiB and decodes at 70.1 at
depth, level with two layers, but pays 6% of short decode and 16% of deep
prefill against them. Two layers stay: an agent that re-reads long contexts
pays prefill on every turn. At this verbosity `--fit` logs nothing of what it
chose, only a warning that CPU tensor overrides run with mmap on and that
`--load-mode none` would be faster; that mode is not measured here.

Two protocol points cost a run. The 237.5k prompt shares its first 196.6k
tokens with the 200k one, so its first pass reads the prompt cache and reports
a prefill of about 700 tok/s over 41k new tokens, not comparable and left out
of the table. And `-Extra "--n-cpu-moe 0"` on a profile carrying
`--n-cpu-moe 2` did not remove the offload: both values sat in the command line
and dedicated memory matched the offloaded run to the megabyte. The run was
stopped and redone with the server started directly, and the `-Extra` comment
in both `llm-ctl` scripts now says which flags it cannot override.

### And `--load-mode none` on top of the two layers

The `--fit` run logged that CPU tensor overrides are slower with mmap on.
Measured the same evening, profile otherwise unchanged, same prompts:

| qwen36, `--n-cpu-moe 2` | Decode short | Prefill short | Decode 196.6k | Prefill 196.6k | Card after 196.6k | Host RAM free after 196.6k |
| ----------------------- | ------------ | ------------- | ------------- | -------------- | ----------------- | -------------------------- |
| mmap, as before         | 135.9 tok/s  | 2,518 tok/s   | 68.9          | 1,788 tok/s    | 15,428 MiB        | 5,645 MiB                  |
| `--load-mode none`      | 132.8        | 2,709         | 71.9          | 1,910          | 15,446            | 16,838                     |

Deep decode and prefill gain 4 and 7%, short decode loses 2%, the card does not
move, and 11 GB of host RAM come back. On a 32 GB box that last figure matters
more than the speeds: it is the room the prompt cache was paged out of earlier
the same day. The shared GPU counter rose from 620 to 1,566 MiB, probably the
offloaded experts held in pinned host memory; as measured above, that counter
does not mark an overflow. qwen36 keeps both flags.

---

## 2026-09-13, on the 16 GB box: Qwen3.8-27B with its full 262,144 window, and what it took

The target was the 5090 box's `qwen` model on this card, with nothing cut from its
trained window. Everything below is at `--ctx-size 262144`, measured with
`bench/vitesse.ps1 -Runs 1 -Chars 30000 -Predict 150` (one 6,018-token prompt)
unless stated.

### The cache decides, and the official binary cannot hold it

Only 16 of the 64 layers cache K/V, 32,768 elements per token, so the cache is
about 4.6 GiB in q4_0 at full window. Weights have to leave room for it:

| Weights               | Cache                      | Engine          | Prefill     | Decode                  | Note             |
| --------------------- | -------------------------- | --------------- | ----------- | ----------------------- | ---------------- |
| UD-IQ4_XS, 13.27 GiB  | q8_0 in host RAM (`-nkvo`) | b10908          | -           | 14.5 tok/s, 4.85 at 45k | dead end         |
| UD-IQ3_XXS, 10.18 GiB | q4_0                       | b10908          | 485 tok/s   | 48 tok/s                | spills 1,450 MiB |
| UD-IQ3_XXS            | KVarN 4                    | BeeLlama v0.4.6 | 1,581 tok/s | 46 tok/s                | spills 754 MiB   |
| UD-IQ3_XXS            | KVarN 3                    | BeeLlama v0.4.6 | 1,572 tok/s | 46 tok/s                | 14,378 MiB       |
| UD-IQ4_XS             | KVarN 2                    | BeeLlama v0.4.6 | 61 tok/s    | 15 tok/s                | overflows        |

**The spill column is the one nvidia-smi does not show.** When dedicated VRAM
runs dry, Windows hands the process shared system memory instead of failing, the
server starts, answers, and reads three times slower. It is visible only in the
`\GPU Process Memory(*)\Shared Usage` counter, which `vitesse.ps1` now reports
on its `debordement` line.

The official release builds flash attention for q4_0 and q8_0 only
(`GGML_CUDA_FA_ALL_QUANTS` is OFF upstream, read in `ggml/CMakeLists.txt`), so a
more compact cache is not an option there. BeeLlama ships prebuilt Windows CUDA
13.3 zips with KVarN, deployed exactly like b10908: unzip, no install.

### Speculation needs its own cache compressed too

| Setting                                       | Prefill     | Decode         | Accepted         |
| --------------------------------------------- | ----------- | -------------- | ---------------- |
| KVarN 3, no MTP                               | 1,572 tok/s | 46.3 tok/s     | -                |
| **KVarN 3, MTP n-max 2, draft cache KVarN 3** | 1,410 tok/s | **63.2 tok/s** | 86/124           |
| KVarN 3, MTP n-max 3                          | 1,459 tok/s | 61.9 tok/s     | 96/155           |
| KVarN 4, MTP n-max 2                          | 133 tok/s   | 26.7 tok/s     | overflows        |
| q4_0 official, MTP n-max 2, draft cache f16   | 37 tok/s    | -              | spills 3,820 MiB |

The MTP head allocates a draft cache over the same window. Left at its f16
default it pushed 3.8 GB into shared memory. On a 45k-token prompt the retained
setting decodes at 53.0 tok/s and reads at 1,460 tok/s.

### Long prompts: the prefill window was the second spill

With BeeLlama's default `GGML_KVARN_WINDOW_CHUNK=65536`, a 240k-token prompt read
at 288 tok/s once past 76k tokens, with 1,244 MiB spilled. KVarN prefill
materialises transient F16 K/V windows of that many tokens, about 800 MiB each at
this geometry. At 16384 an 87,297-token prompt read at 1,342 tok/s and the needle
at 50% depth was found in 76 s. On a 243,053-token prompt the needle was found at
10, 50 and 90% depth, 3 out of 3, in 278 to 371 s, spill steady at 1,048 MiB.
`Start-LLM` in `llm-ctl-16gb.ps1` takes the variable as `-envVars` from the profile
branch and hands it to the child through `Win32_ProcessStartup`.

**Setting a variable on the calling process does not reach the child**, contrary
to what the 5090 box's `llm-ctl.ps1` states for its `PATH`. Measured on this box
with `cmd /c set` launched through `Win32_Process.Create`: the caller-side variable
came out "not defined", the startup-block one came out set. The first attempt at
this refactor used the caller-side route, and prefill on an 87k-token prompt fell
back to about 235 tok/s, the symptom of the default 65,536 window.

### Through Claude Code, end to end

The `qwen27` launcher, asked to create a file: done in 12 s with `--bare`, and in
95 s with the full global instructions loaded, the file on disk both times.

---

## 2026-09-12, on the 16 GB box: the two profiles brought back in line with qwen, kat and nex

The box was serving a hand-edited `llm-ctl.ps1` that the repository never saw. It
carried `--reasoning off` on both models, on the theory that a thinking block
placed before a `tool_use` block made Claude Code skip the tool call. **That
theory is false**, measured end to end with reasoning back on: NeoHorse wrote
209 characters of reasoning and OxCoder 1,747 on a one-line question, and both
then created the requested file through Claude Code.

What changed, to match the 5090 profiles:

- `--reasoning off` removed from both.
- `--load-mode mlock` and `-cram 12288` added. 12288 rather than 24576 because
  this box has 32 GB of RAM, 19,358 MiB free with the model locked.
- OxCoder pins `--top-k 20 --min-p 0`, the qwen values. Unset, llama.cpp applied
  top_k 40 and min_p 0.05. The card's Claude Code protocol, top_p 1.0 and no
  top_k, was tried and rejected: it produced broken French through the client.
- NeoHorse's derived chat template moved into the repository copy. OxCoder's
  embedded template does not raise on a late system message and needs none.
- Client side, `CLAUDE_CODE_MAX_CONTEXT_TOKENS` goes from 262,144 to 245,760,
  the window minus the output budget, the rule kat and nex follow.

**What still blocks real use is not the server.** Same model, same prompt, same
server: with the global instructions loaded, both models ask for approval and
write nothing; with `--bare`, both write the file on the first try.

---

## 2026-09-11, on the 16 GB box: the window doubles for free, and every road to speculation is closed

**Different hardware.** This campaign ran on an RTX 4080 SUPER, 16,376 MiB, not
the 5090 the rest of this file describes. Two dense 9B models on a Qwen3.5 base,
one served at a time. None of the figures below transpose to the 32 GB box, and
that is exactly why they are worth writing down: the same flags land differently
when the card is half the size.

### The window was set to half of what the weights offer, for no reason

The profile served 131,072 because it had been copied from a working profile.
The GGUF header says otherwise: `qwen35.context_length = 262144`.

| Window      | oxcoder        | neohorse       | Memory     |
| ----------- | -------------- | -------------- | ---------- |
| 131,072     | 80.9 tok/s     | 77.5 tok/s     | 9,822 MiB  |
| **262,144** | **80.9 tok/s** | **77.3 tok/s** | 13,018 MiB |

**Doubling is free here.** That is the opposite of the 5090 box, where a window
increase cost fifty times the decode rate. The lesson is not "windows are cheap",
it is that the cost of a window is a property of the machine and has to be
measured on it.

### Asking for more than the trained context burns memory for nothing

At `--ctx-size 524288` the server still serves 262,144, warns once
(`exceeds the training context of the model - capping`), and **keeps 15,882 MiB
allocated** against 13,018 at the correct setting. Three gigabytes spent on a
window that does not exist. The warning is a single line in a log nobody reads,
and nothing else signals it.

### KV cache quantisation costs nothing in quality, and that was worth proving

The suspicion was that compressing the attention cache degraded long reasoning,
since it approximates exactly what the model has just produced. Measured on 48
reasoning items, same protocol on both sides:

| Cache  | Score     | Speed      | Memory     |
| ------ | --------- | ---------- | ---------- |
| `q8_0` | 34/48     | 80.9 tok/s | 9,822 MiB  |
| `f16`  | **34/48** | 78.0 tok/s | 11,357 MiB |

Identical score. Full precision costs 1,535 MiB and 2.9 tok/s and buys nothing.
The hypothesis is closed by measurement rather than by principle.

### Speculative decoding: four roads, four dead ends

All four measured on oxcoder, at 262,144 window, against a reference of
**80.9 tok/s and 237.0 s over 24 real reasoning items**.

| Setting                                | Speed          | 24 real items | Memory     | Acceptance            |
| -------------------------------------- | -------------- | ------------- | ---------- | --------------------- |
| none (reference)                       | 80.9 tok/s     | 237.0 s       | 12,992 MiB | -                     |
| `draft-mtp`                            | refused        | -             | -          | server will not start |
| `ngram-cache`                          | 80.6 tok/s     | 240.3 s       | 12,923 MiB | **0.00**              |
| draft model, n-max 3                   | **18.1 tok/s** | 478.6 s       | 15,905 MiB | 0.70                  |
| draft model, n-max 6                   | 15.4 tok/s     | 437.8 s       | 15,905 MiB | 0.55                  |
| draft model, n-max 3, draft cache q4_0 | 47.3 tok/s     | 328.7 s       | 15,907 MiB | -                     |

The draft model is `Qwen3.5-0.8B-Q4_0`, 537 MB, verified compatible by reading
the GGUF headers of both: same architecture `qwen35`, same 248,320 vocabulary.

**The draft guesses well and ruins throughput anyway.** Seventy percent
acceptance, and decode falls by a factor of four and a half. The cause is in the
memory column: this build offers no way to give the draft a smaller window, so it
allocates its own cache over the same 262,144 tokens, the card saturates at
15,905 of 16,376 MiB, and what no longer fits spills. Quantising the draft's
cache nearly triples the rate back to 47.3, still 1.7 times slower than no
speculation at all.

This is the same lesson the 5090 box learned in the opposite direction, where
raising acceptance from 27 to 66 percent halved throughput: **what decides is
measured throughput, never the acceptance rate.**

The n-gram variants deserve their own note. Acceptance was flat zero, hundreds of
drafts generated and not one accepted, which costs a measurable 1.4 percent. A
published benchmark rating them highly is measuring a synthetic prompt where
repetition is guaranteed; real reasoning output has none.

**Conclusion for a 16 GB card: a long window and a second model do not coexist.**
The window is the one you keep.

### A quoting trap that reads as an incompatibility

The first draft-model runs failed with `failed to open GGUF file` and the server
exiting. The path was correct and the file was there. The launcher's `-Extra`
splits on spaces and passes each word through, so quotes written around the path
survived to the binary and became part of the filename it looked for. The path
had no space and needed none.

A speculation setting that "is not supported" is worth checking twice before it
is written down as such: here the model was compatible all along.

---

## 2026-09-08: 65,536 tokens of extra window cost a factor of fifty on decode

A session sitting at 372,738 tokens of a 393,216 window could no longer compact: the client kept
answering `summarization produced empty response`. The reading was that no room was left for the
summary to be written, since `n_ctx` counts input and output in one pool. So the window went up to
458,752 on `kat`, and the server took it: 30,527 MiB of 32,607, one gigabyte for the extra 65,536
tokens.

It made things worse, and the log says by how much. Same model, same 372,000-token context, same
single slot:

| Window  | Prefill                 | Decode          |
| ------- | ----------------------- | --------------- |
| 393,216 | cached                  | **97.31 tok/s** |
| 458,752 | 371,799 tokens in 111 s | **1.92 tok/s**  |

Four minutes for 259 tokens, after which the client gave up. The empty response was a client
timeout, not a server refusal.

**The cliff is one gigabyte wide.** At 29,389 MiB the card has 3 GB spare and decodes at a hundred
tokens per second; at 30,527 MiB it has 2 GB and decodes at two. This repository already carried
the warning, in the `qwen` entry: the VRAM cost of a window is not linear, 384k was free on this
card and 512k was not. It was read before the change and tried anyway.

**What the incident does not explain.** Why the compaction failed at 393,216 in the first place,
where decode was measured at 97 tok/s and 20,478 tokens of room remained, is still unknown. The
window went back to 393,216 and the compaction then succeeded, so the working hypothesis is that
the earlier failures were the two-slot configuration in place at that moment, not the window size.
Not proven.

---

## 2026-09-08: four candidate models benched, and the bench itself turns out to measure the wrong thing

Four files pulled the same morning were put against the models in service: `Ornith-1.5-9B-MTP-BF16-ASHQ1-6500`,
`Ornith-1.5-35B-A3B-TIEL_Calibrated-MTPv2-23G-ICE`, `Ornith-1.5-35B-A3B-ONYX-compact`, and
`KAT-Philly-MTP-Q4_K_M` from KAT-Coder-V2.5-Dev-35B-A3B. All four declare `nextn_predict_layers`,
so all four were run with speculation on.

Protocol: b10826, the production `tiel` argument list with only the model path changing, no
projector on any of them including the control, `bench.ps1`, 150,000 characters of real llama.cpp
sources (about 38,000 tokens), 512 tokens, seed 42, three runs, median.

| Model              | Decode       | Prefill     | VRAM       | MTP accepted |
| ------------------ | ------------ | ----------- | ---------- | ------------ |
| **tiel** (control) | 199.71 tok/s | 8,758 tok/s | 30,936 MiB | 56.2%        |
| onyx compact       | **207.19**   | 8,275       | **25,621** | 60.1%        |
| kat-coder          | 197.71       | 8,072       | 29,702     | 52.8%        |
| ice (MTPv2 23G)    | 190.97       | 8,411       | 30,530     | 52.2%        |

And the two 9B, same protocol at the production `ornith` window of 262,144:

| Model                           | Decode       | Prefill    | VRAM           | MTP accepted |
| ------------------------------- | ------------ | ---------- | -------------- | ------------ |
| ornith Q5_K_M, as in production | 167.18 tok/s | **10,721** | **11,648 MiB** | none         |
| ornith Q5_K_M, speculation on   | **179.64**   | 7,715      | 14,179         | 58.4%        |
| ashq1 (the downloaded file)     | 164.25       | 7,743      | 14,357         | 49.0%        |

### The 9B already had the MTP head, and nobody had switched it on

The interesting line above is the middle one, and it is not a new file: it is the model that has
been serving `ornith` all along. Starting it without `--spec-type` prints four warnings that are
easy to walk past:

```
W model has unused tensor blk.32.nextn.eh_proj.weight (size = 23068672 bytes) -- ignoring
```

The production weights carry a draft head, llama-server drops it when no speculation is asked for,
and switching it on is worth 7.5% of decode for 2,531 MB of VRAM and a quarter of the prefill. The
file downloaded to answer that same question, ASHQ1, is slower than the one already on the disk.

**And this repository has said so since 2026-08-31**, in
[../models/qwen3.8-27b/README.md](../models/qwen3.8-27b/README.md): "the `blk.*.nextn.*` tensors
are already inside the quant; llama.cpp loads them and ignores them unless you pass `--spec-type
draft-mtp`. One flag enables speculation, no second file to fetch." Written about `qwen`, true of
every quant that ships those tensors, and never replayed against the other profiles. That is the
actual lesson, and it is worse than the one about checking the disk before downloading: the fact
was already written down, in this repository, by us. A finding about one model is worth a pass over
the others the same day.

### The first request after a start is not a measurement

Every speculation run collapsed on its first request and recovered on the next: 49, 57, 69, 86 and
88 tok/s against 180 to 208 immediately after. Runs without speculation showed nothing of the sort.
Two explanations fit, a cold server or a context the draft head has never seen, and they lead to
opposite conclusions, so a second prompt of the same size was built from a different slice of the
sources and sent to a warm server:

| tiel            | prompt A, cold | prompt A, cached | prompt B, new context | prompt B, cached |
| --------------- | -------------- | ---------------- | --------------------- | ---------------- |
| speculation on  | 55.82 tok/s    | 200.20           | 190.10                | 191.83           |
| speculation off | 196.96         | 195.98           | 187.63                | 193.15           |

A new context costs nothing. Only the first request after a start does, and only under speculation.
**Discard run 1 of any speculative bench**, and read the header of this file accordingly: "median
of 3 runs" has always meant one cold run plus two that hit the prompt cache, `-cram` being on in
every profile. The median lands on the cached pair, which is the right regime to read since Claude
Code reuses its prefix, but it is not what the phrase says.

### The bench prompt is the worst possible case for speculation

Compared like for like on prompt B, warm, speculation buys nothing at all: 190.10 against 187.63,
then 191.83 against 193.15, for 3,710 MB of VRAM and 14% of the prefill. That reading would have
sent the MTP head to the bin. It would have been wrong, and the reason is the prompt: `bench.ps1`
asks for a ten-line summary in French, which is the least predictable text a draft head can be
handed. Four short tasks at temperature 0, same model, same day:

| Task                        | tiel, speculation on | tiel, speculation off |
| --------------------------- | -------------------- | --------------------- |
| write a PowerShell function | **284.3 tok/s**      | 218.2                 |
| read a Python snippet       | **226.4**            | 190.3                 |
| reason in French            | 204.8                | **240.1**             |

Thirty percent on generated code, nineteen on reading it, fifteen lost on French prose. The head
earns its VRAM on exactly the work this box exists for, and `bench.ps1` is blind to it. Every MTP
decision taken on this bench since 2026-09-03, the `n-max` sweep included, was taken on prose.
A code-shaped long-context bench is missing from this repository.

### Quality, four exercises, temperature 0

Same four tasks scored by hand: a C++ out-of-bounds loop, a PowerShell function to write, a
classic fly-between-trains problem, and Python's mutable default argument.

- **tiel** 4/4, **kat-coder** 4/4
- **onyx** 3/4, **ice** 3/4, both failing the same one, the PowerShell function they write does
  not return files

Four questions rank nothing. They are a gate: onyx and ice do not pass it, and their throughput
advantage is not worth reopening.

### What moves

Nothing yet, and `tiel` stayed in production throughout. Onyx leads the decode table by 3.7% and
saves 5.3 GB, which is real, but it fails a four-question gate that the incumbent passes, and the
table it leads measures the wrong regime. Kat-coder matches tiel everywhere and beats it on
generated code, 295.1 tok/s against 284.3, which makes it the only candidate worth a real trial.
Ice is out on both counts. The 9B question is answered without a download: the head is already
there.

---

## 2026-09-08: Ornith moves to b10826, which buys nothing, and a launcher trap bites twice

### The build change is neutral on Ornith, and the profile moved anyway

`ornith` was the last profile owing itself to `llama-cpp-20260827`, the NVFP4 build, although its
weights are Q5_K_M and never touched an NVFP4 kernel. Control against `llama-cpp-b10826` with the
production argument list unchanged, only the binary varying: `bench.ps1`, 37,981-token prompt of
real llama.cpp sources, 512 tokens, seed 42, temperature 1.0, thinking off, 3 runs, median decode.

| Build      | Decode       | Prefill      | VRAM       | Window  |
| ---------- | ------------ | ------------ | ---------- | ------- |
| 2026-08-27 | 166.32 tok/s | 10,682 tok/s | 12,696 MiB | 262,144 |
| b10826     | 167.44 tok/s | 10,796 tok/s | 12,697 MiB | 262,144 |

0.7% and 1.1%: noise on three runs. `draft_n` is absent on both, as expected, no MTP file exists
for this model. The vision projector loads on both. A reasoning control (two trains, relative
speed) answers correctly in French on b10826, so the chat template travels fine.

**Moved regardless, and the reason is not speed.** Seven build directories sit on the disk, four
of them in service (see [building-llama-cpp.md](building-llama-cpp.md)), and each has to earn its
keep. `llama-cpp-20260827` now justifies itself through `qwen` alone, which
genuinely needs the NVFP4 kernels; b10826 is an official binary, unzipped, not a local
compilation. One fewer profile depending on something we compiled ourselves.

### The profile-to-build pairing now lives in one table

Six switch branches each quoted their binary by hand at the `Start-LLM` call site, while the same
script already resolved the port through a `$ports` table. The pairing moves far more often than
the port does: `tiel` on 2026-09-06, `ornith` on 2026-09-08. A `$builds` table now holds it, one
row per profile, and `Start-LLM` reads it when no binary is passed explicitly. Bench launchers
still pass one, which is how a profile is run against another build without editing this file.

Proved by execution, not by reading: each of the four distinct binaries was started through the
table and the running process path checked. `embed` came up on the frozen turboquant build,
`qwenu` on upstream, `qwen` on the 2026-08-27 build, `ornith` and `tiel` on b10826.

### `set "PATH=..." && ...` inside `cmd /c` swallows the whole command line, again

`bench-launch.ps1` still carried the pattern that `Start-LLM` documents against since 2026-09-01:
`cmd /c` strips the outer quotes, `set PATH=<value> && <rest>` then absorbs everything after it
into the variable's value. Nothing runs, no log file is created, and `Win32_Process.Create` still
returns 0: the launch reports success and the server never exists. It cost one run here before
the empty 2-byte log gave it away. Both bench launchers now set `$env:PATH` on the PowerShell
process and let the child inherit it, like production does.

### The repository copy of `llm-ctl.ps1` had drifted from the machine

Comparing the two before deploying showed the repository copy was **behind on code and ahead on
comments**. It was missing the 2026-09-05 fix that removes `--load-mode` from `embed` (the frozen
turboquant build dies on that flag) and the dynamic profile list in the `NO_INSTANCE` message,
while carrying better-written comment blocks for b10826 and for Tiel's draft depth. Deploying the
repository file as-is would have broken the embedder. Reconciled in both directions on 2026-09-08:
the machine keeps the code, the repository's comments were merged in, and the two files are now
byte-identical modulo line endings. Worth a check before any future deploy from the repository.

---

## 2026-09-06: Tiel on the official b10826 binary, two slots, and a strict-instruction bench

### The official b10826 binary is neutral in decode and +5% in prefill

Posted flat into `llama-cpp-b10826` from the release zip plus its cudart, no compilation, the
same way as b10740 on 2026-09-01. Control on the `tiel` profile, 65,615-token synthetic code
prompt, 400 tokens forced, seed 42, temperature 0.6, prompt cache off, 3 runs, median:

| Build               | Prefill         | Decode      | VRAM       | MTP counters |
| ------------------- | --------------- | ----------- | ---------- | ------------ |
| 2026-08-27 (b10643) | 8,724 tok/s     | 211.3 tok/s | 31,707 MiB | 339 / 229    |
| **b10826**          | **9,155 tok/s** | 210.8 tok/s | 31,550 MiB | 341 / 228    |

Confirmed in real use by the owner: a 41,264-token opening turn read at 9,950 tok/s against
9,496 the day before. Two startup notices to know: `preserve_reasoning` is on by default since
b10763 (may lengthen prompts, `--no-reasoning-preserve` turns it off), and the server recommends
`--image-min-tokens 1024` for this vision model, which the profile does not carry yet.

### Two slots are worth it for two callers, four are not

`--parallel N --kv-unified` on b10826. Without `--kv-unified` the window is split between slots
while `/props` still announces the total (measured 2026-09-02 on the Vulkan box). Pure generation,
tiny prompts, 400 tokens forced per stream, one process with one thread per stream:

| Streams | Per stream           | Total |
| ------- | -------------------- | ----- |
| 1       | 260 tok/s            | 260   |
| 2       | 202 + 188 tok/s      | 390   |
| 4       | 97 to 104 tok/s each | 400   |

With 40,000-token prompts arriving together the picture changes: two streams finish in 11.8 s,
exactly the time of two sequential requests, and each stream drops to 15 to 50 tok/s while the
other reads its prompt. Four streams finish in 30.9 s against 23 s queued: slower than no
parallelism at all. A single stream on four idle slots loses 3% (205 against 211).

Retained: `--parallel 2 --kv-unified`. VRAM 31,538 MiB at rest, 32,028 MiB under two-stream
load (580 MiB headroom). Four slots rejected.

### Temperature 0.6 instead of 1.0, trial

Ornith's model card recommends 0.6 for general use and reserves 1.0 for reproducing its
benchmarks. Claude Code sends no temperature (verified by capturing a request: only `thinking`,
`output_config.effort` and `max_tokens` are sent, none of which llama-server maps to a
reasoning budget), so the server value is what every session runs at. Set on 2026-09-06 as a
trial on real usage; the strict-instruction bench below could not discriminate because it passed
at 1.0.

### A strict-instruction bench passes at 1.0, so the reported misbehaviour is elsewhere

12 strict-format prompts (single word, exact JSON, four-line list, code without comments, banned
word) on `/v1/chat/completions`, seed 42: 36/36. 8 file-editing tasks through Claude Code (rename
a variable, create a file, replace a word, answer with one word): 8/8 in a fresh session, 7/8
after a forced 55,000-token read placed before the instruction, the miss being an ambiguous
prompt. The only "failure" seen came from this workstation's own UserPromptSubmit hook, which
demands an acknowledgment line and contradicts "answer OK only": the model obeyed the hook.

Read from the GGUF: Tiel embeds the Sharp chat template `qwen3.8-froggeric-v22.4.0` with a
force-appended terseness system prompt (`terse` kwarg, default true), thinking on and reasoning
effort `medium` by default. Ornith's own template was believed to raise on a late system message
like Qwen's; on 2026-09-19 it turned out to drop it silently instead, see the section of that date.

---

## 2026-09-01: the context ceiling was lifted to 384k, and 512k was rejected

### `--override-kv` is what actually raises the window

The 2026-08-28 entry below closed on a rule: only raise `--ctx-size` with an
`--override-kv` that genuinely extends the window. That is what was done here.
`--override-kv qwen35.context_length=int:393216` lifts the value the GGUF
declares, `--ctx-size 393216` then sizes both the slot and the buffers on it.
`/props` returns `default_generation_settings.n_ctx = 393216` and the log prints
`n_ctx_slot = 393216` with no capping line.

### The cost of window is not linear, and that is the finding

Same 50,480-token prompt of real prose, 800 tokens forced, fixed seed, cold
prefill on a fresh process each time, override in place:

| Window      | VRAM          | Decode          | Prefill         |
| ----------- | ------------- | --------------- | --------------- |
| 262,144     | 27,110 MB     | 123.6 tok/s     | 4,007 tok/s     |
| **393,216** | **31,291 MB** | **122.5 tok/s** | **4,035 tok/s** |
| 524,288     | 31,858 MB     | 94.2 tok/s      | 2,308 tok/s     |

Half again as much window costs **4.2 GB of VRAM and nothing else**, both
throughput figures inside the noise. Doubling it costs a quarter of the decode
and 43% of the prefill.

At 524,288 throughput also stops being **reproducible**, which is its own signal.
Six cold runs spread from 71.9 to 94.9 tok/s decode and 1,716 to 2,333 tok/s
prefill; 262,144 and 393,216 each held within 1% across three runs. A profile
whose numbers will not repeat is a profile sitting on a wall.

### The wall is between 31.3 and 31.9 GB, not at 29

The `q4_0` cache entry had put the throttling threshold around 29 GB. That was
the point where a heavier KV cache started costing, not a hard edge: 31,291 MB
runs at full speed here. The edge is narrower and higher than we thought, and it
is worth knowing because it leaves 1,316 MB free. This profile now has no room
for another GPU tenant.

### What is NOT proven

Recall past 262,144. A window the server accepts says nothing about what the
model still finds in it, and 262,144 is where the model was trained. A
needle-in-a-haystack run above 300k was attempted the same day and abandoned when
the client dropped the connection at 58% of the prefill; the server logged a
clean task cancellation and stayed up. Two things were learned from the attempt
anyway: prefill decays badly on very long prompts, from 1,203 tok/s at 143k down
to 796 tok/s at 233k, and a 400k prompt therefore needs a client that will hold a
connection for ten minutes. Treat the top third of the window as unproven.

### The client value moved in the same commit

`CLAUDE_CODE_MAX_CONTEXT_TOKENS` is now 393216 in the launcher. It has to move
with the server value, always: a client promised more than the server serves is
truncated server-side with no warning.

---

## 2026-08-31: the n-max sweep was measured on short prompts only

### Every sweep before this one used a single, short prompt

The 2026-08-27 entry below settled on `--spec-draft-n-max 3`, and the 2026-08-18
entry settled on 2. Both were measured on one prompt of 10,608 tokens. Neither
asked what happens at the context length this box actually serves.

Re-swept at **both** empty and full (150k) context, on two distinct workloads,
3 seeds, median decode tok/s:

| n-max | reasoning / empty | reasoning / 150k | code / empty | code / 150k |
| ----- | ----------------- | ---------------- | ------------ | ----------- |
| 2     | .                 | 71.01            | .            | 65.06       |
| 3     | 152.50            | 76.90            | **139.24**   | 66.19       |
| **4** | **169.83**        | **83.03**        | 129.88       | **71.21**   |
| 5     | 165.28            | 79.81            | 125.47       | 73.82       |
| 6     | 159.20            | 79.38            | 118.33       | 61.05       |
| 8     | 125.22            | 69.10            | 100.58       | 60.92       |

`n-max 4` wins three cases out of four, by 7.6 to 11.4%, and loses only on code
at empty context. That is the least representative case here: under an agentic
client the context is never empty, the system prompt alone exceeds ten thousand
tokens on the first turn. Applied to the `qwen` profile. `qwenu` was left at 3,
it has not been re-swept.

Why the law inverts: at full context, decoding **one** token costs far more,
since attention sweeps the whole context. Verifying several tokens in a single
pass therefore amortises a longer draft, whereas at short context the draft
dominates the cost.

> Acceptance rate falls monotonically as n-max rises, so it is not the criterion.
> Only throughput is. At 63.3% acceptance, n-max 3 yields less than n-max 4 at
> 55.1%.

### `iq4_nl` on the KV cache: same size, 150x slower prefill

`iq4_nl` occupies exactly as much as `q4_0`, 4.5 bits per element, with a
non-linear table and therefore better fidelity on paper. It was applied to both
Qwen profiles and caught mid-benchmark:

| KV cache type | Prefill at 150k |
| ------------- | --------------- |
| `q4_0`        | ~4,000 tok/s    |
| `iq4_nl`      | **25.9 tok/s**  |

The Flash Attention CUDA kernels do not cover this type and the engine falls
back to a slow path. Reverted the same session.

> Cache size tells you nothing about cache speed. Only the compiled kernel set
> decides. Check FA support before trading one quant type for another.

### `--reasoning-preserve` does nothing here

The server suggests it at load time: `chat template supports preserving
reasoning`. Tested across all four combinations, same 3-turn conversation, same
seed:

| Client resends reasoning | Server flag | Prompt at turn 3 |
| ------------------------ | ----------- | ---------------- |
| yes                      | off         | 2,846            |
| yes                      | on          | 2,846            |
| no                       | on          | 219              |
| no                       | off         | 219              |

The flag changes nothing in either direction. What carries the reasoning across
turns is the client resending `reasoning_content`, not the server. Not retained.

### Reference figures that were missing: context length dominates everything

| Workload  | Empty context | 150k context |
| --------- | ------------- | ------------ |
| reasoning | 169.83 tok/s  | 83.03 tok/s  |
| code      | 129.88 tok/s  | 71.21 tok/s  |

Prompt length roughly halves throughput, far beyond what any flag returns. The
counterpart is that the prompt cache earns its keep: switching workload on the
same 150k context re-prefilled **2,046 tokens instead of 149,706**, about 70
seconds saved.

---

## 2026-08-28: the NVFP4 migration and two settings that were wrong all along

### NVFP4 replaces Q5_K_XL

The model body moved to NVFP4, LOW tier, a format the RTX 5090 tensor cores
execute natively. Measured against the previous UD-Q5_K_XL, same prompt, same
protocol, **speculation disabled on both sides** to isolate the structural gain
from acceptance noise:

|         | Q5_K_XL     | NVFP4 LOW   |              |
| ------- | ----------- | ----------- | ------------ |
| Decode  | 58.84 tok/s | 70.64 tok/s | +20.1%       |
| Prefill | 3,015 tok/s | 4,584 tok/s | +52.0%       |
| VRAM    | 28.5 GB     | 24.4 GB     | 4.1 GB freed |

With speculation on, median over 5 seeds: 98.46 to 122.79 tok/s (+24.7%). On a
real workload of 60 reasoning problems: 233.1 s to 188.6 s.

**The gain comes from size, which confirms the ceiling is memory bandwidth**:
fewer bytes to re-read per token. The operational corollary is uncomfortable but
clear: raising precision costs speed. A Q8 of the weights would be a net
regression, and it is also why the KV cache stays at q4_0.

**Quality was proven, not assumed.** MMLU over 500 stratified questions, fixed
seed, identical question set, 5-shot, temperature 0: 72.8% against 72.4%, two
questions out of five hundred, well inside the roughly 2-point uncertainty.
GSM8K over 60 problems in thinking mode: **56/60 for both, failing the same
problems**. Needle-in-a-haystack recall verified at 59,622 and 198,625 tokens,
two positions each, 4/4.

The MEDIUM tier was measured and **rejected**: 98.05 tok/s, heavier, MMLU 71.8%.
Dominated on every axis. The repository publishes nine tiers sharing an identical
NVFP4 body, differing only in head precision.

### `--ctx-size` was oversizing buffers on two profiles

Both profiles requested 524288 while the GGUF declares 262144. llama.cpp caps
the window at the declared value, in a single log line, **but still sizes its
buffers on what was requested**. We were paying the memory of a 512k window
without ever having it.

This is one notch beyond the capping trap already known, which concerns the
window and not the memory. Measured with only `--ctx-size` changing:

| Requested | Real window | VRAM    | Decode     | Prefill   |
| --------- | ----------- | ------- | ---------- | --------- |
| 262144    | 262144      | 27.2 GB | **123.03** | **4,241** |
| 524288    | 262144      | 31.9 GB | 99.76      | 2,462     |

Twenty-three percent of decode and 72% of prefill lost for nothing. The defect
predated the NVFP4 migration, so it was already costing on the previous quant.

### The client-side ceiling has to match the server

Two sides, one number. Server: `--ctx-size`. Client:
`CLAUDE_CODE_MAX_CONTEXT_TOKENS`. The client value had been raised to 524288 and
was brought back to 262144 the same day: it promised the client twice what the
server serves, and a session crossing the real ceiling would have been truncated
with no warning.

The authoritative value is what `/props` returns in
`default_generation_settings.n_ctx`, never what you asked for.

### n-gram speculation, all modes rejected

The build exposes `ngram-simple`, `ngram-cache`, `ngram-map-k`, `ngram-mod`,
`draft-eagle3` and `draft-dflash` alongside `draft-mtp`. The four n-gram modes
need no draft file, so they were free to try.

On a synthetic benchmark, `ngram-simple` gave **176.52 tok/s** against 121.70 for
`draft-mtp`. On a real workload it took **358.1 s against 188.6 s**, almost
double.

The benchmark lied because its prompt is a repeated paragraph and because
`ignore_eos` prolongs generation into degenerate text. Two gifts to a method that
predicts by replaying the context. `ngram-cache` was worse still, 69.85 tok/s.

> A benchmark that does not resemble the workload can invert the ranking. This
> one did, by a factor of two, in the direction that looked like a win.

### A published setting for the same card and model did not transpose

A community benchmark recommended `--spec-draft-n-max 4` on this exact quant.
Measured here: **108.66 tok/s against 123.03 at n-max 3.**

That holds for the short prompt it was measured on. At full context the ranking
reverses and n-max 4 wins; see the 2026-08-31 entry above.

---

## 2026-08-27: retuning, two hypotheses disproved

### A speculation sweep without a fixed seed measures nothing

An earlier sweep concluded `--spec-draft-n-max 2` beat 3. Re-run with a fixed
seed, the order inverts: 2 gives 114.90 tok/s, 3 gives 117.75.

The reason is that draft acceptance depends on the text being generated. Without
a seed, every run generates different text and the 2-3-4 neighbourhood sits
entirely inside that noise. With a seed, three runs of the same configuration
return rigorously identical speculation counters, which makes the A/B readable.

| n-max | Throughput       | Acceptance        |
| ----- | ---------------- | ----------------- |
| 2     | 114.90 tok/s     | 64% (449/697)     |
| **3** | **117.75 tok/s** | **54% (495/907)** |
| 4     | 113.53 tok/s     | 45% (516/1128)    |
| 6     | 97.76 tok/s      | 33% (534/1575)    |

Measured on a single 10,608-token prompt. Superseded for the `qwen` profile by
the 2026-08-31 sweep above, which found the ranking inverts at full context.

### Removing the vision projector does NOT gain throughput

The hypothesis was that the vision encoder cost speed and constrained `-ub`.
Measured: 115.70 tok/s without it against 114.90 with, which is noise. It costs
1.3 GB of VRAM and nothing else.

And `-ub 512` was not imposed by it either. At `-ub 2048` the encoder stays
loaded with no penalty; the multimodal buffer only overflows at 4096, where the
card saturates at 31.5 GB and **both** metrics regress.

| `-ub`    | Prefill         | Decode           | VRAM        |
| -------- | --------------- | ---------------- | ----------- |
| 512      | 3,106 tok/s     | 117.08 tok/s     | 29.5 GB     |
| **2048** | **3,252 tok/s** | **117.75 tok/s** | **30.7 GB** |
| 4096     | 2,582 tok/s     | 111.81 tok/s     | 31.5 GB     |

### `--min-p 0`: the publisher's calibration, silently overridden

The model card calibrates thinking mode at `temperature 1.0 / top_p 0.95 /
top_k 20 / min_p 0.0`. **`min_p` is zero.**

llama.cpp imposes `min_p = 0.05` by default when nothing sets it, which clips the
tail of the distribution **on top of** the already-calibrated top-p and top-k,
with no message anywhere. `/props` confirmed it.

Same family of trap as a hard-coded `temperature=0.3` inherited from another
model, which had been quietly degrading a different profile for weeks.

### The build was not the limiting factor

Covered in [building-llama-cpp.md](building-llama-cpp.md). Two recompiles,
two null results, and a build kept for nothing that turned out to be
indispensable three weeks later.

### What the whole campaign was worth

**2.5% in generation and 4.7% in context reading.** The configuration from nine
days earlier was already good. Worth knowing before opening a tuning campaign:
on a bandwidth-bound workload, the remaining margin is structural. Neither a
setting nor a llama.cpp version will unlock it; only a smaller model in memory
would, at the cost of quality.

---

## A note on single-seed throughput numbers

With speculation active, the previous quant varied from **68.44 to 139.07 tok/s
on seed alone**, and one seed in five hit an end-of-sequence on the first token,
so a throughput of zero. A number quoted from a single seed is not wrong, it is
fragile.

Two valid protocols: speculation disabled, which returns a measurement stable to
0.3%, or a median over five seeds minimum. The NVFP4 quant turned out markedly
more regular than the old one, 104 to 141 against 68 to 139.
