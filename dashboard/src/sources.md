# Sources et mises en garde

Ce tableau de bord ne couvre pour l'instant qu'une source : **SEAO**, le système
électronique d'appel d'offres du Québec. Il s'agit de contrats publics, pas de
l'ensemble des dépenses publiques — voir *Ce qui manque* plus bas.

## Provenance

Les données proviennent du jeu de données SEAO publié sur
[Données Québec](https://www.donneesquebec.ca/recherche/dataset/d23b2e02-085d-43e5-9e6e-e1d558ebfdd5),
sous [Licence Ouverte du Gouvernement du Québec](https://www.donneesquebec.ca/licence/).
Rien n'est extrait par moissonnage de seao.ca : les fichiers sont téléchargés tels
quels, conservés avec leur empreinte SHA-256, puis analysés hors ligne.

SEAO publie deux formats successifs :

| Période | Format |
| --- | --- |
| 2009 → mai 2021 | archives XML annuelles puis mensuelles |
| juin 2021 → aujourd'hui | JSON conforme à l'Open Contracting Data Standard |

Les deux se chevauchent de juin 2021 à mai 2024 dans la source. L'ingestion n'en
retient qu'un par période, sans quoi les montants seraient comptés deux fois.

Chaque archive XML contient trois faits distincts, réunis par le numéro d'avis :
les **avis** (avec toutes les soumissions reçues), les **contrats** (montant final
réglé) et les **dépenses** supplémentaires.

## Comment les totaux sont calculés

Trois filtres s'appliquent à tout montant affiché ici. Chacun corrige une erreur
qui fausserait les totaux de façon importante.

**Seules les soumissions gagnantes.** SEAO publie toutes les soumissions reçues.
Seul `<adjudicataire>1</adjudicataire>` correspond à une adjudication. Sur les
données de mai 2024, cela représente 6 276 gagnants sur 11 350 soumissions :
compter toutes les lignes surestimerait les dépenses d'environ 80 %.

**Seulement les dollars.** Le champ `<montantssoumisunite>` indique l'unité du
montant : dollars, mais aussi $/km, $/h, %, points ou $US. Additionner sans
filtrer reviendrait à ajouter des pourcentages à des dollars.

**Zéro ne veut pas dire zéro.** SEAO inscrit `0.000000` pour « sans objet ».
`<montanttotalcontrat>` vaut presque toujours zéro alors que `<montantcontrat>`
porte le vrai montant.

Les montants sont en **dollars courants**, non ajustés pour l'inflation. Une
comparaison entre 2009 et 2026 surestime donc la croissance réelle.

## Ce qui manque

SEAO couvre les contrats. Il ne couvre pas :

- les **paiements de transfert** — santé, éducation, aide sociale — qui
  représentent la majeure partie des dépenses publiques ;
- la **rémunération** de la fonction publique ;
- le **service de la dette** ;
- les dépenses fédérales, de quelque nature que ce soit.

Pour ces éléments il faut le Budget de dépenses du Conseil du trésor et les
Comptes publics du Québec (tous deux en PDF), ainsi que GC InfoBase et les
Comptes publics du Canada du côté fédéral. Aucun n'est encore intégré.

Les taxonomies fédérale et québécoise ne se correspondent pas : la structure
fédérale *article standard / crédit / programme* et la structure québécoise
*portefeuille / mission / programme* n'ont pas de table de correspondance qui
résiste au détail. C'est la décision structurante qui reste à trancher avant
d'ajouter les dépenses budgétaires.

## Mises en garde complètes

Le registre du dépôt (`sources/registry.json`) recense chaque mise en garde avec
sa gravité. En ligne de commande :

```
govbudget caveats
```

## Reproduire ces chiffres

```
govbudget seao:ingest --era xml  --until 2021-05
govbudget seao:ingest --era ocds --cadence mensuel --since 2021-06
govbudget dashboard:data
cd dashboard && npm run dev
```

Les fichiers bruts sont conservés et adressés par empreinte, donc une correction
d'analyseur se rejoue sur tout l'historique sans retélécharger.
