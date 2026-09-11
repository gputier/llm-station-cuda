# Schéma du jeu d'épreuves inédit

Arrêté le 11/09/2026 après le débat entre les deux concepteurs. Ce fichier fait
autorité : toute épreuve qui ne le respecte pas est rejetée sans discussion.

## Une épreuve, une ligne JSON

Champs communs, tous obligatoires sauf mention contraire.

- `id` : chaîne, unique, de la forme `<famille>-<slug>-<numéro sur 3 chiffres>`,
  par exemple `f7-anchor-001`. Il ne change jamais après écriture, c'est lui qui
  relie une épreuve à ses résultats passés.
- `family` : `F1` à `F7`.
- `q` : l'énoncé complet, en français, y compris la phrase qui impose le format
  de réponse. Le modèle ne doit rien avoir à deviner.
- `answer` : la réponse attendue. Type selon `check`.
- `check` : objet décrivant la correction. Voir plus bas.
- `max_tokens` : entier, toujours `3000`. Relevé de 1500 à 3000 le 11/09/2026, sur mesure et non
  sur intuition. À 1500, six réponses sur quatre-vingt-quinze s'arrêtaient au milieu d'une phrase de
  raisonnement et étaient comptées comme vides. Les réponses complètes de cette passe montaient à
  3978 caractères, les tronquées commençaient à 2966 : le plafond coupait, il ne bornait pas. Un
  plafond trop bas ne pénalise pas au hasard, il pénalise les modèles qui détaillent leur
  raisonnement, ce qui est précisément ce que ce jeu leur demande de faire. Il ne coûte rien à
  relever, un modèle s'arrête quand il a fini.

Champs propres à la famille d'ancrage F7, obligatoires pour elle seule.

- `lure` : la réponse que produit un modèle qui récite le classique au lieu de
  lire l'énoncé. Même type que `answer`. C'est le champ qui donne à ce jeu sa
  preuve directe, une épreuve F7 sans lui n'a pas d'objet.
- `lure_source` : une ligne disant de quel classique l'épreuve dérive et ce qui
  a été changé. Sert à la relecture humaine, jamais au correcteur.

Champ facultatif, posé par la relecture et par personne d'autre.

- `flag` : `piege_partage` quand tous les modèles s'écartent de la clé et que la
  relecture humaine a confirmé que la clé est juste. Ces épreuves restent dans le
  jeu, ce sont les plus discriminantes qu'il contient.

## Les cinq modes de correction, et aucun autre

Un mode qui demande un jugement humain ne peut pas exister ici : le correcteur
est un script.

- `exact_norm` : égalité après normalisation. Minuscules, accents retirés,
  espaces compactés, ponctuation finale retirée.
- `numeric` : extraction des nombres par le motif de `check.pattern`, dans
  l'ordre. `check.tolerance` donne l'écart absolu accepté, `0` pour un entier.
  La virgule décimale est convertie en point avant lecture.
- `set` : ensemble d'éléments, ordre et doublons indifférents.
- `impossible` : le jeton `IMPOSSIBLE` doit apparaître, et
  `check.require_elements` liste les éléments qui doivent être cités avec lui.
  Quand un élément attendu est un nombre, la comparaison se fait sur sa forme
  normalisée : espaces, espaces insécables, points et virgules de séparation des
  milliers retirés, de sorte que `35000`, `35 000` et `35.000` soient le même
  élément. Sans cette règle, un modèle qui a trouvé la contradiction serait
  compté faux sur sa façon d'écrire un nombre, ce qui mesurerait la typographie
  et non le raisonnement.
- `constraints` : liste de prédicats mécaniques dans `check.rules`, tous vrais
  ou l'épreuve est ratée. La grammaticalité n'est JAMAIS notée, aucun script ne
  la décide, et l'énoncé doit le dire au modèle en toutes lettres.

## La règle de lecture de la réponse

La dernière ligne commençant par `Answer:` fait foi, cherchée parmi les trois
dernières lignes non vides. Tolérance délibérée : un format strict mesure
l'obéissance et non la capacité.

Une sortie sans aucune ligne `Answer:`, troncature comprise, est comptée VIDE et
non fausse. Le nombre de vides se lit AVANT le score. Un banc antérieur a noté un
modèle 51,8 pour cent au lieu de bien plus parce que sa sortie était coupée.

## Ce qu'une épreuve doit satisfaire pour entrer

Le critère, et il est unique : un lecteur compétent et attentif trouve la bonne
réponse avec certitude, et cette réponse est démontrable sans convention
implicite.

En sont donc exclues, par construction :

- toute épreuve dont l'échec viendrait du découpage en jetons plutôt que du
  raisonnement, comme compter les lettres d'un mot ;
- toute épreuve où deux règles peuvent s'appliquer sans que l'ordre soit écrit,
  car deux réponses y sont défendables et on mesure la chance ;
