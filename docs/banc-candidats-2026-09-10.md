# Banc des trois candidats, protocole

Date de rédaction : 10/09/2026. Périmètre : `nex`, `spark`, `bonsai`, les trois
modèles installés ce jour. Aucune mesure n'a encore été prise : ce document est
le plan, pas le résultat.

## Ce qui doit être vrai avant de lancer quoi que ce soit

La machine a reçu une mise à jour de pilote et de boîte à outils CUDA 13.4.1 le
10/09/2026, et un redémarrage. Trois choses se vérifient dans cet ordre, et un
échec sur l'une arrête tout le reste.

**Le chemin CUDA du lanceur existe encore.** `llm-ctl.ps1` ajoute au PATH un
dossier écrit en dur, `v13.3\bin` pour neuf profils sur dix et `v12.8\bin` pour
l'embedder. Si l'installeur a remplacé la 13.3 au lieu de l'installer à côté,
ces dossiers ont disparu et le script ne s'en plaint pas : il ajoute au PATH un
chemin mort et laisse le chargement échouer plus loin.

```powershell
Get-ChildItem 'C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA' -Name
```

Ce que la commande doit rendre : `v12.8` et `v13.3`. Si elle rend `v13.4`
seulement, les deux lignes de chemin du script sont à corriger avant tout essai,
et l'embedder est le profil le plus exposé, son moteur gelé d'avril étant le
seul sans cudart embarqué à côté du binaire.

**Le pilote répond.** `nvidia-smi` doit rendre la carte et une version de
pilote, pas une erreur de communication.

**Rien ne tourne déjà.** Un seul modèle à la fois sur cette carte, et un banc
lancé pendant qu'un serveur sert fausse toutes les mesures.

```powershell
.\llm-ctl.ps1 -Action status
```

## L'ordre des trois, et pourquoi

**Spark en premier**, parce que c'est le seul dont le blocage éventuel a une
cause connue et une réponse connue : si l'architecture n'est pas reconnue, c'est
que le mauvais moteur a été pris. Quatre gigaoctets à charger, l'aller-retour est
court, et il valide au passage que le moteur `b10883` fonctionne sur cette
machine. Rater ce test rend inutile de tenter les deux autres.

**Nex ensuite**, le plus gros et celui qui porte le plus d'enjeu. Deux essais
distincts : le texte seul d'abord, la vision après. Ne pas mélanger, le défaut
connu de la vision fait tomber le serveur et masquerait un résultat de texte
parfaitement valable.

**Bonsai en dernier**, parce que son brouillon de spéculation est attendu en
échec. Le premier lancement se fait donc avec les trois drapeaux de spéculation,
pour savoir ; s'il tombe sur le décalage de tenseur documenté dans l'issue 26337,
on les retire et on relance. Deux lancements prévus d'avance, pas un raté.

## Le banc de qualité

Cent dix questions MMLU réparties sur vingt-cinq matières, plus soixante
problèmes GSM8K, température 0, le même jeu pour tous les modèles. C'est le banc
déjà utilisé pour départager `tiel`, `qwen` et `ornith`, ce qui donne les points
de comparaison : 82,0 % et 52/60 pour `qwen`, 82,2 % et 58/60 pour `tiel`,
73,0 % et 53/60 pour `ornith`.

L'outil et le jeu de questions vivent sur la machine, `quality.ps1` et
`epreuves.jsonl` dans `D:\LLM-Setup`. **Les relire avant de lancer** : ce
document ne recopie pas leurs paramètres, parce qu'une copie se périme en
silence et que le script fait foi.

Un point de méthode qui a déjà mordu ici : température 0 pour le banc, jamais
pour le service. Qwen documente que le décodage glouton sur ces poids dégrade la
qualité et produit des répétitions sans fin. Le banc mesure à 0 parce qu'il veut
un résultat reproductible, le profil sert à 0,3 ou 0,6 parce qu'il veut un
modèle utilisable.

## La session longue, en parallèle

Le banc de qualité ne voit pas ce qui casse en usage réel : la boucle de
répétition, la génération qui s'arrête sans marqueur de fin, la dérive au bout
de plusieurs dizaines de milliers de jetons. Une session de travail ordinaire
tourne donc à côté, sur le modèle en cours d'essai, et ce qu'on y guette est
nommé d'avance.

Trois signaux, par ordre de gravité. La **répétition sans fin**, la plus visible.
L'**arrêt silencieux** en contexte long, qui est le défaut ouvert 27756 sur la
famille de Nex : la génération s'achève sans jeton de fin, la réponse est
tronquée et rien ne le signale. Et la **perte de rappel**, la plus sournoise,
quand le modèle cesse de retrouver ce qui est pourtant dans sa fenêtre : c'est
elle qui décide de la fenêtre réellement annonçable au client, et aucun des trois
candidats ne l'a prouvée.

## Ce que le banc ne dira pas

Il ne dira rien de la vitesse comparée à `tiel` et `kat` : le protocole de
vitesse est un autre exercice, même invite de 38 000 jetons, graine fixe, trois
passes, médiane. Il ne dira rien de la vision, qui se juge sur des images
réelles. Il ne dira rien de la tenue à 262 144 jetons, qui demande une aiguille
dans une botte de foin.

Trois mesures manquantes, trois campagnes séparées. Les enchaîner dans la même
séance est le meilleur moyen de n'en réussir aucune.
