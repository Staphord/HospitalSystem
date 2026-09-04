"""The hospital medicines reference pack.

Every statement the assistant makes about a medicine comes from this file. It
is deliberately a file in the repository rather than rows in a database: it is
versioned with the code, it is reviewed in a pull request like the code, and a
change to what the assistant tells a prescriber leaves the same trail as a
change to what the software does.

BEFORE SWITCHING THIS CAPABILITY ON, HAVE IT REVIEWED.

This pack is a starter set. It covers the medicines a district hospital uses
most and the interactions that most often matter between them, written to be
conservative: where a reference would hedge, this hedges, and where it would say
avoid, this says avoid. It is not a formulary, it is not exhaustive, and it is
not a substitute for the national guidelines the hospital works to. The
hospital's own pharmacist must read every entry against those guidelines and
sign it off before `ASSISTANT_MEDICATION_CHECK_ENABLED` is turned on for real
patients. Bump `MEDICINES_PACK_VERSION` on every change: it is stamped on the
audit record for every answer, so an answer given last month can be traced to
the exact text that produced it.

Adding a medicine is one `_medicine(...)` entry. Adding an interaction is one
`_rule(...)` entry, and a class rule is usually the better one to add: a rule
between `NSAID` and `ANTICOAGULANT` answers every pair in both classes,
including the ones added after it.
"""

from __future__ import annotations

from datetime import date

from shared.medicines.models import (
    ApprovalState,
    InteractionRule,
    Monograph,
    PregnancyStance,
    Severity,
)

# Bump on every change to a monograph or a rule. Recorded on every audit row.
MEDICINES_PACK_VERSION = "medicines-reference-2026.09.2"

_V1 = "1.0.0"
_EFFECTIVE = date(2026, 1, 1)
_SOURCE = "Hospital medicines reference, compiled from national and WHO essential medicines guidance"

# Classes. Rules match on these, so a name here is part of the reference's
# interface: renaming one silently detaches every rule that used it, which is
# why `test_assistant_medicines.py` asserts every class named by a rule is
# carried by at least one monograph.
NSAID = "nsaid"
ANTICOAGULANT = "anticoagulant"
ANTIPLATELET = "antiplatelet"
ACE_INHIBITOR = "ace_inhibitor"
ARB = "arb"
STATIN = "statin"
MACROLIDE = "macrolide"
FLUOROQUINOLONE = "fluoroquinolone"
TETRACYCLINE = "tetracycline"
AMINOGLYCOSIDE = "aminoglycoside"
PENICILLIN = "penicillin"
CEPHALOSPORIN = "cephalosporin"
NITROIMIDAZOLE = "nitroimidazole"
AZOLE_ANTIFUNGAL = "azole_antifungal"
OPIOID = "opioid"
BENZODIAZEPINE = "benzodiazepine"
CORTICOSTEROID = "corticosteroid"
LOOP_DIURETIC = "loop_diuretic"
THIAZIDE_DIURETIC = "thiazide_diuretic"
SULFONYLUREA = "sulfonylurea"
BIGUANIDE = "biguanide"
CALCIUM_CHANNEL_BLOCKER = "calcium_channel_blocker"
BETA_BLOCKER = "beta_blocker"
ANTICONVULSANT = "anticonvulsant"
ANTIRETROVIRAL = "antiretroviral"
ANTIMALARIAL = "antimalarial"
ANTITUBERCULAR = "antitubercular"
PROTON_PUMP_INHIBITOR = "proton_pump_inhibitor"
ANTIEMETIC = "antiemetic"
IRON = "iron"
HORMONAL_CONTRACEPTIVE = "hormonal_contraceptive"
# Cross-cutting classes. A medicine carries these alongside its own class, so a
# rule about QT prolongation or enzyme induction reaches everything that does it
# without a rule per pair.
QT_PROLONGING = "qt_prolonging"
ENZYME_INDUCER = "enzyme_inducer"
NEPHROTOXIC = "nephrotoxic"
SEDATING = "sedating"


def _medicine(
    drug_id: str,
    generic_name: str,
    class_label: str,
    drug_classes: set[str],
    used_for: str,
    adult_dose: str,
    max_adult_dose: str = "",
    pregnancy_stance: PregnancyStance = PregnancyStance.NOT_STATED,
    pregnancy: str = "",
    breastfeeding: str = "",
    renal: str = "",
    hepatic: str = "",
    monitoring: str = "",
    cautions: tuple[str, ...] = (),
    synonyms: set[str] | None = None,
) -> Monograph:
    return Monograph(
        drug_id=drug_id,
        generic_name=generic_name,
        class_label=class_label,
        drug_classes=frozenset(drug_classes),
        used_for=used_for,
        adult_dose=adult_dose,
        max_adult_dose=max_adult_dose,
        pregnancy_stance=pregnancy_stance,
        pregnancy=pregnancy,
        breastfeeding=breastfeeding,
        renal=renal,
        hepatic=hepatic,
        monitoring=monitoring,
        cautions=cautions,
        synonyms=frozenset(synonyms or set()),
        version=_V1,
        effective_from=_EFFECTIVE,
        approval_state=ApprovalState.APPROVED,
        source=_SOURCE,
    )


def _rule(
    rule_id: str,
    severity: Severity,
    effect: str,
    management: str,
    drug_a: str = "",
    class_a: str = "",
    drug_b: str = "",
    class_b: str = "",
) -> InteractionRule:
    return InteractionRule(
        rule_id=rule_id,
        severity=severity,
        effect=effect,
        management=management,
        drug_a=drug_a,
        class_a=class_a,
        drug_b=drug_b,
        class_b=class_b,
        version=_V1,
        effective_from=_EFFECTIVE,
        approval_state=ApprovalState.APPROVED,
        source=_SOURCE,
    )


# ---------------------------------------------------------------------------
# Pain, fever and inflammation
# ---------------------------------------------------------------------------

