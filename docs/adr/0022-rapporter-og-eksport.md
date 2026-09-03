# ADR-0022: Rapporter og eksport — afledte udtræk, samme forespørgsel som skærmen, hver eksport logget

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** frontend-ux
- **Deciders:** Project owner
- **Related:** ADR-0001 (afledte felter beregnes ét sted), ADR-0002 (RLS gælder
  udtræk), ADR-0003 (`eksport`- og `okonomi`-tilladelserne; Auditor), ADR-0004
  (ubesluttede forslag er ikke registerdata), ADR-0007 (objektstorage til store filer,
  presigned URL), ADR-0011 (eksport logges; læsning af fortroligt logges), ADR-0012
  (GDPR-eksport er en anden ting), ADR-0016 (modeloutput i udtræk), ADR-0017 (ingen
  kanal til leverandøren); `docs/adr-plan.md` (N20); bidflow ADR-0008 (GDPR-eksport som
  ZIP), ADR-0036 (dansk format)

## Kontekst

Mockuppens *Rapporter* er fire navngivne udtræk, hver defineret som **kolonner + en
afledning af registret** — ikke som gemte rapporter:

| Rapport | Kolonner | Afledning |
|---|---|---|
| Kontraktportefølje | Kontrakt-ID, Navn, Leverandør, Kategori, Status, Årlig værdi (kr.), Udløb, Risiko, Niveau | alle kontrakter |
| Kontraktudløb (12 mdr.) | Kontrakt-ID, Navn, Udløbsdato, Måneder til udløb, Opsigelsesvarsel, Optioner, Anbefalet næste skridt | udløb ≤ 12 mdr., ikke arkiveret, sorteret efter udløb |
| Risikorapport | Risiko-ID, Kontrakt, Titel, Kategori, Score, Status, Ansvarlig, Frist | alle risici, score = sandsynlighed × konsekvens |
| Fakturaafvigelser | Faktura, Kontrakt, Beløb (kr.), Afvigelse (kr.), Status, Anbefaling | alle fakturaer |

