# Campagne de mesures du 10/09/2026

Neuf modèles passés au même banc, dans la même journée, sur la même machine. Ce
document remplace tous les chiffres de qualité qui circulaient avant lui.

Il raconte aussi quatre erreurs de méthode, parce qu'elles ont chacune produit un
résultat faux et crédible, et que c'est ça qui coûte cher.

## Pourquoi tout a été remesuré

Les scores publiés jusqu'ici, 82,0 % pour `qwen`, 82,2 % pour `tiel`, 73,0 % pour
`ornith`, venaient d'un programme **qui n'existe plus sur la machine**. Il n'était
versionné nulle part. Seul le jeu de questions avait survécu.

Deux nombres obtenus par deux méthodes inconnues ne se comparent pas, et un
classement bâti dessus est de la décoration. Le banc a donc été réécrit, versé au
dépôt sous [../bench/banc.ps1](../bench/banc.ps1), et les neuf modèles y sont
repassés.

La suite a donné raison à cette prudence : `ornith` ne fait pas 73,0 % mais
83,4 %, et `qwen` pas 82,0 % mais 78,8 %. L'ancien banc se trompait **dans les
deux sens à la fois**, il sous-estimait le petit modèle et surestimait le gros.
C'est précisément la comparaison qui justifiait la place de `qwen` dans le parc.

## Le protocole

Cinq cents questions MMLU sur vingt-cinq matières, soixante problèmes GSM8K,
température 0, graine 42, mode réflexion coupé, le même jeu pour tous. Le jeu
vit sur la machine dans `bench\epreuves.jsonl`, 560 lignes.

Quatre choix qui décident de ce que le banc mesure vraiment.

**Deux mille jetons de sortie au QCM, pas huit.** Un plafond n'est pas un coût :
un modèle qui répond en trente jetons s'arrête tout seul et ne paie rien pour la
marge inutilisée. Voir plus bas ce que les deux valeurs précédentes ont coûté.

**La dernière ligne `Answer: X` fait foi.** Un modèle qui raisonne cite des
lettres candidates en chemin ; seule sa conclusion compte. La recherche remonte
donc la réponse depuis la fin.

**Température 0 ici et nulle part ailleurs.** Le banc veut un nombre
reproductible et accepte pour cela le décodage glouton. Un profil de service ne
le fait jamais : Qwen documente que le glouton sur ces poids dégrade la qualité
et part en répétitions.

**Le nombre de réponses vides est rapporté.** C'est le garde-fou qui a rattrapé
trois résultats faux dans la journée.

## Qualité, les neuf modèles

Classement par MMLU. La colonne « vides » est à lire avant le score : un modèle
qui n'a pas fini n'a pas été mesuré, il a été tronqué.

| Modèle | Paramètres | MMLU | GSM8K | Vides | Durée |
|---|---|---|---|---|---|
| `nex` | 35B-A3B | **86,6 %** | 58/60 | 37 | 13,0 min |
| `tiel` | 35B-A3B | **86,6 %** | 53/60 | 0 | 3,5 min |
| `muse` | 30B | 85,0 % | 52/60 | 41 | 48,1 min |
| `kat` | 35B-A3B | 84,6 % | **60/60** | 0 | 9,1 min |
| `bonsai` | 27B ternaire | 83,6 % | 59/60 | 0 | 23,5 min |
| `ornith` | 9B | 83,4 % | 51/60 | 0 | 8,8 min |
| `qwen` | 27B | 78,8 % | 57/60 | 0 | 19,7 min |
| `qwenu` | 27B | 77,0 % | 57/60 | 0 | 25,9 min |
| `spark` | 4B | 73,0 % | 46/60 | 0 | 5,6 min |

Ce qu'il faut en retenir.

**`nex` égale `tiel` en connaissance et le bat en raisonnement**, avec 37
réponses encore coupées. Son score est donc un plancher, pas un plafond : sur les
463 questions qu'il termine il en place 433, soit 93,5 %. C'est le meilleur
modèle de qualité du parc, et il n'a aucune accélération utilisable.

**`bonsai` est le meilleur rapport qualité sur mémoire, de très loin.**
Vingt-sept milliards de paramètres compressés à 1,71 bit chacun, 7,06 Go de
poids, et trois points seulement sous le meilleur. C'est le seul modèle du parc
qui laisserait de la place pour un second sur la carte.

**`ornith`, neuf milliards de paramètres, bat `qwen` qui en a vingt-sept**, de
4,6 points, en occupant un tiers de la mémoire. `qwen` est le seul modèle à
mobiliser un moteur compilé rien que pour lui, celui qui porte les noyaux NVFP4.
Sa place dans le parc est à rediscuter.

