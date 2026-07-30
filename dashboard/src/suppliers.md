# Qui est payé

```js
import {money, moneyShort, number, percent, truncate} from "./components/format.js";

const suppliers = await FileAttachment("data/suppliers.csv").csv({typed: true});
const buyers = await FileAttachment("data/buyers.csv").csv({typed: true});
```

Les 100 principaux fournisseurs et organismes acheteurs, classés par valeur
adjugée. Seules les soumissions gagnantes libellées en dollars sont comptées.

## Fournisseurs

```js
const supplierSearch = view(Inputs.search(suppliers, {
  placeholder: "Chercher un fournisseur…",
  columns: ["supplier_name", "neq", "city"]
}));
```

```js
Inputs.table(supplierSearch, {
  columns: ["supplier_name", "city", "awards", "direct_awards", "total_cad"],
  header: {
    supplier_name: "Fournisseur",
    city: "Ville",
    awards: "Contrats",
    direct_awards: "Dont gré à gré",
    total_cad: "Valeur totale"
  },
  format: {
    supplier_name: (d) => truncate(d, 45),
    awards: number,
    direct_awards: number,
    total_cad: money
  },
  align: {awards: "right", direct_awards: "right", total_cad: "right"},
  width: {supplier_name: 300, total_cad: 140},
  rows: 20
})
```

```js
Plot.plot({
  width,
  height: 420,
  marginLeft: 220,
  x: {label: "Valeur adjugée", tickFormat: moneyShort, grid: true},
  y: {label: null},
  marks: [
    Plot.barX(suppliers.slice(0, 20), {
      x: "total_cad",
      y: "supplier_name",
      sort: {y: "-x"},
      fill: "var(--theme-foreground-focus)",
      tip: {format: {x: money}}
    }),
    Plot.ruleX([0])
  ]
})
```

## Organismes acheteurs

```js
const buyerSearch = view(Inputs.search(buyers, {
  placeholder: "Chercher un organisme…",
  columns: ["buyer_name"]
}));
```

```js
Inputs.table(buyerSearch, {
  columns: ["buyer_name", "is_municipal", "awards", "suppliers", "direct_awards", "total_cad"],
  header: {
    buyer_name: "Organisme",
    is_municipal: "Municipal",
    awards: "Contrats",
    suppliers: "Fournisseurs",
    direct_awards: "Dont gré à gré",
    total_cad: "Valeur totale"
  },
  format: {
    buyer_name: (d) => truncate(d, 45),
    is_municipal: (d) => (d ? "oui" : "non"),
    awards: number,
    suppliers: number,
    direct_awards: number,
    total_cad: money
  },
  align: {awards: "right", suppliers: "right", direct_awards: "right", total_cad: "right"},
  width: {buyer_name: 300, total_cad: 140},
  rows: 20
})
```

## Concentration : dépendance au gré à gré

Chaque point est un organisme. L'axe horizontal donne la valeur totale adjugée,
le vertical la part de ses contrats octroyés sans appel d'offres. En haut à
droite : les organismes qui dépensent beaucoup et attribuent surtout de gré à
gré.

```js
Plot.plot({
  width,
  height: 380,
  marginLeft: 60,
  x: {label: "Valeur adjugée", type: "log", tickFormat: moneyShort, grid: true},
  y: {label: "Part des contrats de gré à gré", percent: true, grid: true, domain: [0, 1]},
  // Plot already applies a sqrt scale to r; pre-squaring the value collapses
  // every small buyer to a sub-pixel dot.
  r: {range: [2.5, 16]},
  marks: [
    Plot.dot(buyers.filter((d) => d.total_cad > 0 && d.awards > 0), {
      x: "total_cad",
      y: (d) => d.direct_awards / d.awards,
      r: "awards",
      fill: "var(--theme-foreground-focus)",
      fillOpacity: 0.55,
      stroke: "var(--theme-background)",
      tip: {
        format: {
          x: money,
          y: (d) => percent(100 * d),
          r: false,
          fill: false
        },
        channels: {Organisme: "buyer_name", Contrats: "awards"}
      }
    })
  ]
})
```

<div class="note">

Une part élevée de gré à gré n'est pas en soi irrégulière — l'urgence, les
fournisseurs uniques et les seuils réglementaires le permettent. Le champ
`disposition` de SEAO indique la disposition légale invoquée ; il est conservé à
l'ingestion et reste à exploiter.

</div>

<style>
.note {
  border-left: 3px solid var(--theme-foreground-focus);
  padding: 0.5rem 0 0.5rem 1rem;
  margin: 1.5rem 0;
  font-size: 0.9rem;
  color: var(--theme-foreground-muted);
}
</style>
