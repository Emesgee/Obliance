# ADR-0025: Storage-facade — én port, lokal disk i dev, objektstorage i drift

- **Status:** Accepted
- **Date:** 2026-09-03
- **Area:** documents · infra
- **Deciders:** Project owner
- **Related:** ADR-0006 (versioner er uforanderlige; nøgle `{org}/{contract}/{document}/
  {version_no}/{filnavn}`), ADR-0007 §3 (objektstorage på Hetzner, EU), ADR-0012
  (opbevaring sletter via egen rolle), ADR-0023 (app-lag kender ikke driftsmiljøet);
  bidflow ADR-0007 (storage facade over lokal/S3) — overført med nyt domænesprog.

## Kontekst

ADR-0006 gjorde filen til grundlaget for alt andet: citations, forslag og revision peger
på en version, og den version må aldrig ændre sig. Hvor bytes ligger, er derimod et
driftsvalg: udvikleren på en Windows-pc uden Docker skal kunne uploade en PDF til en
mappe; Hetzner-miljøet skal skrive til objektstorage i EU med versionering og
uforanderlighed slået til (ADR-0007). Bidflow løste det samme med én facade og to
backends, og den model var det eneste i bidflows filhåndtering, der ikke gav problemer.

Der er også en sikkerhedsdimension: et filnavn er brugerinput. `../../etc/passwd` som
`original_filename` må hverken forlade tenantens præfiks på disk eller blive til en
nøgle, der kolliderer med en anden tenants.

## Beslutning

### 1. Én port, to backends, valgt af konfiguration

`app.core.storage` er den eneste kode, der rører bytes. Den udstiller fem operationer og
intet andet: `save(key, data)`, `read_bytes(key)`, `exists(key)`, `delete_prefix(prefix)`
og `materialize(key) → Path` (en læsbar fil til værktøjer, der kræver en sti — PyMuPDF,
LibreOffice). Ingen anden modul importerer `boto3`, åbner `storage_root` eller kender
forskel på backends.

`STORAGE_BACKEND=local` skriver under `STORAGE_ROOT` (default `./storage`, git-ignoreret)
og er det eneste, der findes i dev, test og CI. `STORAGE_BACKEND=s3` er besluttet, men
endnu ikke bygget; kaldes den, fejler den højlydt (`NotImplementedError` med henvisning
hertil), og konfigurationen **nægter at starte** i `staging`/`prod` med `local` — en
Hetzner-node må ikke stille og roligt gemme kontrakter på sin egen disk.

### 2. Nøglen er tenancy

Nøglen er ADR-0006's: `{org}/{contract}/{document}/{version_no}/{filnavn}`. Den bygges
udelukkende af serverens egne UUID'er og et **saniteret** filnavn (`safe_filename`: kun
sidste sti-element, kontroltegn og separatorer fjernet, længde begrænset, aldrig tom).
Backenden afviser desuden enhver nøgle, der opløses uden for roden. Derfor kan en
tenant ikke navngive sig ind i en andens præfiks, og en driftsoperatør kan læse
tenancy og version direkte af stien — også når databasen ikke er ved hånden.

### 3. Sletning er ikke app-lagets

`delete_prefix` findes, fordi ADR-0012's opbevaringsjob skal kunne fjerne et helt
dokument eller en kontrakt, og fordi test skal rydde op. App-rollen kalder den aldrig
fra en HTTP-rute; ADR-0006 gav samme rolle intet `DELETE` på `document_versions`, og
storage følger databasen: en version, der findes i tabellen, findes også i storage,
byte-identisk (`sha256` bekræfter det).

### 4. Konverterede PDF'er er også objekter

Word/Excel/PowerPoint konverteres til PDF (LibreOffice headless, bidflow 0049) og
resultatet gemmes under samme præfiks som `.../{version_no}/{filnavn}.pdf` med
`pdf_storage_key` i versionen. Sidetekst og klausuler udtrækkes altid af PDF'en, så
citations peger på den samme paginering, som brugeren ser i fremviseren.

## Diagram

Fravalgt. Beslutningen er en enkelt indirektion (én port, to implementeringer) uden
proces- eller tilstandsforløb; ADR-0006's flowchart viser allerede, hvornår filer
skrives og læses, og en boks med to pile ville ikke tydeliggøre noget, prosaen ikke
allerede siger.

## Konsekvenser

- Dev og test kører uden netværk og uden hemmeligheder for storage; CI bruger samme
  lokale backend i en midlertidig mappe.
- S3-backenden skal bygges før første staging-deploy (ADR-0007 §3: Hetzner Object
  Storage, bucket pr. miljø, versionering + object lock). Indtil da er `staging`/`prod`
  bevidst umulige at starte.
- `materialize` kopierer objekter til en scratch-mappe for værktøjer, der kræver en sti;
  for lokal backend er det gratis (samme fil), for S3 er det én download pr. ingest.
- Tests, der skal findes: `safe_filename` neutraliserer `..`, separatorer og
  kontroltegn; en nøgle uden for roden afvises; `read_bytes` efter `save` er
  byte-identisk; app-konfiguration med `local` i `prod` fejler ved opstart.

## Alternativer overvejet

- **Skriv direkte i Postgres (`bytea`).** Afvist: backup og PITR (ADR-0007) bliver
  tunge af filer, der aldrig ændrer sig; objektstorage har uforanderlighed som
  primitiv.
- **Altid S3, også lokalt (MinIO i Docker).** Afvist: udviklingsmaskinen kan ikke køre
  Docker; en lokal mappe er nok til at afprøve ADR-0006's regler.
- **Lade hver modul kalde storage-SDK'et selv.** Afvist: det var vejen til bidflows
  første filfejl; én facade er også det eneste sted, nøgle-sanitering kan garanteres.