**`spark` est dernier partout.** Quinze points sous les modèles de production sur
la connaissance, ce qui est attendu à quatre milliards de paramètres. Dernier
aussi sur le raisonnement, ce qui ne l'était pas : `ornith` avait montré qu'un
petit modèle pouvait raisonner comme un trois fois plus gros, l'espoir était que
Spark répète l'exploit un cran plus bas. Non.

**`muse` remonte de vingt-six points** entre l'ancien plafond de sortie et le
nouveau, de 59,0 % à 85,0 %, ce qui le fait passer de dernier à troisième. C'est
la démonstration la plus brutale de ce qu'un plafond trop serré fait à un
classement, et il lui reste 41 réponses coupées.

**Les durées disent autre chose que les scores.** `tiel` passe le banc en 3,5
minutes, `qwenu` en 25,9. Sept fois plus lent pour six points de moins.

## Vitesse et spéculation

Invite de 45 000 jetons, point d'entrée conversationnel, graine 42, trois passes,
médiane de la génération. L'ingestion ne se lit qu'à la première passe, ce point
d'entrée gardant son cache d'invite.

| Modèle et réglage | Génération | VRAM |
|---|---|---|
| `tiel` avec sa tête MTP, production | **230,4 tok/s** | 31 784 MiB |
| `nex` sans spéculation | **215,1 tok/s** | 27 089 MiB |
| `tiel` sans spéculation | 198,3 tok/s | 28 199 MiB |
| `tiel` MTP + ngram-cache empilés | 147,6 tok/s | 31 784 MiB |
| `nex` avec ngram-cache | 110,4 tok/s | 27 090 MiB |
| `tiel` avec ngram-cache seul | 103,8 tok/s | 28 197 MiB |
| `bonsai` sans spéculation | 102,6 tok/s | |

Trois conclusions, dont deux contre-intuitives.

**La spéculation par motifs divise le débit par deux en usage conversationnel.**
Sur les deux modèles essayés. Chaque motif proposé est rejeté et chaque rejet se
paie.

**Empiler deux mécanismes coûte 36 %.** `--spec-type` s'accumule au lieu de
remplacer : demander un type sur un profil qui en a déjà un fait tourner les deux,
et ils se disputent les mêmes candidats. Le lanceur a gagné un `-NoSpec` pour
pouvoir remplacer au lieu d'empiler.

**Le brouillon DFlash est un piège coûteux.** Mesuré à 316,7 tok/s sur le point
d'entrée brut, il divise l'ingestion par trois, de 11 486 à 3 703, et coûte
4,6 Go : son fichier de 392 Mio traîne un cache dimensionné sur la fenêtre
entière, ne laissant que 487 Mio de marge sur la carte. Son coût n'est pas sa
taille, c'est sa réserve.

Conséquence pour `nex` : il n'aura pas d'accélération. Sa propre tête MTP est
déclarée dans sa configuration et absente de ses poids, le brouillon externe
coûte trop cher, les motifs le ralentissent de moitié. Il tient quand même la
comparaison avec `tiel` privé de sa tête, 215,1 contre 198,3.

## Les quatre erreurs de méthode, et ce qu'elles ont coûté

Elles ont toutes produit un résultat faux et crédible. C'est le seul type
d'erreur qui compte.

**Huit jetons pour une question à choix multiple.** Le raisonnement paraissait
solide, la réponse attendue tient en une lettre. `nex` s'est fait couper sur 220
questions sur 500 et a été noté 51,8 %, sous un modèle dix fois plus petit. Une
épreuve doit mesurer le sujet, pas son obéissance à un format.

**Six cents jetons, ensuite.** Corrigeait `nex` à moitié et laissait `muse` à 181
réponses coupées sur 500, noté 59 % quand il place 92,5 % de ce qu'il termine. Les
deux valeurs avaient été serrées par crainte de la durée, crainte qui ne survit
pas à la phrase « un plafond n'est pas un coût ».

**Le mauvais point d'entrée pour la vitesse.** Une invite brute sans gabarit de
discussion : `tiel` a ingéré 44 801 jetons puis émis une fin de séquence
immédiate, un jeton produit, 0,0 tok/s. Le modèle allait bien, un modèle
d'instruction lit un bloc de code terminé comme terminé.

**Une mesure qui ne ressemblait pas à l'usage.** La plus coûteuse, parce qu'elle a
fait poser un réglage en production. La spéculation par motifs mesurée sur une
complétion de code brut donnait +92 %, et c'était vrai : compléter du code, c'est
recopier des structures déjà présentes. Reprise sur le point d'entrée
conversationnel, elle divise par deux. **Un banc doit ressembler à l'usage, sinon
il mesure le banc.**

## Deux profils écrits de travers, corrigés

