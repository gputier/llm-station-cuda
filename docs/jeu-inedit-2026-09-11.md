# Un jeu d'épreuves que personne n'a vu, et ce qu'il révèle

Campagne du 11/09/2026. Quatre modèles, 235 épreuves écrites ici, jamais
publiées. Ce document raconte pourquoi elles existent, comment elles ont été
vérifiées, ce qu'elles mesurent, et surtout les **quatre défauts de protocole**
trouvés en route, qui sont la partie réutilisable du travail.

Les questions elles-mêmes ne sont pas dans ce dépôt, et la raison est écrite plus
bas. Tout le reste l'est : le correcteur, les outils de vérification, le schéma
qui permet d'en reconstruire un, et les chiffres.

## Le problème, en un chiffre

Deux modèles de neuf milliards de paramètres obtiennent 88,4 et 88,0 pour cent
sur le jeu de questions à choix multiples public. À 500 questions, l'écart
nécessaire pour les départager est de 4,5 points : ils sont indistinguables.

Sur 235 épreuves que personne n'a jamais vues, les mêmes modèles obtiennent 72,8
et 68,1, et l'écart devient lisible.

Quinze points d'évaporation. La question est de savoir d'où ils viennent.

## Ce que le jeu mesure vraiment, et ce qu'il a écarté

L'explication réflexe est la contamination : le modèle a lu les questions à
l'entraînement et les récite. Une famille d'épreuves a été construite pour en
apporter la **preuve directe** plutôt qu'une présomption statistique.

Le principe est simple. On part d'une énigme classique très diffusée, on en
change un détail, et on connaît alors deux valeurs à l'avance : la bonne réponse
au nouvel énoncé, et celle que produit un modèle qui récite l'ancien. Produire la
seconde n'est pas une erreur de calcul, c'est une signature. Le correcteur les
compte à part et ne les mélange jamais au score.

Exemple du principe, sur une épreuve retirée depuis pour une autre raison : une
raquette et une balle coûtent 1,10 ensemble, la raquette coûte 1,00 de plus que
la balle, la balle vaut 0,05. En portant le total à 1,90, la balle vaut 0,45, et
0,05 devient l'empreinte de la récitation.

**Résultat : zéro sur les quatre modèles.** Aucun n'a produit une seule fois la
réponse mémorisée sur les 23 épreuves construites pour ça.

L'évaporation ne vient donc pas d'un entraînement sur les questions. Elle vient
de ce que le format à choix multiples ne mesure pas le raisonnement : on y gagne
des points en éliminant trois mauvaises réponses, ce qui n'est pas la même
compétence que dérouler quinze étapes sans en perdre une.

## Le matériel, qui n'est pas celui du reste du dépôt

| | |
|---|---|
| GPU | NVIDIA GeForce RTX 4080 SUPER, 16 376 MiB, dont 668 pris par Windows |
| Moteur | `llama-server`, build officiel b10908, CUDA 13.3, aucune compilation |
| Modèles | quatre, tous entre 6,0 et 6,7 Go, un seul servi à la fois sur le port 8080 |

Les 16 Go changent ce qui est mesurable : un modèle qui remplit la carte ne
laisse plus de place au contexte. Le modèle le plus gourmand du lot occupe
13 478 MiB, ce qui est en soi un résultat.

## Les sept familles

| Famille | Épreuves | Ce qu'elle demande |
|---|---|---|
| Réécriture symbolique | 24 | Appliquer une règle de réécriture jusqu'au point fixe, en comptant les étapes |
| Suivi d'état | 24 | Tenir trois compteurs sur huit instructions avec plafonds et conditions |
| Problèmes insolubles | 24 | Trouver la contradiction, dont 6 solubles pour piéger qui répond toujours « impossible » |
| Unités inventées | 48 | Convertir et calculer en bases mixtes, taux de change, grandeurs dérivées |
| Génération sous contrainte | 48 | Produire une phrase respectant des contraintes vérifiables par programme |
| Spatial et temporel | 44 | Déplacements sur grille avec bords et obstacles, calendriers imaginaires |
| Ancrage sur un classique | 23 | La famille au leurre décrite plus haut |

