"""Crosswalk between the federal and Quebec spending taxonomies.

The decision this encodes
-------------------------
**Two parallel fact tables, joined only on fiscal year, appropriation type, and
a coarse economic category — and the economic join carries a per-category
comparability flag rather than being trusted uniformly.**

Grounded in the published vocabularies, not in theory:

    dimension        federal (GC InfoBase)        Quebec (Budget de dépenses)
    ---------------  ---------------------------  ---------------------------
    organization     ~130 orgs, org_id            25 portefeuilles
    programme        1,496 programs under         99 programmes / 330 éléments
                     core responsibilities
    economic         14 standard objects          10 supercatégories
    appropriation    voted / statutory            Votés / Permanents
    fiscal year      April–March                  April–March

Organization and programme do not map. There is no crosswalk between 1,496
federal programs and 99 Quebec programmes that survives contact with the detail,
and inventing one would be the single most misleading thing this project could
do. They stay in separate tables.

Fiscal year and appropriation type map exactly. Both governments run April to
March, and voted-versus-statutory is the same constitutional idea in both:
money Parliament or the Assemblée votes annually, versus money that flows under
standing legislation.

The economic dimension maps structurally but **not uniformly in meaning**, and
that is the finding that shapes everything downstream.

Why the economic join needs a comparability flag
------------------------------------------------
Federal Personnel is $65.3B. Quebec Rémunération is $4.9B. Quebec does not
employ a thirteenth as many people — its public-sector payroll sits *inside* the
$112.1B it books as Transfert to the health and education networks, which employ
the staff and pay them. Quebec's Rémunération line covers the core civil service
only.

So the two governments consolidate at different levels, and any chart placing
"personnel spending" side by side is comparing a consolidated figure with an
unconsolidated one. Debt service and total spending survive the comparison;
personnel and operating do not.

`COMPARABILITY` records that per category so a chart can refuse, or annotate,
rather than silently mislead.
"""

from __future__ import annotations

from dataclasses import dataclass

#: Harmonized economic categories. Deliberately few: this is the coarse spine
#: the two jurisdictions genuinely share, not an attempt at fine equivalence.
ECONOMIC_CATEGORIES = (
    "personnel",
    "operating",
    "transfers",
    "capital",
    "debt_service",
    "other",
)

ECONOMIC_LABELS = {
    "personnel": ("Personnel", "Rémunération"),
    "operating": ("Operating", "Fonctionnement"),
    "transfers": ("Transfer payments", "Transferts"),
    "capital": ("Capital", "Immobilisations"),
    "debt_service": ("Debt service", "Service de la dette"),
    "other": ("Other", "Autres"),
}


@dataclass(frozen=True)
class Comparability:
    """How far a harmonized category can be trusted across jurisdictions."""

    level: str  # 'comparable' | 'caution' | 'not-comparable'
    note: str
    #: French rendering, for the dashboard. The data is Quebec-facing and the
    #: site is French; an English caveat on a French page reads as boilerplate
    #: and gets skipped, which defeats the point of writing it.
    note_fr: str = ""


#: Per-category verdicts, from the FY2023-24 federal and 2026-27 Quebec figures.
COMPARABILITY: dict[str, Comparability] = {
    "debt_service": Comparability(
        "comparable",
        "Both book interest on their own debt at the same consolidation level.",
           note_fr=(
            "Les deux ordres de gouvernement inscrivent les intérêts de leur propre dette au même "
            "niveau de consolidation."
        ),
    ),
    "transfers": Comparability(
        "caution",
        "Both are large (federal $270.2B of $474.9B; Quebec $112.1B of $145.5B) "
        "but they are not the same thing. Federal transfers go mostly to "
        "individuals and provinces; Quebec transfers go mostly to the health and "
        "education networks it funds, which then spend on salaries. Summing the "
        "two also double-counts the federal transfers Quebec receives as revenue.",
           note_fr=(
            "Les deux montants sont importants (fédéral 270,2 G$ sur 474,9 G$ ; Québec 112,1 G$ sur "
            "145,5 G$) mais ne recouvrent pas la même chose. Les transferts fédéraux vont surtout "
            "aux particuliers et aux provinces ; les transferts québécois vont surtout aux réseaux "
            "de la santé et de l'éducation, qui paient ensuite les salaires. Additionner les deux "
            "compterait aussi deux fois les transferts fédéraux versés au Québec."
        ),
    ),
    "personnel": Comparability(
        "not-comparable",
        "Federal Personnel is $65.3B, Quebec Rémunération $4.9B. Quebec's "
        "public-sector payroll is inside its Transfert line, since the networks "
        "employ the staff. The two measure different populations.",
           note_fr=(
            "Le poste Personnel fédéral atteint 65,3 G$, la Rémunération québécoise 4,9 G$. La "
            "masse salariale publique du Québec est comprise dans les transferts, puisque ce sont "
            "les réseaux qui emploient le personnel. Les deux mesures ne portent pas sur la même "
            "population."
        ),
    ),
    "operating": Comparability(
        "not-comparable",
        "Same consolidation problem as personnel: network operating costs appear "
        "as Quebec transfers, not as Quebec operating spending.",
           note_fr=(
            "Même problème de consolidation que la rémunération : les frais de fonctionnement des "
            "réseaux apparaissent comme des transferts québécois, non comme des dépenses de "
            "fonctionnement."
        ),
    ),
    "capital": Comparability(
        "caution",
        "Quebec reports investment separately from expenditure credits "
        "(BUDGET_INVESTISSEMENT) and splits out information-technology assets; "
        "the federal standard objects fold acquisition into expenditure.",
           note_fr=(
            "Le Québec présente les investissements séparément des crédits de dépenses "
            "(BUDGET_INVESTISSEMENT) et isole les ressources informationnelles ; les objets "
            "standards fédéraux intègrent les acquisitions aux dépenses."
        ),
    ),
    "other": Comparability(
        "not-comparable",
        "A residual on both sides, holding different things. Never chart it as "
        "though it were one category.",
           note_fr=(
            "Un poste résiduel des deux côtés, qui ne contient pas les mêmes éléments. À ne jamais "
            "présenter comme une catégorie unique."
        ),
    ),
}