_ANALGESICS: tuple[Monograph, ...] = (
    _medicine(
        "paracetamol",
        "Paracetamol",
        "simple analgesic and antipyretic",
        {"analgesic"},
        "Mild to moderate pain and fever.",
        "500 mg to 1 g by mouth every 4 to 6 hours.",
        max_adult_dose="4 g in 24 hours.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Analgesic of choice in pregnancy at usual doses, for the shortest effective period.",
        breastfeeding="Compatible with breastfeeding at usual doses.",
        hepatic="Reduce the dose in significant liver disease, low body weight, malnutrition or chronic alcohol use.",
        cautions=(
            "Overdose causes liver failure and the patient may look well for the first 24 hours.",
            "Check other products the patient is taking: many combination cold and pain preparations already contain paracetamol.",
        ),
        synonyms={"acetaminophen", "panadol", "pcm"},
    ),
    _medicine(
        "ibuprofen",
        "Ibuprofen",
        "NSAID",
        {NSAID},
        "Mild to moderate pain, fever and inflammatory conditions.",
        "200 mg to 400 mg by mouth every 6 to 8 hours with food.",
        max_adult_dose="1.2 g in 24 hours without specialist advice.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy=(
            "Avoid from 20 weeks onwards: NSAIDs reduce fetal urine output and amniotic fluid, "
            "and from about 30 weeks they can close the ductus arteriosus. Before 20 weeks use "
            "only if there is no alternative and the indication justifies it."
        ),
        breastfeeding="Compatible with breastfeeding; amounts in milk are very small.",
        renal="Avoid in significant kidney impairment; NSAIDs reduce renal perfusion.",
        monitoring="Kidney function and blood pressure on prolonged use.",
        cautions=(
            "Avoid in active peptic ulcer, and in asthma where NSAIDs have caused bronchospasm before.",
            "Take with or after food.",
        ),
        synonyms={"brufen", "nurofen"},
    ),
    _medicine(
        "diclofenac",
        "Diclofenac",
        "NSAID",
        {NSAID},
        "Moderate pain and inflammatory conditions.",
        "50 mg by mouth two to three times a day with food.",
        max_adult_dose="150 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Avoid, particularly from 20 weeks onwards. The NSAID cautions apply as for ibuprofen.",
        breastfeeding="Small amounts in milk; short courses are generally acceptable.",
        renal="Avoid in significant kidney impairment.",
        cautions=(
            "Carries a higher cardiovascular risk than ibuprofen at equivalent doses; avoid in established heart disease.",
        ),
        synonyms={"voltaren", "voltarol"},
    ),
    _medicine(
        "aspirin",
        "Aspirin",
        "NSAID and antiplatelet",
        {NSAID, ANTIPLATELET},
        "Low dose for cardiovascular protection; higher doses for pain and fever.",
        "75 mg to 150 mg daily for antiplatelet use. 300 mg to 900 mg every 4 to 6 hours for pain.",
        max_adult_dose="4 g in 24 hours for analgesic use.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy=(
            "Low dose (75 mg to 150 mg) is used deliberately in pregnancy for pre-eclampsia prophylaxis "
            "on obstetric advice. Analgesic doses should be avoided, especially in the third trimester."
        ),
        breastfeeding="Avoid analgesic doses while breastfeeding because of the risk of Reye's syndrome.",
        cautions=(
            "Do not give to children under 16 for fever or pain: risk of Reye's syndrome.",
            "Increases bleeding risk with any other medicine that affects clotting.",
        ),
        synonyms={"acetylsalicylic acid", "asa"},
    ),
    _medicine(
        "tramadol",
        "Tramadol",
        "opioid analgesic",
        {OPIOID, SEDATING},
        "Moderate to severe pain not controlled by paracetamol or an NSAID.",
        "50 mg to 100 mg by mouth every 4 to 6 hours.",
        max_adult_dose="400 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid in pregnancy unless there is no alternative; prolonged use near term causes neonatal withdrawal.",
        breastfeeding="Avoid; small amounts pass into milk and the neonate is sensitive to sedation.",
        renal="Increase the dosing interval in kidney impairment.",
        cautions=(
            "Lowers the seizure threshold; avoid in epilepsy that is not controlled.",
            "Sedation and respiratory depression are additive with any other sedating medicine.",
        ),
    ),
    _medicine(
        "morphine",
        "Morphine",
        "strong opioid analgesic",
        {OPIOID, SEDATING},
        "Severe pain.",
        "5 mg to 10 mg by mouth or subcutaneously every 4 hours, titrated to the patient's response.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Use only where the indication justifies it. Prolonged use near term causes neonatal withdrawal and respiratory depression at delivery.",
        breastfeeding="Short courses at usual doses are acceptable; watch the infant for drowsiness and poor feeding.",
        renal="Accumulates in kidney impairment; reduce the dose and lengthen the interval.",
        monitoring="Respiratory rate and sedation score.",
        cautions=(
            "Have naloxone available wherever parenteral opioids are given.",
        ),
    ),
    _medicine(
        "codeine",
        "Codeine",
        "opioid analgesic",
        {OPIOID, SEDATING},
        "Mild to moderate pain, usually combined with paracetamol.",
        "30 mg to 60 mg by mouth every 4 to 6 hours.",
        max_adult_dose="240 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid prolonged use; use near term causes neonatal withdrawal.",
        breastfeeding="Avoid. Mothers who metabolise codeine rapidly can pass dangerous amounts of morphine into milk.",
        cautions=(
            "Constipation is predictable; prescribe with it in mind.",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Anti-infectives
# ---------------------------------------------------------------------------

_ANTI_INFECTIVES: tuple[Monograph, ...] = (
    _medicine(
        "amoxicillin",
        "Amoxicillin",
        "penicillin antibiotic",
        {PENICILLIN},
        "Respiratory, ear, urinary and dental infections caused by susceptible organisms.",
        "500 mg by mouth every 8 hours.",
        max_adult_dose="1 g every 8 hours in severe infection.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Widely used in pregnancy at usual doses.",
        breastfeeding="Compatible with breastfeeding.",
        renal="Reduce the dose in severe kidney impairment.",
        cautions=(
            "Do not give to a patient with a documented penicillin allergy. Ask before prescribing.",
        ),
        synonyms={"amoxycillin", "amox", "augmentin", "co-amoxiclav", "amoxicillin-clavulanate"},
    ),
    _medicine(
        "ceftriaxone",
        "Ceftriaxone",
        "third-generation cephalosporin antibiotic",
        {CEPHALOSPORIN},
        "Severe bacterial infection, including meningitis and severe pneumonia.",
        "1 g to 2 g by intravenous or intramuscular injection once daily.",
        max_adult_dose="4 g in 24 hours in meningitis.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Acceptable in pregnancy where a parenteral cephalosporin is indicated.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "Do not mix or give through the same line as a calcium-containing infusion; the two precipitate.",
            "Cross-reactivity with penicillin allergy is uncommon but real; ask about anaphylaxis specifically.",
        ),
        synonyms={"rocephin"},
    ),
    _medicine(
        "ciprofloxacin",
        "Ciprofloxacin",
        "fluoroquinolone antibiotic",
        {FLUOROQUINOLONE, QT_PROLONGING},
        "Urinary, gastrointestinal and some respiratory infections caused by susceptible organisms.",
        "500 mg by mouth every 12 hours.",
        max_adult_dose="750 mg every 12 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid in pregnancy unless no suitable alternative exists; use another antibiotic where one will do.",
        breastfeeding="Avoid where an alternative exists.",
        renal="Reduce the dose in kidney impairment.",
        cautions=(
            "Tendon pain or swelling means stop the medicine and review; rupture can follow.",
            "Prolongs the QT interval.",
        ),
        synonyms={"cipro", "ciproxin"},
    ),
    _medicine(
        "metronidazole",
        "Metronidazole",
        "nitroimidazole antimicrobial",
        {NITROIMIDAZOLE},
        "Anaerobic bacterial infection, amoebiasis, giardiasis and trichomoniasis.",
        "400 mg by mouth every 8 hours.",
        max_adult_dose="2 g in 24 hours for short courses.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Usual doses are used in pregnancy where indicated; avoid high single doses.",
        breastfeeding="Small amounts pass into milk and may change its taste; short courses are acceptable.",
        hepatic="Reduce the dose in severe liver disease.",
        cautions=(
            "Tell the patient not to drink alcohol during the course and for 48 hours after it: flushing, vomiting and headache follow.",
        ),
        synonyms={"flagyl"},
    ),
    _medicine(
        "doxycycline",
        "Doxycycline",
        "tetracycline antibiotic",
        {TETRACYCLINE},
        "Chest infection, skin infection, sexually transmitted infection and some tropical infections.",
        "100 mg by mouth every 12 hours on the first day, then 100 mg daily.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Avoid from the second trimester onwards: it is taken up by developing teeth and bone and discolours them permanently.",
        breastfeeding="Avoid prolonged courses while breastfeeding.",
        cautions=(
            "Swallow with a full glass of water sitting upright; it causes oesophageal ulceration if it lodges.",
            "Causes photosensitivity: warn about sun exposure.",
        ),
        synonyms={"doxy", "vibramycin"},
    ),
    _medicine(
        "azithromycin",
        "Azithromycin",
        "macrolide antibiotic",
        {MACROLIDE, QT_PROLONGING},
        "Respiratory infection, some sexually transmitted infections, and trachoma.",
        "500 mg by mouth once daily for 3 days, or as a single 1 g dose for specific indications.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Acceptable in pregnancy where a macrolide is indicated.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=("Prolongs the QT interval.",),
        synonyms={"azithro", "zithromax"},
    ),
    _medicine(
        "erythromycin",
        "Erythromycin",
        "macrolide antibiotic",
        {MACROLIDE, QT_PROLONGING},
        "Respiratory and skin infection, and as an alternative where penicillin cannot be used.",
        "250 mg to 500 mg by mouth every 6 hours.",
        max_adult_dose="4 g in 24 hours.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Acceptable in pregnancy.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "A strong inhibitor of drug metabolism: check every other medicine the patient takes before starting it.",
            "Prolongs the QT interval.",
        ),
    ),
    _medicine(
        "clarithromycin",
        "Clarithromycin",
        "macrolide antibiotic",
        {MACROLIDE, QT_PROLONGING},
        "Respiratory infection and part of Helicobacter pylori eradication.",
        "500 mg by mouth every 12 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid in the first trimester where an alternative macrolide will do.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "A strong inhibitor of drug metabolism, with more interactions than any other medicine on this list.",
            "Prolongs the QT interval.",
        ),
        synonyms={"klacid"},
    ),
    _medicine(
        "gentamicin",
        "Gentamicin",
        "aminoglycoside antibiotic",
        {AMINOGLYCOSIDE, NEPHROTOXIC},
        "Severe Gram-negative infection, usually with another antibiotic.",
        "5 mg/kg by intravenous or intramuscular injection once daily, adjusted to levels and kidney function.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid unless the infection is severe and no safer alternative exists: it can damage fetal hearing.",
        breastfeeding="Amounts in milk are negligible.",
        renal="Dose by weight and kidney function; extend the interval as clearance falls.",
        monitoring="Kidney function and drug levels. Ask about hearing and balance at every review.",
        cautions=(
            "Damage to hearing and balance can be permanent and is not always reversible on stopping.",
        ),
    ),
    _medicine(
        "cotrimoxazole",
        "Co-trimoxazole",
        "sulfonamide and trimethoprim antibiotic",
        {"sulfonamide"},
        "Pneumocystis prophylaxis and treatment, urinary infection, and some skin infections.",
        "960 mg by mouth every 12 hours for treatment. 960 mg daily for prophylaxis.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy=(
            "Avoid in the first trimester, where it antagonises folate, and near term, where it "
            "raises the risk of neonatal jaundice. Where it is continued in pregnancy, give folic acid with it."
        ),
        breastfeeding="Avoid in the first weeks of life and in jaundiced or premature infants.",
        renal="Reduce the dose in kidney impairment.",
        monitoring="Full blood count on prolonged courses; potassium in kidney impairment.",
        cautions=(
            "Stop at the first sign of a rash: severe skin reactions begin this way.",
        ),
        synonyms={"co-trimoxazole", "septrin", "bactrim", "trimethoprim-sulfamethoxazole"},
    ),
    _medicine(
        "fluconazole",
        "Fluconazole",
        "azole antifungal",
        {AZOLE_ANTIFUNGAL, QT_PROLONGING},
        "Candidiasis and cryptococcal disease.",
        "150 mg as a single dose for vaginal candidiasis. 200 mg to 400 mg daily for systemic infection.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy=(
            "A single 150 mg dose is generally acceptable. Avoid the high doses used in systemic "
            "infection during the first trimester: they are associated with birth defects."
        ),
        breastfeeding="Compatible with breastfeeding at usual doses.",
        renal="Reduce the dose in kidney impairment.",
        cautions=(
            "Inhibits the metabolism of several medicines, including warfarin and phenytoin.",
        ),
    ),
    _medicine(
        "rifampicin",
        "Rifampicin",
        "antitubercular antibiotic",
        {ANTITUBERCULAR, ENZYME_INDUCER},
        "Tuberculosis, as part of combination therapy.",
        "10 mg/kg by mouth once daily, usually 450 mg to 600 mg, on an empty stomach.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used throughout pregnancy where tuberculosis is being treated; the treatment matters more than the risk.",
        breastfeeding="Compatible with breastfeeding.",
        hepatic="Monitor liver function; stop and review if transaminases rise sharply.",
        monitoring="Liver function and adherence.",
        cautions=(
            "Colours urine, sweat and tears orange. Tell the patient, or they will stop taking it.",
            "A powerful enzyme inducer: it lowers the level of many other medicines, including hormonal contraceptives.",
        ),
        synonyms={"rifampin"},
    ),
    _medicine(
        "isoniazid",
        "Isoniazid",
        "antitubercular antibiotic",
        {ANTITUBERCULAR},
        "Tuberculosis treatment and preventive therapy.",
        "5 mg/kg by mouth once daily, usually 300 mg.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used in pregnancy where tuberculosis is being treated. Give pyridoxine with it.",
        breastfeeding="Compatible with breastfeeding; give the infant pyridoxine if it is also treated.",
        hepatic="Monitor liver function, particularly over 35 and with alcohol use.",
        cautions=(
            "Give pyridoxine 10 mg to 25 mg daily to prevent peripheral neuropathy.",
        ),
    ),
    _medicine(
        "artemether_lumefantrine",
        "Artemether with lumefantrine",
        "artemisinin-based combination antimalarial",
        {ANTIMALARIAL},
        "Uncomplicated Plasmodium falciparum malaria.",
        "4 tablets (80 mg artemether with 480 mg lumefantrine) by mouth twice daily for 3 days, for an adult over 35 kg.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy=(
            "Used in the second and third trimesters. In the first trimester follow the national "
            "protocol, which may direct quinine with clindamycin instead."
        ),
        breastfeeding="Acceptable while breastfeeding.",
        cautions=(
            "Give with fatty food or milk: absorption is poor on an empty stomach and the treatment fails.",
        ),
        # "AL" is deliberately not a synonym here. It is two letters, it is a
        # word in its own right, and a two-letter alias that matches inside a
        # sentence would pull an antimalarial into an unrelated question.
        synonyms={"coartem", "alu", "artemether-lumefantrine"},
    ),
    _medicine(
        "quinine",
        "Quinine",
        "antimalarial",
        {ANTIMALARIAL, QT_PROLONGING},
        "Severe malaria, and uncomplicated malaria where an artemisinin combination cannot be used.",
        "600 mg by mouth every 8 hours for 7 days, or by infusion in severe malaria per the national protocol.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used in pregnancy, including the first trimester, where malaria is being treated.",
        breastfeeding="Compatible with breastfeeding.",
        monitoring="Blood glucose during intravenous treatment: quinine causes hypoglycaemia.",
        cautions=(
            "Prolongs the QT interval. Never give by rapid intravenous injection.",
            "Tinnitus and deafness are expected at treatment doses and are not on their own a reason to stop.",
        ),
    ),
)