Eksporten er *"semikolonsepareret, dansk Excel-format"*, viser *"genereret <dato> · N
rækker"*, og skriver auditrækken `Rapport eksporteret · Kontraktportefølje (CSV, 42
rækker)`. Tilladelsen `eksport` findes i matricen (ADR-0003); Auditor-rollen har **kun**
`eksport` og `audit` — dens hele formål er at læse og tage med.

To prototypegenveje skal ikke med: CSV'en vises i et **tekstfelt** til markér-og-kopiér
(et rigtigt produkt downloader en fil), og semikoloner i værdier **erstattes med komma**
(`replace(/;/g, ",")`) — det ændrer data i stedet for at citere dem.

Kravet er: udtræk, der er de samme tal som skærmen, i et format Excel på dansk åbner
korrekt, med de samme rettigheder som skærmen, og et spor pr. udtræk.

## Beslutning

### 1. Rapporter er et register i kode — ikke gemte filer, ikke gemte forespørgsler

`app/reports/registry.py` definerer hver rapport som `(key, navn, kolonner,
forespørgsel, kræver_okonomi, standardsortering)`. Forespørgslen er **den samme
funktion, listevisningen bruger** — ADR-0001's princip om, at et afledt tal beregnes ét
sted, gælder også her. "Måneder til udløb" i rapporten og på Kontrakter-siden kan ikke
være to tal.

De fire fra mockuppen seedes, plus tre, der følger af tidligere ADR'er:

| key | Kilde | `kræver_okonomi` |
|---|---|---|
| `portefolje` | mockup | ja (årlig værdi) |
| `udloeb` | mockup | nej |
| `risici` | mockup | nej |
| `fakturaafvigelser` | mockup | ja |
| `forpligtelser` | ADR-0004/0017 — status, frist, ansvarlig, kilde | nej |
| `krav` | ADR-0013 — beløb, status, grundlag, signaturer | ja |
| `auditlog` | ADR-0011 §5 — filtreret udtræk | nej (kræver `audit`) |

Nye rapporter er en kodeændring med test — ikke en brugerdefineret forespørgsel. En
"byg din egen rapport"-editor er bevidst ikke i v1.

### 2. Rettigheder: de samme som skærmen, plus `eksport`

- Udtræk kører i brugerens tenant- og brugerkontekst (ADR-0002). En Business User uden
  adgang til en fortrolig kontrakt får den ikke med i porteføljerapporten — ikke fordi
  rapporten filtrerer, men fordi databasen gør.
- **`eksport`** kræves for at downloade; uden den kan rapporten ses på skærmen (tabel),
  men ikke tages med.
- Rapporter med `kræver_okonomi = ja` kræver **også `okonomi`** — og en bruger med
  `eksport` uden `okonomi` får rapporten **uden beløbskolonnerne**, ikke en fejl (ADR-0003
  §2's feltmaskering, anvendt på kolonner).
- `auditlog` kræver `audit` — det er Auditor-rollens rapport.

### 3. Formater: én rækkemængde, to filer

Alle rapporter genereres fra samme rækker til:

- **CSV, dansk Excel-format:** UTF-8 **med BOM** (ellers gætter Excel Latin-1 og ødelægger
  æøå), **semikolon** som skilletegn, **komma** som decimaltegn, datoer `dd-mm-åååå`,
  beløb uden tusindseparator (`612500,00` — Excel formaterer selv), og **RFC 4180-
  citering** af værdier med semikolon, anførselstegn eller linjeskift. Mockuppens
  `;`→`,`-erstatning erstattes af korrekt citering; ingen værdi ændres af eksporten.
- **XLSX:** samme rækker, typede kolonner (tal som tal, datoer som datoer), fastfrosset
  overskriftsrække, kolonnebredder. Det fjerner hele klassen af "Excel åbnede filen
  forkert"-supportsager, og koster ét bibliotek i worker'en.

Hver fil har en **metadata-linje/-fane**: rapportnavn, genereret (dato og tid), af hvem,
filtre, antal rækker, og organisationens navn. Mockuppens *"genereret 30-08-2026 · 42
rækker"* bliver dermed en del af filen, ikke kun af skærmen.

### 4. Hver eksport logges — og læsning af fortroligt tæller

- Én auditrække pr. download (ADR-0011): `rapport_eksporteret` med `key`, format,
  filtre, rækkeantal og — når udtrækket indeholder **fortrolige** kontrakter — listen af
  deres id'er i `details`. Det er ADR-0011's afklaring 2 (læsning af fortroligt logges)
  anvendt på udtræk: en eksport er en læsning, der forlader systemet.
- Metadata-linjen i filen og auditrækken bærer samme `export_id`, så en fil, der dukker op
  et forkert sted, kan spores til sin download.

### 5. Store udtræk kører i worker'en

Under 5.000 rækker genereres filen synkront og streames. Over det enqueues et job
(ADR-0010), filen lægges i objektstorage under `{org}/exports/{export_id}` (ADR-0007),
og brugeren får en notifikation (ADR-0017, kind `opgave`) med en presigned URL, der
udløber efter 24 timer. Filen slettes efter 7 dage — den er et udtræk, ikke et arkiv;
kilden er registret.

### 6. Hvad et udtræk aldrig indeholder

- **Ubesluttede AI-forslag** (ADR-0004): registret er det godkendte. Et filter "medtag
  forslag" findes på skærmen, ikke i eksporten — en revisor skal ikke skulle gætte, hvad
  der er besluttet.
- **Modelgenereret fritekst uden markering:** "Anbefalet næste skridt" i udløbsrapporten
  er ADR-0001's afledte `naeste_deadline`-tekst (kode), ikke et copilot-svar. Kolonner,
  der stammer fra en model (fx en agents `rationale`), mærkes i overskriften med
  "(AI-forslag)" og er fravalgt som default.
- **Prompt- eller systemdata:** ingen agentkørsler, tokens eller model-id'er i
  kunderapporter (ADR-0008/0014: det er operatørens tal, bag `okonomi` i Administration —
  ikke i et udtræk, der sendes videre).
- **En modtager uden for organisationen:** eksport er en download til den, der klikker.
  Der er ingen "send rapport til"-funktion, og leverandøren er ikke en modtager
  (ADR-0017 §4).

### 7. Tre ting, der ikke er denne ADR

- **GDPR-eksporten** (bidflow ADR-0008 → ADR-0012 §4) er organisationens *komplette* data
  som ZIP, til portabilitet — ikke en rapport, og ikke bag `eksport` men bag
  Systemadministrator.
- **Auditlog-eksporten** defineres i ADR-0011 §5; den er registreret her som rapport
  `auditlog`, fordi den deler format og logning, men reglerne for indhold er 0011's.
- **Planlagte rapporter** (månedlig porteføljerapport pr. mail til Contract Owner) er
  **ikke** i v1 — se åbne spørgsmål.

## Diagram — bevidst fravalgt

Beslutningen er et register (tabellen i §1), to formatregler (§3) og en rettighedsregel,
der er ADR-0003's anvendt på kolonner. Downloadstien er trivielt lineær — forespørgsel,
maskering, fil, auditrække — og de to kilder, den trækker på (RLS i ADR-0002, feltmaskering i
ADR-0003), er allerede tegnet der. Det eneste, et diagram kunne vise, er tabellen i §1 som
bokse. Vurderet og fravalgt.

## Konsekvenser

- **Skærm og fil kan ikke være uenige**, fordi de er samme forespørgsel. Det lyder
  selvfølgeligt og er det ikke — parallelle "rapport-queries" er den klassiske kilde til
  "tallet i Excel passer ikke med systemet".
- **XLSX ved siden af CSV** koster et bibliotek og en ekstra kodesti, og sparer den
  supportklasse, hvor Excel på dansk gætter forkert om tegnsæt og skilletegn.
- **Kolonnemaskering** gør, at samme rapport har to former afhængigt af `okonomi`. Det
  er korrekt og skal stå i metadata-linjen ("beløbskolonner udeladt: mangler tilladelse"),
  så en fil uden beløb ikke læses som en fil uden beløbsdata.
- Logning af fortrolige kontrakt-id'er i auditrækken betyder, at én stor eksport giver
  én lang `details`. Det er rigtigt; alternativet er, at en eksport af 40 fortrolige
  kontrakter ikke kan efterprøves.
- Asynkrone store udtræk kræver, at brugeren kommer tilbage. Notifikationen og 24-timers
  linket er kompromiset; en synkron 50.000-rækkers XLSX ville time ud.
- "Byg din egen rapport" vil blive efterspurgt. Svaret i v1 er: de syv rapporter dækker
  mockuppens og ADR'ernes behov, og en ny rapport er en lille kodeændring — ikke en
  editor, der kan omgå kolonnemaskering.
- Tests/tjek: CSV åbner korrekt i dansk Excel (BOM, semikolon, komma-decimal) — verificeret
  manuelt én gang og fastholdt med en byte-test; en værdi med semikolon citeres og ændres
  ikke; en bruger med `eksport` uden `okonomi` får porteføljerapporten uden
  beløbskolonner og med metadata-note; en Business User uden adgang får ikke fortrolige
  kontrakter med; hver download giver præcis én auditrække med `export_id`, og id'et står
  i filen; ubesluttede forslag optræder aldrig i en fil; et udtræk over 5.000 rækker ender
  i objektstorage med udløbende link.

## Alternativer overvejet

- **Tekstfelt med CSV til kopiering (mockuppen).** Afvist: prototypegenvej; et rigtigt
  udtræk er en fil med korrekt tegnsæt og citering.
- **Erstat semikolon med komma i værdier (mockuppen).** Afvist: ændrer data. RFC 4180-
  citering findes netop for det.
- **Kun CSV.** Afvist: dansk Excel og CSV er en supportbyrde, XLSX er billig.
- **Gemte, brugerdefinerede rapporter (forespørgselseditor).** Afvist for v1: kan omgå
  kolonnemaskering, kan ikke testes, og behovet er dækket af registret.
- **Parallel rapport-SQL adskilt fra skærmens forespørgsler.** Afvist: ADR-0001's princip
  — ét sted, ét tal.
- **Log kun "rapport eksporteret" uden fortrolige id'er.** Afvist: gør ADR-0011's
  læselogning af fortroligt meningsløs for den kanal, der faktisk fører data ud.
- **"Send rapport til e-mail".** Afvist: en eksport er en download til den, der klikker.
  Mail med kontraktdata er ADR-0007 §6's grænse, og leverandøren er aldrig modtager.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **XLSX er med fra v1** ved siden af CSV — ét bibliotek og én kodesti, og det fjerner
   den mest forudsigelige supportsag.
2. **Ingen planlagte mail-rapporter i v1.** Mail med beløb er ADR-0007 §6's grænse; når
   behovet kommer, er svaret en notifikation med link til en genereret fil (§5).
3. **5.000 rækker som grænse for synkron generering**, konfigurerbar og justeret efter
   målt svartid i staging.