#: Federal standard objects (`sobj_en` in GC InfoBase) -> harmonized category.
#: Revenue lines are negative offsets, not spending, and are kept out of the
#: spending categories entirely.
FEDERAL_STANDARD_OBJECT: dict[str, str] = {
    "Personnel": "personnel",
    "Transfer payments": "transfers",
    "Public debt charges": "debt_service",
    "Professional and special services": "operating",
    "Transportation and communications": "operating",
    "Utilities, materials and supplies": "operating",
    "Rentals": "operating",
    "Repair and maintenance": "operating",
    "Information": "operating",
    "Acquisition of machinery and equipment": "capital",
    "Acquisition of land, buildings and works": "capital",
    "Other subsidies and payments": "other",
    "External revenues": "other",
    "Internal revenues": "other",
}

#: Quebec supercatégories -> harmonized category. Keyed on the leading code
#: because the label text varies across years while the code does not.
QUEBEC_SUPERCATEGORIE: dict[str, str] = {
    "1": "personnel",       # Rémunération
    "2": "operating",       # Fonctionnement
    "3": "capital",         # Immobilisations autres qu'en ressources informationnelles
    "M": "capital",         # Immobilisations en ressources informationnelles
    "4": "debt_service",    # Service de la dette
    "5": "transfers",       # Transfert
    "6": "other",           # Prêts, placements, avances et autres coûts
    "F": "other",           # Affectation à un fonds spécial
    "P": "other",           # Créances douteuses, autres provisions et pertes
    "A": "other",           # moins Amortissement
}

#: Quebec supercatégories keyed on the *label* rather than the code.
#:
#: The Budget de dépenses writes '5 Transfert' with a leading code; the Comptes
#: publics write 'Transferts' with none, and pluralise differently. Keying only
#: on the code sends every Comptes publics row to 'other' — silently, since the
#: fallback is a valid category.
QUEBEC_SUPERCATEGORIE_LABEL: dict[str, str] = {
    "remuneration": "personnel",
    "fonctionnement": "operating",
    "transfert": "transfers",
    "transferts": "transfers",
    "immobilisations autres qu'en ressources informationnelles": "capital",
    "immobilisations en ressources informationnelles": "capital",
    "service de la dette": "debt_service",
    "affectation a un fonds special": "other",
    "creances douteuses et autres provisions": "other",
    "creances douteuses, autres provisions et pertes": "other",
    "prets, placements, avances et autres couts": "other",
    "moins amortissement": "other",
    "amortissement": "other",
}


def _strip_accents(text: str) -> str:
    import unicodedata

    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


#: Appropriation type. The one dimension that maps exactly.
APPROPRIATION = {
    "voted": ("Voted", "Votés"),
    "statutory": ("Statutory", "Permanents"),
}

FEDERAL_APPROPRIATION: dict[str, str] = {
    "voted": "voted",
    "statutory": "statutory",
    "1": "voted",
    "0": "statutory",
}

QUEBEC_APPROPRIATION: dict[str, str] = {
    # Budget de dépenses wording
    "Votés": "voted",
    "Permanents": "statutory",
    # Comptes publics wording for the same distinction
    "Annuels": "voted",
    # Spending that requires no appropriation at all — neither voted nor
    # statutory, so it is left unmapped rather than forced into one.
    "Ne nécessitant pas de crédits": None,
}


def federal_economic(standard_object: str | None) -> str:
    """Map a federal standard object to the harmonized category."""
    if not standard_object:
        return "other"
    return FEDERAL_STANDARD_OBJECT.get(standard_object.strip(), "other")


def quebec_economic(supercategorie: str | None) -> str:
    """Map a Quebec supercatégorie to the harmonized category.

    Handles both published forms: the Budget de dépenses prefixes a code
    ('5 Transfert'), the Comptes publics give the label alone ('Transferts').
    """
    text = (supercategorie or "").strip()
    if not text:
        return "other"

    code = text.split(None, 1)[0]
    if code in QUEBEC_SUPERCATEGORIE:
        return QUEBEC_SUPERCATEGORIE[code]

    label = _strip_accents(text).lower().strip()
    return QUEBEC_SUPERCATEGORIE_LABEL.get(label, "other")


def quebec_appropriation(type_de_credits: str | None) -> str | None:
    if not type_de_credits:
        return None
    return QUEBEC_APPROPRIATION.get(type_de_credits.strip())


def comparability(category: str) -> Comparability:
    return COMPARABILITY.get(
        category, Comparability("not-comparable", "Unknown category.")
    )


def is_comparable(category: str) -> bool:
    """True only for categories safe to place side by side without a caveat."""
    return comparability(category).level == "comparable"