# ---------------------------------------------------------------------------
# Cardiovascular and metabolic
# ---------------------------------------------------------------------------

_CARDIOMETABOLIC: tuple[Monograph, ...] = (
    _medicine(
        "enalapril",
        "Enalapril",
        "ACE inhibitor",
        {ACE_INHIBITOR},
        "Hypertension and heart failure.",
        "5 mg by mouth daily initially, increased to 10 mg to 20 mg daily.",
        max_adult_dose="40 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy=(
            "Contraindicated. ACE inhibitors damage the developing fetal kidney and cause "
            "oligohydramnios, skull defects and neonatal renal failure. Stop it and change to a "
            "medicine used in pregnancy, such as methyldopa or nifedipine, as soon as pregnancy is known."
        ),
        breastfeeding="Enalapril is among the ACE inhibitors used while breastfeeding, at usual doses.",
        renal="Reduce the dose and monitor closely; it can precipitate renal failure in renal artery stenosis.",
        monitoring="Kidney function and potassium before starting and after each dose increase.",
        cautions=(
            "A dry persistent cough is a class effect and a common reason for stopping.",
            "Raises potassium.",
        ),
    ),
    _medicine(
        "losartan",
        "Losartan",
        "angiotensin receptor blocker",
        {ARB},
        "Hypertension, and heart failure where an ACE inhibitor is not tolerated.",
        "50 mg by mouth daily.",
        max_adult_dose="100 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Contraindicated for the same reasons as ACE inhibitors. Change to a medicine used in pregnancy as soon as pregnancy is known.",
        breastfeeding="Avoid; there is little information and alternatives are better documented.",
        renal="Monitor kidney function and potassium.",
        monitoring="Kidney function and potassium.",
        cautions=("Raises potassium.",),
    ),
    _medicine(
        "amlodipine",
        "Amlodipine",
        "calcium channel blocker",
        {CALCIUM_CHANNEL_BLOCKER},
        "Hypertension and angina.",
        "5 mg by mouth daily, increased to 10 mg daily.",
        max_adult_dose="10 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Nifedipine is the better documented calcium channel blocker in pregnancy; change to it where one is needed.",
        breastfeeding="Acceptable while breastfeeding.",
        cautions=("Ankle swelling is common and is not fluid overload.",),
    ),
    _medicine(
        "nifedipine",
        "Nifedipine",
        "calcium channel blocker",
        {CALCIUM_CHANNEL_BLOCKER},
        "Hypertension, including hypertension in pregnancy, and as a tocolytic.",
        "Modified release 20 mg by mouth every 12 hours.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="One of the medicines used for hypertension in pregnancy, alongside methyldopa and labetalol.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "Do not use short-acting capsules to bring blood pressure down quickly: the fall is uncontrolled.",
        ),
    ),
    _medicine(
        "methyldopa",
        "Methyldopa",
        "centrally acting antihypertensive",
        {"antihypertensive"},
        "Hypertension in pregnancy.",
        "250 mg by mouth two to three times a day, increased as needed.",
        max_adult_dose="3 g in 24 hours.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="The longest-established antihypertensive in pregnancy, with the most safety data behind it.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=("Causes sedation and depression; ask about mood at review.",),
    ),
    _medicine(
        "atenolol",
        "Atenolol",
        "beta blocker",
        {BETA_BLOCKER},
        "Hypertension, angina and rate control.",
        "25 mg to 50 mg by mouth daily.",
        max_adult_dose="100 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Associated with fetal growth restriction, particularly when used early and for long. Labetalol or methyldopa is preferred in pregnancy.",
        breastfeeding="Accumulates in milk more than other beta blockers; watch the infant for bradycardia.",
        renal="Reduce the dose in kidney impairment.",
        cautions=(
            "Do not stop abruptly in ischaemic heart disease.",
            "Masks the warning signs of hypoglycaemia in diabetes.",
        ),
    ),
    _medicine(
        "furosemide",
        "Furosemide",
        "loop diuretic",
        {LOOP_DIURETIC},
        "Fluid overload in heart failure, kidney disease and liver disease.",
        "20 mg to 40 mg by mouth daily, increased as needed.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Not used for hypertension in pregnancy; it reduces plasma volume. Use where there is a clear cardiac or renal indication.",
        breastfeeding="May suppress lactation.",
        renal="Higher doses are needed as kidney function falls.",
        monitoring="Potassium, sodium and kidney function.",
        cautions=("Lowers potassium; check it, particularly alongside digoxin or a QT-prolonging medicine.",),
        synonyms={"frusemide", "lasix"},
    ),
    _medicine(
        "hydrochlorothiazide",
        "Hydrochlorothiazide",
        "thiazide diuretic",
        {THIAZIDE_DIURETIC},
        "Hypertension.",
        "12.5 mg to 25 mg by mouth daily.",
        max_adult_dose="50 mg in 24 hours for hypertension.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid in pregnancy; it reduces plasma volume and is not a first-line antihypertensive there.",
        breastfeeding="High doses suppress lactation.",
        monitoring="Potassium, sodium, glucose and kidney function.",
        cautions=("Lowers potassium and sodium; raises glucose and urate.",),
        synonyms={"hctz"},
    ),
    _medicine(
        "simvastatin",
        "Simvastatin",
        "statin",
        {STATIN},
        "Lowering cholesterol and cardiovascular risk.",
        "20 mg to 40 mg by mouth at night.",
        max_adult_dose="40 mg in 24 hours in routine use.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Contraindicated. Stop it before a planned pregnancy and as soon as pregnancy is confirmed; cholesterol is needed for fetal development.",
        breastfeeding="Avoid while breastfeeding.",
        hepatic="Avoid in active liver disease.",
        monitoring="Ask about muscle pain at every review. Check creatine kinase if there is unexplained muscle pain.",
        cautions=(
            "Muscle pain with dark urine needs the medicine stopped and creatine kinase measured the same day.",
        ),
    ),
    _medicine(
        "atorvastatin",
        "Atorvastatin",
        "statin",
        {STATIN},
        "Lowering cholesterol and cardiovascular risk.",
        "10 mg to 20 mg by mouth daily, increased as needed.",
        max_adult_dose="80 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Contraindicated, as for every statin.",
        breastfeeding="Avoid while breastfeeding.",
        monitoring="Ask about muscle pain at every review.",
    ),
    _medicine(
        "warfarin",
        "Warfarin",
        "oral anticoagulant",
        {ANTICOAGULANT},
        "Anticoagulation in atrial fibrillation, venous thromboembolism and mechanical heart valves.",
        "Dose is set by INR, not by a standard amount. Typical maintenance is 3 mg to 9 mg daily.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy=(
            "Contraindicated in the first trimester, where it causes a recognised embryopathy, and "
            "near term because of fetal bleeding. Heparin is used instead. A mechanical valve in "
            "pregnancy is a specialist decision, not a ward one."
        ),
        breastfeeding="Compatible with breastfeeding; warfarin does not pass into milk in meaningful amounts.",
        monitoring="INR, at the interval the anticoagulation service sets.",
        cautions=(
            "More interactions than any other medicine here. Check the INR after starting or stopping anything else.",
            "Large changes in dietary vitamin K change the INR.",
        ),
        synonyms={"coumadin"},
    ),
    _medicine(
        "metformin",
        "Metformin",
        "biguanide",
        {BIGUANIDE},
        "Type 2 diabetes.",
        "500 mg by mouth once or twice daily with food, increased gradually.",
        max_adult_dose="2 g in 24 hours in divided doses.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used in pregnancy for gestational and pre-existing type 2 diabetes, often with insulin.",
        breastfeeding="Compatible with breastfeeding.",
        renal="Reduce the dose as eGFR falls and stop below 30; lactic acidosis is the reason.",
        monitoring="Kidney function at least yearly, more often if it is falling.",
        cautions=(
            "Hold during acute illness with dehydration, and around contrast imaging.",
        ),
        synonyms={"glucophage"},
    ),
    _medicine(
        "glibenclamide",
        "Glibenclamide",
        "sulfonylurea",
        {SULFONYLUREA},
        "Type 2 diabetes where metformin is not enough or not tolerated.",
        "5 mg by mouth daily with breakfast, increased as needed.",
        max_adult_dose="15 mg in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Insulin or metformin is preferred in pregnancy. Avoid near term: it causes neonatal hypoglycaemia.",
        breastfeeding="Avoid; watch the infant for hypoglycaemia if it is used.",
        renal="Avoid in kidney impairment: it accumulates and causes prolonged hypoglycaemia.",
        monitoring="Blood glucose, and ask about hypoglycaemic episodes.",
        cautions=(
            "Hypoglycaemia from a sulfonylurea can last many hours and can recur after treatment.",
        ),
        synonyms={"glyburide", "daonil"},
    ),
)

