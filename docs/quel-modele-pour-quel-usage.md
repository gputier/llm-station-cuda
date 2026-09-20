# Quel modèle pour quel usage

Établi le 10/09/2026 à partir de la [campagne de mesures](campagne-mesures-2026-09-10.md).

**Deux colonnes, et il faut lire laquelle avant de décider.** Ce qui est MESURÉ
sur cette machine, et ce qui est DÉDUIT d'une mesure voisine. Les déductions sont
raisonnables et elles restent des déductions.

## Ce qui est réellement mesuré, et ce qui ne l'est pas

Mesuré : la connaissance générale, cinq cents questions MMLU sur vingt-cinq
matières. Le raisonnement arithmétique, soixante problèmes GSM8K. La vitesse de
génération et d'ingestion. La mémoire occupée. Le rappel en contexte long, sur
`nex` uniquement.

**Pas mesuré, et ce sont deux trous importants.** L'usage agentique, c'est-à-dire
tenir un dialogue de plusieurs dizaines de tours, appeler des outils avec les bons
arguments, ne pas perdre le fil : rien de tout cela n'apparaît dans un
questionnaire à choix multiples. Et l'écriture de code, qu'aucune des 560
questions ne teste.

Le classement ci-dessous en tient compte : là où la mesure manque, c'est écrit.

## Le tableau, par usage

| Usage                     | Modèle                                               | Fondé sur                       |
| ------------------------- | ---------------------------------------------------- | ------------------------------- |
| Connaissance générale     | `nex` et `tiel`, 86,6 %                              | **mesuré**                      |
| Raisonnement arithmétique | `kat`, 60/60                                         | **mesuré**                      |
| Vitesse pure              | `tiel`, 230,4 tok/s                                  | **mesuré**                      |
| Qualité par octet         | `bonsai`, 83,6 % en 7,06 Go                          | **mesuré**                      |
| Petite empreinte          | `ornith`, 83,4 % en 15,5 Go                          | **mesuré**                      |
| Contexte long prouvé      | `nex`, 6/6 à 243 969 jetons                          | **mesuré**                      |
| Agentique                 | `muse`                                               | déduit, voir plus bas           |
| Écriture de code          | `tiel` ou `kat`                                      | déduit, voir plus bas           |
| Vision                    | `nex`, `tiel`, `muse`, `bonsai`, `bonsai2`, `ornith` | non testé, projecteurs présents |
| Lecture d'un long fil     | `bonsai2`, 3 347 jetons par seconde en ingestion     | **mesuré**, qualité non mesurée |

## Les recommandations, une par usage

**Coder au quotidien : `tiel`.** Il est premier ou co-premier en connaissance,
le plus rapide de tous à 230,4 tok/s, et il passe le banc en 3,5 minutes là où
les autres mettent trois à sept fois plus. Sa tête MTP lui donne une accélération
qu'aucun candidat ne peut égaler. Réserve honnête : son GSM8K est le plus faible
des modèles de tête, 53/60, et l'écriture de code n'est pas mesurée ici.

**Raisonner, calculer, vérifier : `kat`.** Seul modèle à faire 60/60. Deux points
de connaissance sous `tiel` et nettement plus lent au banc, 9,1 minutes contre
3,5. À sortir quand la justesse du raisonnement prime sur le débit.

**Agentique : `muse`, mais sans preuve.** Il tient ce rôle depuis l'origine sur
cette machine, il a la vision et un OCR fidèle là où `qwen` déforme les
identifiants. Sa remesure du jour le place troisième en connaissance, 85,0 %,
donc il n'est pas disqualifié. Mais **aucune mesure agentique n'a été faite**, ni
sur lui ni sur ses concurrents. Sa place tient à l'usage et à l'habitude, pas à
un chiffre.

**Le plus fort en qualité brute : `nex`.** Premier ex aequo en connaissance,
deuxième en raisonnement, et son score est un plancher, 37 réponses ayant encore
été tronquées. Seul modèle dont le rappel en contexte long soit prouvé. Il paie
en vitesse, 215,1 tok/s sans aucune accélération possible.

**Tenir dans peu de mémoire : `bonsai` d'abord, `ornith` ensuite.** `bonsai`
place 83,6 % et 59/60 dans 7,06 Go de poids, ce qui en fait le seul modèle du
parc laissant assez de place pour un second sur la carte. Il paie en vitesse,
102,6 tok/s. `ornith` fait 83,4 % dans 15,5 Go et va deux fois plus vite.

**Relire un long fil à chaque tour : `bonsai2`, installé le 18/09/2026.** Il
avale l'invite à 3 347 jetons par seconde là où `bonsai` plafonne à 56, sur le
même banc et la même invite le même jour, pour une génération équivalente. Un
client agentique relit toute la conversation à chaque tour, donc c'est ce
chiffre-là qui décide entre les deux générations. Réserve, et elle est sérieuse :
sa qualité n'est pas mesurée ici, il n'a pas passé la campagne du 10/09, et les
83,6 % du tableau restent ceux de `bonsai`. Tant que le banc n'a pas tourné, le
choix se fait sur la vitesse d'ingestion seule.

**À ne pas utiliser : `spark`.** Dernier partout, 73,0 % et 46/60. Il avait été
pris comme exécutant agentique léger ; à ce niveau, un exécutant qui se trompe ne
fait pas gagner de temps.

**À l'essai : `qwent`, depuis le 19/09/2026.** Meilleur score jamais mesuré ici
sur le jeu inédit avec réflexion, 225/235, devant `qwenu` à 221. Au jeu public,
92,0 % et 56/60, le même soir où `tiel` a redonné exactement ses 86,6 % du
10/09 : le chiffre se compare donc au tableau ci-dessus. Sa limite est la
mémoire, 31,6 Go au chargement, au-dessus du seuil où cette carte ralentit, et
sa vitesse en contexte long n'est pas mesurée. Le détail est dans le
[journal](tuning-log.md) et sous [models/](../models/qwen3.8-27b-twin-turbo-709l/).

**À rediscuter : `qwen`.** 78,8 %, battu de 4,6 points par `ornith` qui a trois
fois moins de paramètres et occupe la moitié de la mémoire. C'est le seul modèle
à mobiliser un moteur compilé pour lui seul, celui qui porte les noyaux NVFP4.

## Ce qu'il faudrait mesurer pour combler les deux trous

**Un banc agentique.** Une tâche multi-tours avec appels d'outils, où l'on compte
les appels bien formés, les arguments corrects, et les tours avant abandon. C'est
la mesure qui manque le plus, parce que c'est l'usage principal de cette machine.

**Un banc de code.** Des problèmes à sortie exécutable, jugés en exécutant les
tests plutôt qu'en lisant le code. `quality.ps1` pose déjà quatre questions
ouvertes dont deux de code, mais elles se lisent à l'œil et ne donnent pas de
note.

Tant que ces deux bancs n'existent pas, les lignes « agentique » et « code » du
tableau restent des opinions informées, pas des résultats.
