"""Code tables from the SEAO XML specification.

Transcribed from "Format XML pour les données ouvertes du SEAO" (Secrétariat du
Conseil du trésor, 1 December 2014), which SEAO publishes as a resource on the
same Données Québec dataset as the data itself.

Codes are stored on the records as-is; these tables exist so the dashboard can
label them without re-deriving the meanings.
"""

from __future__ import annotations

#: <type> — how the contract was awarded.
NOTICE_TYPE: dict[str, str] = {
    "3": "Contrat adjugé suite à un appel d'offres public",
    "9": "Contrat octroyé de gré à gré",
    "10": "Contrat adjugé suite à un appel d'offres sur invitations",
    "14": "Contrat suite à un appel d'offres sur invitation publié au SEAO",
    "16": "Contrat conclu relatif aux infrastructures de transport",
    "17": "Contrat conclu - Appel d'offres public non publié au SEAO",
}

#: <nature> — what was procured.
NATURE: dict[str, str] = {
    "1": "Approvisionnement (biens)",
    "2": "Services",
    "3": "Travaux de construction",
    "5": "Autre",
    "6": "Concession",
    "7": "Vente de biens immeubles",
    "8": "Vente de biens meubles",
}

#: <municipal> — whether the buyer is a municipal body.
MUNICIPAL: dict[str, str] = {
    "0": "Contrat non municipal",
    "1": "Contrat municipal",
}

#: <adjudicataire> — whether this supplier actually won.
#: Only code 1 is an award. The <fournisseurs> block lists every bidder, so
#: treating all of them as awards overstates contract spending badly.
ADJUDICATAIRE: dict[str, str] = {
    "0": "Non adjudicataire",
    "1": "Adjudicataire",
}

#: <conforme> and <admissible> — bid compliance and eligibility.
#: An empty value means not captured or not disclosed, which is distinct from 0.
CONFORMITY: dict[str, str] = {
    "0": "Non conforme",
    "1": "Conforme",
}

#: <montantssoumisunite> — the UNIT the amount is expressed in.
#: This matters enormously: only code 1 is plain Canadian dollars. Summing
#: without filtering on it adds percentages, points and hourly rates into a
#: dollar total.
AMOUNT_UNIT: dict[str, str] = {
    # 0 is absent from the 2014 specification but is the most common value in
    # the data — 3,704 of 6,276 winning bids in May 2024 alone. It is dollars:
    # cross-checking those rows against the independently published final
    # amounts in Contrats_*.xml gives a median final/awarded ratio of 1.000,
    # identical to unit 1. Treating it as unknown would discard most of the
    # money in the dataset.
    "0": "$ (non précisé)",
    "1": "$",
    "2": "$/ANNÉE",
    "3": "$/KM",
    "4": "$/L",
    "5": "$EA",
    "6": "$L",
    "7": "$U.S.",
    "8": "%",
    "9": "$/TM",
    "10": "$/h",
    "11": "points",
    "12": "$/m³",
}

#: Units that represent an absolute amount in Canadian dollars, and so may be
#: summed. '$U.S.' (7) is excluded deliberately: absolute, but not CAD, and
#: converting needs a date-matched exchange rate this project does not have.
#: Rates and scores (per-km, per-hour, %, points) are excluded because summing
#: them with dollars is meaningless.
SUMMABLE_UNITS: frozenset[str] = frozenset({"0", "1"})

#: <regionlivraison> — Quebec administrative region of delivery.
#: Note there is no code 10 in the specification.
DELIVERY_REGION: dict[str, str] = {
    "1": "Bas St-Laurent",
    "2": "Saguenay-Lac-St-Jean",
    "3": "Capitale Nationale",
    "4": "Mauricie",
    "5": "Estrie",
    "6": "Montréal",
    "7": "Outaouais",
    "8": "Abitibi-Témiscaminque",
    "9": "Côte-Nord",
    "11": "Nord-du-Québec",
    "12": "Chaudière-Appalaches",
    "13": "Laval",
    "14": "Laurentides",
    "15": "Montérégie",
    "16": "Lanaudière",
    "17": "Centre-du-Québec",
    "18": "Gaspésie-Iles-de-la-Madeleine",
    "19": "Hors Québec",
}

#: <precision> — only meaningful when <nature> is Services; free text otherwise.
SERVICE_PRECISION: dict[str, str] = {
    "1": "Services professionnels",
    "2": "Services de nature technique",
}


def label(table: dict[str, str], code: str | None) -> str | None:
    """Look up a code, returning None rather than raising on unknown values."""
    if code is None:
        return None
    return table.get(str(code).strip())