Rien n'est repris d'un jeu existant. Les unités, les calendriers, les alphabets
sont inventés, donc rien n'est mémorisable.

**Une famille a été recalibrée après la campagne, et les chiffres de ce document
sont ceux d'avant.** La génération sous contrainte montait jusqu'à six contraintes
simultanées sur seize mots : les quatre modèles y ont fait 2 sur 48, et 40 des 48
épreuves ont été ratées par tout le monde. Mesure de la zone utile, faite après
coup : une contrainte sur huit mots sépare 2 modèles sur 4, deux contraintes sur
huit ou neuf mots en séparent encore, et au delà de dix mots ou de trois
contraintes plus personne ne passe.

Les 40 épreuves hors zone ont donc été réécrites entre six et neuf mots, une à
trois contraintes, et les 8 qui discriminaient sont conservées telles quelles.
**Ces 40 épreuves neuves n'ont pas encore été mesurées ni relues à l'aveugle** :
elles passent le contrôle mécanique et le contrôle à sec, rien de plus. Tout
chiffre de ce document portant sur cette famille décrit l'ancienne version.

## La vérification, quatre étages, aucun facultatif

Sur un jeu inédit, **aucune source extérieure ne peut contredire une réponse
attendue fausse**. C'est la difficulté centrale, et elle commande tout le
dispositif.

1. **Contrôle mécanique** ([tools/verifier-epreuves.py](../bench/tools/verifier-epreuves.py)).
   Chaque épreuve est vérifiée cohérente avec elle-même : le motif d'extraction
   reconnaît sa propre réponse attendue, la phrase témoin satisfait ses propres
   contraintes, les identifiants sont uniques. Ce contrôle a rattrapé 24 épreuves
   d'un coup dont la réponse attendue contenait le préfixe `Answer:`, ce qui les
   rendait toutes infaisables.

2. **Contrôle à sec avant toute dépense de calcul**, intégré au correcteur. Le
   jeu entier est corrigé contre ses propres réponses avant la première requête.
   Deux campagnes avaient été perdues faute de ce contrôle, mortes à la
   vingt-cinquième épreuve sur quatre-vingt-quinze, après deux heures de GPU.

3. **Résolution à l'aveugle par un tiers** ([tools/extraire-enonces.py](../bench/tools/extraire-enonces.py)
   et [tools/comparer-aveugle.py](../bench/tools/comparer-aveugle.py)). Un second
   solveur reçoit les énoncés seuls, jamais les réponses ni le code de l'auteur,
   et résout tout. L'extracteur vérifie qu'aucune valeur sensible ne fuit dans ce
   qu'il produit. Sur 191 épreuves confrontées : 187 accords, 4 désaccords.

   Les quatre désaccords ont été traités sans arbitrage. Une réponse attendue
   plaçait un robot hors d'une grille que son propre énoncé bornait : clé
   corrigée, parce qu'elle était réfutée par l'énoncé et non par une opinion. Les
   trois autres étaient de vraies ambiguïtés, jetées. Une épreuve dont deux
   lectures donnent deux réponses mesure laquelle le modèle a choisie, c'est à
   dire la chance.

   La famille de génération sous contrainte se vérifie autrement, car ses
   épreuves n'ont pas une réponse unique : le solveur produit une phrase depuis
   le seul énoncé, et on la passe au correcteur
   ([tools/verifier-f5.py](../bench/tools/verifier-f5.py)). Si elle échoue,
   l'énoncé et la règle ne disent pas la même chose. Les 48 sont passées.

4. **Relecture des épreuves ratées par tous les modèles.** C'est là que se cache
   une clé fausse, et le signal est net : quand plusieurs modèles indépendants
   donnent **la même** réponse différente de la nôtre, c'est la nôtre qu'il faut
   rouvrir. Un désaccord entre eux ne dit rien.

   Attention, ce signal n'est pas une preuve. Trois modèles ont répondu la somme
   de deux durées là où l'énoncé fait travailler deux tâches en parallèle : une
   faute canonique que n'importe qui commet, donc des erreurs non indépendantes.
   La clé était juste et l'épreuve a été marquée comme piège partagé. Ce qui
   tranche est de refaire le raisonnement, jamais le vote des modèles.

   Ce même étage a en revanche exhumé une clé réellement fausse, décrite plus bas.