# ---------------------------------------------------------------------------
# Other commonly asked-about medicines
# ---------------------------------------------------------------------------

_OTHERS: tuple[Monograph, ...] = (
    _medicine(
        "omeprazole",
        "Omeprazole",
        "proton pump inhibitor",
        {PROTON_PUMP_INHIBITOR},
        "Peptic ulcer, reflux, and gastric protection alongside an NSAID.",
        "20 mg by mouth daily, or 40 mg daily in severe disease.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Acceptable in pregnancy where reflux is not controlled by simpler measures.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "Reduces the absorption of medicines that need an acid stomach.",
            "Long-term use is associated with low magnesium.",
        ),
    ),
    _medicine(
        "prednisolone",
        "Prednisolone",
        "corticosteroid",
        {CORTICOSTEROID},
        "Inflammatory and allergic conditions, asthma exacerbation, and immune suppression.",
        "5 mg to 60 mg by mouth daily depending on the indication, taken in the morning.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Used in pregnancy where the indication requires it; the maternal condition usually matters more than the small risk.",
        breastfeeding="Compatible with breastfeeding at usual doses.",
        monitoring="Blood glucose and blood pressure on prolonged courses.",
        cautions=(
            "Do not stop a course of more than three weeks abruptly.",
            "Raises blood glucose and blood pressure, and increases infection risk.",
        ),
    ),
    _medicine(
        "dexamethasone",
        "Dexamethasone",
        "corticosteroid",
        {CORTICOSTEROID},
        "Cerebral oedema, severe croup, fetal lung maturation, and as an antiemetic.",
        "4 mg to 8 mg by mouth or by injection, per the indication.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Given deliberately in pregnancy for fetal lung maturation. Other uses follow the corticosteroid cautions.",
        breastfeeding="Compatible with breastfeeding at usual doses.",
        cautions=("Raises blood glucose sharply; check it in diabetes.",),
    ),
    _medicine(
        "salbutamol",
        "Salbutamol",
        "short-acting beta-2 agonist",
        {"bronchodilator"},
        "Asthma and reversible airway obstruction.",
        "100 to 200 micrograms by inhaler as needed. 2.5 mg to 5 mg by nebuliser in an exacerbation.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Continue in pregnancy. Uncontrolled asthma is far more dangerous to the pregnancy than the medicine.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "Tremor and a fast heart rate are expected at nebulised doses.",
            "Repeated nebulisers lower potassium.",
        ),
        synonyms={"ventolin", "albuterol"},
    ),
    _medicine(
        "ondansetron",
        "Ondansetron",
        "antiemetic",
        {ANTIEMETIC, QT_PROLONGING},
        "Nausea and vomiting.",
        "4 mg to 8 mg by mouth or by slow intravenous injection every 8 hours.",
        max_adult_dose="16 mg as a single intravenous dose is the ceiling; give it slowly.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Used for hyperemesis where simpler antiemetics have failed. Follow the local protocol for first-trimester use.",
        breastfeeding="Acceptable while breastfeeding.",
        cautions=("Prolongs the QT interval; give intravenous doses slowly.",),
        synonyms={"zofran"},
    ),
    _medicine(
        "diazepam",
        "Diazepam",
        "benzodiazepine",
        {BENZODIAZEPINE, SEDATING},
        "Seizures, severe anxiety, muscle spasm and alcohol withdrawal.",
        "5 mg to 10 mg by mouth or slow intravenous injection, repeated per the indication.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Avoid regular use. A single dose for status epilepticus is given where it is needed; sustained use near term causes neonatal withdrawal and floppiness.",
        breastfeeding="Avoid repeated doses; the infant becomes drowsy and feeds poorly.",
        hepatic="Reduce the dose in liver disease.",
        monitoring="Respiratory rate after intravenous doses.",
        cautions=(
            "Respiratory depression is additive with opioids and with alcohol.",
        ),
        synonyms={"valium"},
    ),
    _medicine(
        "carbamazepine",
        "Carbamazepine",
        "anticonvulsant",
        {ANTICONVULSANT, ENZYME_INDUCER},
        "Epilepsy and trigeminal neuralgia.",
        "100 mg to 200 mg by mouth once or twice daily initially, increased slowly.",
        max_adult_dose="1.6 g in 24 hours.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy=(
            "Teratogenic, including neural tube defects. Stopping an anticonvulsant abruptly is more "
            "dangerous than continuing it, so this is a decision for the epilepsy service before "
            "conception, with high-dose folic acid."
        ),
        breastfeeding="Compatible with breastfeeding; watch the infant for drowsiness.",
        monitoring="Full blood count, sodium and liver function.",
        cautions=(
            "A powerful enzyme inducer: it lowers the level of many other medicines, including hormonal contraceptives.",
            "Stop at the first sign of a rash.",
        ),
        synonyms={"tegretol"},
    ),
    _medicine(
        "phenytoin",
        "Phenytoin",
        "anticonvulsant",
        {ANTICONVULSANT, ENZYME_INDUCER},
        "Epilepsy and status epilepticus.",
        "150 mg to 300 mg by mouth daily, adjusted to level and response.",
        pregnancy_stance=PregnancyStance.CAUTION,
        pregnancy="Teratogenic. As with carbamazepine, a decision for the epilepsy service before conception rather than a change made on the ward.",
        breastfeeding="Compatible with breastfeeding.",
        monitoring="Plasma levels, full blood count and liver function.",
        cautions=(
            "The relationship between dose and level is not proportional: a small dose rise can cause toxicity.",
            "A powerful enzyme inducer.",
        ),
    ),
    _medicine(
        "ferrous_sulfate",
        "Ferrous sulfate",
        "oral iron",
        {IRON},
        "Iron deficiency anaemia.",
        "200 mg by mouth once to three times daily.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used routinely in pregnancy, with folic acid.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "Binds several antibiotics in the gut and stops them being absorbed; separate the doses.",
            "Blackens the stool. Warn the patient so it is not mistaken for bleeding.",
        ),
        synonyms={"iron", "ferrous sulphate", "iron tablets"},
    ),
    _medicine(
        "folic_acid",
        "Folic acid",
        "vitamin supplement",
        {"vitamin"},
        "Preventing and treating folate deficiency, and preventing neural tube defects.",
        "5 mg by mouth daily in pregnancy and in deficiency. 400 micrograms daily before conception.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Recommended in pregnancy. A higher dose is used where the woman takes an anticonvulsant or has had an affected pregnancy.",
        breastfeeding="Compatible with breastfeeding.",
        cautions=(
            "In megaloblastic anaemia, exclude B12 deficiency first: folate alone can mask it while the neurology progresses.",
        ),
        synonyms={"folate"},
    ),
    _medicine(
        "magnesium_sulfate",
        "Magnesium sulfate",
        "anticonvulsant for eclampsia",
        {"obstetric"},
        "Preventing and treating seizures in severe pre-eclampsia and eclampsia.",
        "Loading and maintenance doses per the national eclampsia protocol; it is a protocol medicine, not a titrated one.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="The treatment of choice for eclampsia. Given deliberately in pregnancy.",
        breastfeeding="Compatible with breastfeeding.",
        monitoring="Respiratory rate, tendon reflexes and urine output at every check while the infusion runs.",
        cautions=(
            "Have calcium gluconate at the bedside as the antidote to magnesium toxicity.",
        ),
    ),
    _medicine(
        "combined_oral_contraceptive",
        "Combined oral contraceptive",
        "combined hormonal contraceptive",
        {HORMONAL_CONTRACEPTIVE},
        "Contraception, and cycle control.",
        "1 tablet daily, per the preparation's own regimen.",
        pregnancy_stance=PregnancyStance.CONTRAINDICATED,
        pregnancy="Not used in pregnancy: stop it when pregnancy is confirmed. There is no evidence that doses taken before the pregnancy was known cause harm.",
        breastfeeding="Avoid a combined preparation in the first 6 weeks after birth; a progestogen-only method is used instead.",
        cautions=(
            "Contraindicated in migraine with aura, and in a history of venous thromboembolism.",
            "Enzyme-inducing medicines make it fail. Check what else the patient takes before relying on it.",
        ),
        synonyms={"oral contraceptive", "the pill", "combined pill", "coc", "contraceptive pill"},
    ),
    _medicine(
        "dolutegravir",
        "Dolutegravir",
        "integrase inhibitor antiretroviral",
        {ANTIRETROVIRAL},
        "HIV, as part of combination antiretroviral therapy.",
        "50 mg by mouth daily.",
        pregnancy_stance=PregnancyStance.ACCEPTABLE,
        pregnancy="Used throughout pregnancy in current guidance, including at conception, with folic acid as for any pregnancy.",
        breastfeeding="Continue treatment while breastfeeding; suppressed maternal virus is what protects the infant.",
        monitoring="Viral load per the HIV programme schedule.",
        cautions=(
            "Iron, calcium and antacids stop it being absorbed. Separate them from the dose.",
            "Rifampicin halves its level; the dose is changed, not the medicine.",
        ),
        synonyms={"dtg", "tld"},
    ),
)