`nex` et `spark` ont tourné leurs premières heures avec un échantillonnage
recopié de la forme des profils Qwen, sans que la carte de chaque modèle soit
ouverte. `nex` demande température 0,7 et top-k 40, il tournait à 0,6 et 20.
`spark` demande le filtre top-k **désactivé**, `top_k: -1` dans son
`generation_config.json`, il tournait bridé à 20.

## Température : 0,3 gagne, et la contrepartie est visible

Mesuré sur `tiel`, top-k 20 constant, même jeu de 560 questions à chaque fois.

| Température | MMLU | GSM8K |
|---|---|---|
| 0 | 86,6 % | 53/60 |
| **0,3** | **88,0 %** | 53/60 |
| 0,6 | 86,2 % | 54/60 |
| 1,0 | 85,0 % | 55/60 |

Le réglage posé à la main sur la machine est donc le meilleur des quatre en
connaissance : il gagne 1,4 point sur le décodage glouton et 3 points sur la
valeur par défaut du modèle. L'intuition qui l'avait fait choisir était juste.

La contrepartie apparaît dans l'autre colonne, en sens exactement inverse : le
GSM8K monte de 53 à 55 quand la température monte de 0 à 1. Une basse température
sert la connaissance factuelle, où il n'y a qu'une bonne réponse, et dessert
légèrement le raisonnement en chaîne, où le modèle a besoin de pouvoir changer de
piste. Sur du code, l'arbitrage penche du bon côté.

Ne jamais descendre à 0 pour autant : le glouton fait ici PERDRE 1,4 point, et
Qwen documente qu'il produit des répétitions sans fin sur ces poids.

## Le top-k ne sert à rien à cette température

Mesuré sur `tiel` à température 0,3, quatre largeurs de filtre, même jeu de 560
questions :

| top-k | MMLU | GSM8K |
|---|---|---|
| 0, filtre désactivé | 88,0 % | 53/60 |
| 20, valeur de tous les profils | 88,0 % | 53/60 |
| 40 | 88,0 % | 53/60 |
| 64 | 88,0 % | 53/60 |

Quatre fois le même nombre, à la question près. Les trois valeurs ont bien été
transmises jusqu'au banc, vérifié dans le protocole écrit par chaque fichier de
résultat, ce qui n'était pas une précaution superflue après deux défauts de
passage d'arguments dans la même journée.

L'explication tient au cumul des filtres. À 0,3 la distribution est déjà très
piquée, et `top_p 0.95` a coupé la queue avant que le top-k n'ait quoi que ce
soit à faire : les jetons au-delà du vingtième ont une probabilité négligeable,
les garder ou les jeter revient au même.

Conséquence pratique : **un paramètre de moins à régler**. Aligner le top-k sur
la carte de chaque modèle reste correct par principe, mais n'attendez rien de
mesurable tant que la température reste basse. Le résultat pourrait changer à
température 1,0, où la queue de distribution pèse encore quelque chose ; ce
n'est pas mesuré.

## Le rappel de `nex` tient jusqu'à la fenêtre annoncée

Aiguille dans la botte de foin, remplissage de code source réel et varié, phrase
arbitraire plantée à 10, 50 et 90 % de profondeur.

| Longueur du contexte | Résultat |
|---|---|
| 176 080 jetons | 3/3 |
| **243 969 jetons** | **3/3** |

Six sur six. La seconde longueur est le chiffre qui compte : elle passe juste
sous les 245 760 jetons que le lanceur annonce au client. Ce que la station
promet est donc tenu, et ce n'était pas acquis.

Deux inquiétudes levées au passage. Le défaut d'arrêt silencieux en contexte long
qui frappe cette famille, issue 27756, ne s'est pas manifesté : le modèle répond
normalement à 244 000 jetons. Et l'ingestion de ce contexte prend 47 secondes,
un coût réel mais supportable pour une session qui va vivre longtemps.

Ce que ce résultat ne dit pas : il mesure la capacité à retrouver **une** phrase
exacte, pas à raisonner sur l'ensemble du contexte. Un modèle peut retrouver une
aiguille et rester incapable de synthétiser ce qui l'entoure.

## Ce qui reste à mesurer

La température sur `kat`, `nex` et `spark`, en cours. Le script est
[../bench/banc-sampling.ps1](../bench/banc-sampling.ps1) et il balaie un facteur
à la fois.

Le rappel en contexte long de `spark` et `bonsai`. Celui de `nex` est prouvé,
voir plus haut.

La vision de `nex`, assise sur un défaut ouvert de llama.cpp qui fait tomber le
serveur quand texte et image alternent.

Et `bonsai` en cohabitation, la seule piste que ses 7 Go rendent crédible.