## Les résultats

Protocole identique pour les quatre : température 0, graine 42, top-k 20,
top-p 0,95, plafond de 3000 jetons par réponse, la dernière ligne `Answer:` fait
foi, les réponses illisibles se comptent avant le score.

| Modèle | Score | Vides | Leurres | Vitesse | Mémoire |
|---|---|---|---|---|---|
| Modèle de codage 9B | 171/235, 72,8 % | 3 | 0 | 81,4 tok/s | 9 796 MiB |
| Généraliste 9B | 160/235, 68,1 % | 0 | 0 | 77,3 tok/s | 9 795 MiB |
| Généraliste 8B | 120/235, 51,1 % | 74 | 0 | 45,9 tok/s | 13 478 MiB |
| Mélange d'experts 8B | 61/235, 26,0 % | 8 | 0 | 181,5 tok/s | 8 278 MiB |

Comparaisons deux à deux par test apparié de McNemar, seul test valable ici
puisque les modèles répondent aux mêmes épreuves : tous les écarts dépassent le
seuil de significativité, y compris celui entre les deux premiers, qui le frôle.

**Le mélange d'experts est le résultat le plus spectaculaire.** Il annonce 74,2
pour cent sur le jeu public, il obtient 26,0 ici, et ce n'est pas un défaut de
mesure : il répond, avec seulement 8 réponses illisibles. Il est aussi deux fois
plus rapide que tout le reste et le plus léger. La vitesse n'achète rien quand le
raisonnement ne suit pas.

## Le pouvoir discriminant, ou pourquoi la taille du jeu décide de tout

C'est le chiffre le plus utile du document, et il ne se voit qu'après coup.

| Taille du jeu | Réussies par tous | Ratées par tous | **Discriminantes** |
|---|---|---|---|
| 95 épreuves | 68 | 13 | **14** |
| 235 épreuves | 50 | 52 | **133** |

Une épreuve que tous réussissent, ou que tous ratent, coûte le même temps de
calcul qu'une autre et ne classe personne. À 95 épreuves, un écart apparent de
8,5 points entre les deux premiers n'atteignait pas le seuil de significativité.
À 235, les quatre modèles se départagent tous.

Un jeu peut être irréprochable et presque muet. Seule une campagne le dit.

## Quatre défauts de protocole, et c'est la partie qui se réutilise

Chacun a produit un chiffre faux et crédible.

### Le plafond de sortie coupait au lieu de borner

Six réponses sur quatre-vingt-quinze n'étaient pas des abandons mais des phrases
tranchées net. Mesure : les réponses complètes montaient à 3978 caractères, les
coupées commençaient à 2966. Le plafond était à 1500 jetons.

Ce biais n'est pas neutre, il pénalise exactement les modèles qui détaillent leur
raisonnement, c'est à dire ce que ce jeu leur demande. Porté à 3000 jetons, le
premier modèle gagne trois points de score. Relever un plafond ne coûte rien, un
modèle s'arrête quand il a fini.

### Un modèle boucle au lieu de conclure

Un des quatre rend 74 réponses illisibles sur 235. Diagnostic : 66 d'entre elles
dépassent 5000 caractères et répètent la même phrase jusqu'à la troncature. Ce
n'est pas un manque de budget, c'est une dégénérescence, probablement liée au
décodage toujours glouton que le protocole impose alors que son profil de service
demande une température de 1.

Vérifié par mesure plutôt que supposé : en doublant le plafond, il passe de 3 à 7
épreuves réussies sur 24 et ses réponses illisibles tombent de 19 à 14. Le
plafond le pénalisait donc réellement, **et** il boucle. Les deux causes se
cumulent, et son score le sous-estime sans le sauver.