- toute épreuve qui mesure la maîtrise du français plutôt que le raisonnement ;
- toute épreuve dont une seconde lecture défendable donne une autre réponse.

## Vérification, quatre étages, aucun facultatif

1. Double implémentation croisée. Pour chaque famille paramétrique, deux auteurs.
   Le premier écrit le générateur, l'énoncé et son solveur ensemble. Le second ne
   reçoit QUE l'énoncé rendu, jamais le code du premier, et écrit son solveur
   depuis la phrase seule. Deux cents jeux de paramètres, comparaison des sorties.
   Tout désaccord est une désynchronisation entre la phrase et la formule, et
   c'est la seule erreur que le code ne peut pas voir seul, puisqu'il EST la
   formule.
2. Résolution humaine à l'aveugle pour F3 et F7, écrites à la main. Désaccord :
   l'épreuve est jetée, jamais arbitrée.
3. Recherche d'ambiguïté sur un échantillon tiré au sort. Une épreuve dont deux
   lectures donnent deux réponses est jetée, même si notre clé est la bonne.
4. Convergence des modèles contre notre clé. Ce n'est PAS un filtre, c'est une
   file de relecture humaine. Sortie binaire : clé fausse, on corrige ou on jette ;
   clé juste, l'épreuve revient marquée `piege_partage`. Rien ne sort par défaut.

Une épreuve ni dérivée par programme ni relue par un humain est non certifiée et
sort du score. Leur nombre se rapporte avant le score, comme les vides.

## Taille et calibration

240 épreuves gardées, environ 350 écrites. Zone de rétention par épreuve : taux
de réussite entre 0,15 et 0,85 sur les modèles de référence. Une épreuve que tous
réussissent ou que tous ratent ne classe personne.

La famille F7 occupe environ 20 pour cent du jeu. C'est la seule qui donne une
preuve directe de récitation plutôt qu'une présomption statistique, et cela
justifie sa part.

## État au 11/09/2026 : 235 épreuves en service

Sept familles écrites, 240 épreuves rédigées, 235 gardées.

    F1  réécriture symbolique        24
    F2  suivi d'état                 24
    F3  problèmes insolubles         24    dont 6 solubles, sans quoi répondre
                                           « impossible » à tout donnerait 100 %
    F4  unités inventées             48    deux auteurs, conversions et change
    F5  génération sous contrainte   48    deux auteurs, structure et forme
    F6  spatial et temporel          44    deux auteurs, 4 retirées
    F7  ancrage sur un classique     23    la seule à porter un leurre

Les cinq épreuves retirées et leur motif vivent dans `epreuves-rejetees.md`. F7
pèse 10 pour cent et non 20 : la cible reste juste, c'est la famille qui demande
encore des épreuves, et ce sont les plus coûteuses à écrire puisque chacune exige
un classique diffusé dont un détail peut être changé sans rendre l'énoncé bancal.

**Pouvoir discriminant mesuré, et c'est le chiffre à surveiller.** Sur les 95
premières épreuves confrontées à deux modèles, 68 étaient réussies par les deux
et 13 ratées par les deux : 14 seulement départageaient quoi que ce soit. Un jeu
peut donc être irréprochable et presque inutile. La zone de rétention ci-dessus
n'est pas une coquetterie, elle est la raison d'être de la calibration, et elle
ne se vérifie qu'après une campagne, jamais à l'écriture.

**Les épreuves ratées par tous se relisent, une par une.** C'est là que se cache
une clé fausse, et le signal est net : quand plusieurs modèles indépendants
donnent LA MÊME réponse différente de la nôtre, c'est la nôtre qui est fausse.
Un désaccord entre eux ne dit rien. Ce contrôle a déjà rattrapé une clé sur 95.

**Mais l'accord entre modèles n'est pas une preuve, c'est une question**, et la
distinction s'est imposée à la passe sur 235. Le signal ne vaut que si les
erreurs sont indépendantes, et elles ne le sont pas quand l'erreur est un piège
classique : trois modèles ont répondu la somme de deux durées là où l'énoncé
fait travailler deux tâches en parallèle, ce qui est une faute canonique que
n'importe qui commet. Leur accord ne disait donc rien sur la clé.

La sortie reste binaire, comme au quatrième étage de vérification. Si la clé est
fausse, on corrige ou on jette. Si elle est juste, l'épreuve revient marquée
`piege_partage` et elle est parmi les meilleures du jeu : elle attrape une erreur
de raisonnement réelle. Ce qui tranche n'est jamais le vote des modèles, c'est de
refaire le raisonnement.

**Deux pièges de ce détecteur, payés le 11/09/2026.** Il ne s'applique pas à la
famille F5, dont le champ de réponse n'est qu'une phrase témoin parmi une
infinité de phrases valables : y comparer les réponses des modèles produit des
signalements qui ne veulent rien dire. Et deux modèles qui donnent deux valeurs
différentes ne sont pas un accord, même si aucun ne donne la nôtre.