MONOGRAPHS: tuple[Monograph, ...] = tuple(
    sorted(
        _ANALGESICS + _ANTI_INFECTIVES + _CARDIOMETABOLIC + _OTHERS,
        key=lambda m: m.drug_id,
    )
)


# ---------------------------------------------------------------------------
# Interactions
#
# Written class-to-class wherever the reference statement is a class statement,
# so the pack answers pairs nobody enumerated. A rule between a specific pair
# exists only where the statement really is about that pair.
# ---------------------------------------------------------------------------

INTERACTION_RULES: tuple[InteractionRule, ...] = (
    _rule(
        "anticoagulant-nsaid",
        Severity.AVOID,
        "Both increase bleeding risk, and NSAIDs also damage the gastric mucosa that is then bled from. "
        "The combination raises the risk of major gastrointestinal bleeding several times over.",
        "Use paracetamol for pain instead. Where an NSAID is unavoidable, it is a specialist decision, "
        "with gastric protection and close INR monitoring.",
        class_a=ANTICOAGULANT,
        class_b=NSAID,
    ),
    _rule(
        "anticoagulant-antiplatelet",
        Severity.SERIOUS,
        "Additive bleeding risk from two different points in haemostasis.",
        "Combine only where there is a specific indication, such as a recent stent, decided by the "
        "team that set the anticoagulation. Otherwise use one or the other.",
        class_a=ANTICOAGULANT,
        class_b=ANTIPLATELET,
    ),
    _rule(
        "warfarin-metronidazole",
        Severity.SERIOUS,
        "Metronidazole inhibits warfarin metabolism and raises the INR, often sharply and within days.",
        "Where the antibiotic is needed, check the INR within 3 to 5 days of starting and again after "
        "stopping, and expect to reduce the warfarin dose.",
        drug_a="warfarin",
        drug_b="metronidazole",
    ),
    _rule(
        "warfarin-fluconazole",
        Severity.SERIOUS,
        "Fluconazole inhibits warfarin metabolism and raises the INR.",
        "Check the INR within 3 to 5 days. A single 150 mg dose is a smaller effect than a course, "
        "but it is still an effect.",
        drug_a="warfarin",
        drug_b="fluconazole",
    ),
    _rule(
        "warfarin-fluoroquinolone",
        Severity.SERIOUS,
        "Fluoroquinolones raise the INR, partly through metabolism and partly by disturbing gut flora "
        "that produce vitamin K.",
        "Check the INR within 3 to 5 days of starting and after finishing the course.",
        drug_a="warfarin",
        class_b=FLUOROQUINOLONE,
    ),
    _rule(
        "warfarin-macrolide",
        Severity.SERIOUS,
        "Macrolides inhibit warfarin metabolism and raise the INR. Clarithromycin and erythromycin do "
        "this more than azithromycin.",
        "Check the INR within 3 to 5 days, and again after the course finishes.",
        drug_a="warfarin",
        class_b=MACROLIDE,
    ),
    _rule(
        "warfarin-cotrimoxazole",
        Severity.SERIOUS,
        "Co-trimoxazole strongly inhibits warfarin metabolism and displaces it from protein binding; "
        "the INR can rise steeply.",
        "Choose a different antibiotic where one will do. If it must be used, check the INR within "
        "3 days and monitor closely.",
        drug_a="warfarin",
        drug_b="cotrimoxazole",
    ),
    _rule(
        "anticoagulant-enzyme-inducer",
        Severity.SERIOUS,
        "Enzyme inducers such as rifampicin, carbamazepine and phenytoin increase warfarin clearance "
        "and the INR falls, leaving the patient unprotected. The INR then rises again when the inducer "
        "is stopped.",
        "Increase INR monitoring while the inducer is started and again when it is stopped. The warfarin "
        "dose usually has to change in both directions.",
        class_a=ANTICOAGULANT,
        class_b=ENZYME_INDUCER,
    ),
    _rule(
        "statin-macrolide",
        Severity.AVOID,
        "Clarithromycin and erythromycin block statin metabolism; statin levels rise several fold and "
        "with them the risk of myopathy and rhabdomyolysis.",
        "Withhold the statin for the few days of the antibiotic course, or use azithromycin, which does "
        "not inhibit metabolism in the same way.",
        class_a=STATIN,
        class_b=MACROLIDE,
    ),
    _rule(
        "statin-azole",
        Severity.SERIOUS,
        "Azole antifungals raise statin levels and with them the risk of myopathy.",
        "Withhold the statin during a short antifungal course, or reduce the statin dose on specialist advice.",
        class_a=STATIN,
        class_b=AZOLE_ANTIFUNGAL,
    ),
    _rule(
        "ace-arb",
        Severity.AVOID,
        "Dual blockade of the renin-angiotensin system gives no added benefit and causes more renal "
        "failure, hyperkalaemia and hypotension than either alone.",
        "Use one or the other, not both.",
        class_a=ACE_INHIBITOR,
        class_b=ARB,
    ),
    _rule(
        "ace-nsaid",
        Severity.SERIOUS,
        "NSAIDs oppose the antihypertensive effect and, by reducing renal perfusion, combine with an "
        "ACE inhibitor to cause acute kidney injury and hyperkalaemia. With a diuretic as well, this is "
        "the classic triple combination behind hospital-acquired renal failure.",
        "Avoid the NSAID where paracetamol will do. If it is unavoidable, keep the course short, check "
        "kidney function and potassium within a week, and make sure the patient stays hydrated.",
        class_a=ACE_INHIBITOR,
        class_b=NSAID,
    ),
    _rule(
        "arb-nsaid",
        Severity.SERIOUS,
        "As for ACE inhibitors: reduced renal perfusion, acute kidney injury and hyperkalaemia.",
        "Avoid where paracetamol will do; otherwise keep it short and check kidney function and potassium.",
        class_a=ARB,
        class_b=NSAID,
    ),
    _rule(
        "diuretic-nsaid",
        Severity.MODERATE,
        "NSAIDs blunt the effect of loop and thiazide diuretics and add renal risk.",
        "Expect less diuresis and check kidney function if the NSAID is continued.",
        class_a=LOOP_DIURETIC,
        class_b=NSAID,
    ),
    _rule(
        "thiazide-nsaid",
        Severity.MODERATE,
        "NSAIDs oppose the antihypertensive effect and add renal risk.",
        "Recheck blood pressure and kidney function if the NSAID is continued.",
        class_a=THIAZIDE_DIURETIC,
        class_b=NSAID,
    ),
    _rule(
        "nsaid-nsaid",
        Severity.AVOID,
        "Two NSAIDs together give no more pain relief and multiply the risk of gastrointestinal bleeding "
        "and kidney injury. Low-dose aspirin for cardiovascular protection is the exception and is not a "
        "second painkiller.",
        "Prescribe one NSAID only. Add paracetamol if more analgesia is needed.",
        class_a=NSAID,
        class_b=NSAID,
    ),
    _rule(
        "nsaid-corticosteroid",
        Severity.SERIOUS,
        "The risk of peptic ulceration and gastrointestinal bleeding is substantially higher with both "
        "than with either alone.",
        "Avoid the combination where possible. Where both are needed, prescribe gastric protection and "
        "keep the course as short as it can be.",
        class_a=NSAID,
        class_b=CORTICOSTEROID,
    ),
    _rule(
        "opioid-benzodiazepine",
        Severity.SERIOUS,
        "Additive respiratory depression and sedation. This combination is behind a large share of "
        "in-hospital respiratory arrests.",
        "Avoid together where possible. Where both are needed, use the lowest doses, monitor the "
        "respiratory rate and sedation score, and have naloxone available.",
        class_a=OPIOID,
        class_b=BENZODIAZEPINE,
    ),
    _rule(
        "opioid-opioid",
        Severity.AVOID,
        "Two opioids at once is duplication, not better analgesia, and the respiratory depression adds up.",
        "Use one opioid, titrated. Combining a weak and a strong opioid gives the side effects of both "
        "and the benefit of one.",
        class_a=OPIOID,
        class_b=OPIOID,
    ),
    _rule(
        "qt-qt",
        Severity.MODERATE,
        "Two medicines that prolong the QT interval add up, and the risk is torsade de pointes.",
        "Avoid combining them where an alternative exists. Where both are needed, correct potassium and "
        "magnesium first, and get an ECG if the patient has heart disease or is on other QT-prolonging "
        "medicines.",
        class_a=QT_PROLONGING,
        class_b=QT_PROLONGING,
    ),
    _rule(
        "aminoglycoside-loop",
        Severity.SERIOUS,
        "Both damage the ear and the kidney, and together they do so more often and more severely.",
        "Avoid the combination where possible. Where both are needed, monitor kidney function and "
        "gentamicin levels closely and ask about hearing daily.",
        class_a=AMINOGLYCOSIDE,
        class_b=LOOP_DIURETIC,
    ),
    _rule(
        "aminoglycoside-nsaid",
        Severity.MODERATE,
        "Both reduce renal function; together the risk of acute kidney injury rises.",
        "Check kidney function during treatment and avoid the NSAID where paracetamol will do.",
        class_a=AMINOGLYCOSIDE,
        class_b=NSAID,
    ),
    _rule(
        "tetracycline-iron",
        Severity.MODERATE,
        "Iron binds doxycycline in the gut and stops it being absorbed, so the infection goes untreated.",
        "Separate the doses by at least 2 hours before or 4 hours after the iron.",
        class_a=TETRACYCLINE,
        class_b=IRON,
    ),
    _rule(
        "fluoroquinolone-iron",
        Severity.MODERATE,
        "Iron binds ciprofloxacin in the gut and markedly reduces absorption.",
        "Separate the doses by at least 2 hours before or 4 hours after the iron.",
        class_a=FLUOROQUINOLONE,
        class_b=IRON,
    ),
    _rule(
        "dolutegravir-iron",
        Severity.SERIOUS,
        "Iron chelates dolutegravir and can drop its level far enough for the virus to escape control.",
        "Give dolutegravir at least 2 hours before, or 6 hours after, the iron. Taking them with food "
        "together is an alternative where separating them is not realistic.",
        drug_a="dolutegravir",
        class_b=IRON,
    ),
    _rule(
        "dolutegravir-rifampicin",
        Severity.SERIOUS,
        "Rifampicin induces dolutegravir metabolism and roughly halves its level.",
        "Do not stop either. The dolutegravir dose is doubled to 50 mg twice daily for as long as the "
        "rifampicin runs, and for two weeks after it stops, per the HIV programme protocol.",
        drug_a="dolutegravir",
        drug_b="rifampicin",
    ),
    _rule(
        "enzyme-inducer-contraception",
        Severity.SERIOUS,
        "Enzyme inducers such as rifampicin, carbamazepine and phenytoin lower hormonal contraceptive "
        "levels and the contraception fails.",
        "This is not a small effect. Arrange an alternative method - an intrauterine device or a "
        "depot injection - before starting the inducer, and counsel the patient explicitly.",
        class_a=ENZYME_INDUCER,
        class_b=HORMONAL_CONTRACEPTIVE,
    ),
    _rule(
        "sulfonylurea-cotrimoxazole",
        Severity.MODERATE,
        "Co-trimoxazole increases the hypoglycaemic effect of sulfonylureas.",
        "Warn the patient about hypoglycaemia and check glucose more often during the course.",
        class_a=SULFONYLUREA,
        drug_b="cotrimoxazole",
    ),
    _rule(
        "sulfonylurea-fluconazole",
        Severity.MODERATE,
        "Fluconazole raises sulfonylurea levels and with them the risk of hypoglycaemia.",
        "Check glucose more often during the antifungal course.",
        class_a=SULFONYLUREA,
        drug_b="fluconazole",
    ),
    _rule(
        "corticosteroid-sulfonylurea",
        Severity.MODERATE,
        "Corticosteroids raise blood glucose and work against oral hypoglycaemic treatment.",
        "Expect glucose to rise while the steroid runs; monitor it and adjust treatment, then adjust "
        "back when the steroid stops.",
        class_a=CORTICOSTEROID,
        class_b=SULFONYLUREA,
    ),
    _rule(
        "corticosteroid-biguanide",
        Severity.MODERATE,
        "Corticosteroids raise blood glucose and work against metformin.",
        "Monitor glucose while the steroid runs and adjust treatment, then adjust back afterwards.",
        class_a=CORTICOSTEROID,
        class_b=BIGUANIDE,
    ),
    _rule(
        "ppi-dolutegravir",
        Severity.MINOR,
        "Acid-suppressing medicines have a small effect on dolutegravir absorption, much less than iron "
        "or calcium.",
        "No routine change is needed; keep antacids and iron separated from the dose as the monograph says.",
        class_a=PROTON_PUMP_INHIBITOR,
        drug_b="dolutegravir",
    ),
    _rule(
        "phenytoin-fluconazole",
        Severity.SERIOUS,
        "Fluconazole inhibits phenytoin metabolism and levels can reach the toxic range, where the "
        "relationship between dose and level is already unpredictable.",
        "Monitor phenytoin levels during and after the antifungal course, and watch for ataxia, "
        "nystagmus and drowsiness.",
        drug_a="phenytoin",
        drug_b="fluconazole",
    ),
    _rule(
        "carbamazepine-macrolide",
        Severity.SERIOUS,
        "Clarithromycin and erythromycin inhibit carbamazepine metabolism and levels rise into the "
        "toxic range.",
        "Use azithromycin instead, or monitor carbamazepine levels through the course.",
        drug_a="carbamazepine",
        class_b=MACROLIDE,
    ),
)