### L'option qui coupe la réflexion n'est pas honorée par tout le monde

C'est le plus coûteux des quatre, et il invalide une partie du classement
ci-dessus.

Le banc envoie `chat_template_kwargs: {enable_thinking: false}`, hérité du banc à
choix multiples où il est justifié. Longueur médiane des réponses sur la famille
de réécriture, même protocole pour les quatre :

| Modèle | Médiane |
|---|---|
| Modèle de codage 9B | 1943 caractères |
| Généraliste 8B | 6230 caractères |
| Généraliste 9B | **12 caractères** |
| Mélange d'experts 8B | **15 caractères** |

Deux modèles ignorent l'option et raisonnent quand même. Deux l'honorent et
répondent au jugé. **Le protocole est identique sur le papier et inégal dans les
faits**, et les deux désavantagés finissent second et dernier.

La mesure juste a été tentée, réflexion active pour tous. Elle échoue autrement :
le raisonnement part dans un champ séparé qui **consomme le budget de jetons**,
et il l'épuise avant d'écrire la moindre réponse. Mesure sur trois épreuves avec
un budget large : 5562, 9509 et 13 978 jetons produits, soit une à deux minutes
et demie par épreuve, et plus de vingt heures pour les quatre modèles. La passe a
été arrêtée après 86 minutes et 170 réponses vides.

**Ce qu'il faut en retenir** : sur un banc de raisonnement, la réflexion se
laisse active, et le plafond de jetons doit être dimensionné pour elle, pas pour
la réponse finale. Le paramètre existe dans le correcteur
([bench/banc-inedit.ps1](../bench/banc-inedit.ps1), `-Reflexion`), il n'est pas
le défaut, et le chiffre qu'il faut budgéter est de l'ordre de 16 000 jetons.

### Une clarification d'énoncé a rendu une clé fausse

Une épreuve avait passé la relecture à l'aveugle. Un solveur l'ayant jugée
ambiguë, l'énoncé a été resserré en ajoutant que les notes étaient entières, et
la réponse a été déclarée inchangée après vérification du champ principal.

Or ce champ ne portait que le mot « impossible », qui ne pouvait pas changer. Les
nombres qui l'accompagnaient, eux, dépendaient de la borne : rendre les notes
entières déplaçait un maximum de 120 à 115. Deux modèles ont ensuite répondu 115
tous les deux, contre la clé, et c'est cet accord qui a révélé l'erreur.

**Une vérification date.** Toute modification postérieure, même présentée comme
une simple clarification, sort du périmètre de la preuve sans que rien ne
l'annonce. Le signal à guetter : une clarification qui déplace une borne, une
unité, un type ou un domaine de valeurs n'en est pas une.

## Ce qui n'est pas ici, et pourquoi

Les 235 épreuves ne sont pas dans ce dépôt, et le `.gitignore` les refuse.

Ce n'est pas une question de confidentialité. **Un jeu de questions publié cesse
de mesurer** : il est absorbé par les entraînements suivants, et un modèle qui a
lu les réponses obtient un bon score sans rien savoir. C'est le mécanisme que ce
document constate sur le jeu public, en ouverture.

Ce qui est publié suffit à en refaire un : le schéma, le correcteur, les quatre
étages de vérification et leurs outils. Ce qui ne l'est pas, ce sont les 235
questions, pour que les chiffres du prochain modèle mesuré ici veuillent encore
dire quelque chose.

## Où vit la vérité

Le correcteur, [bench/banc-inedit.ps1](../bench/banc-inedit.ps1), implémente le
schéma et ne doit jamais en diverger. Son autotest, `-SelfTest`, tourne 21 cas
sans contacter un modèle : un correcteur qui n'a jamais vu de mauvaise réponse
n'est pas connu pour en rejeter une.

La comparaison entre modèles, [bench/comparer-inedit.ps1](../bench/comparer-inedit.ps1),
rend les tests appariés et le pouvoir discriminant du jeu. Ce dernier chiffre
décide s'il faut agrandir le jeu ; il ne se devine pas.
