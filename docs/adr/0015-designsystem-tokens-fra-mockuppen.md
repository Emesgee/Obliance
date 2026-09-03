# ADR-0015: Designsystem — tokens trukket ud af mockuppen, med kontrasten rettet

- **Status:** Accepted (2026-09-03)
- **Date:** 2026-09-03
- **Area:** frontend-ux
- **Deciders:** Project owner
- **Related:** ADR-0004 (AI-forslag skal se anderledes ud end registret), ADR-0005
  (kilde-chips), ADR-0013 (beregningsgrundlag vises); `docs/adr-plan.md` (N18);
  bidflow ADR-0038 (designsystem retunet til prototypen), ADR-0036 (dansk UI-tekst),
  ADR-0032 (tastaturnavigerbare tabelrækker), ADR-0027 (hjælpekort)

## Kontekst

Bidflow ADR-0038 gjorde præcis denne øvelse: trak farver, typografi, radius og
komponentklasser ud af en prototype og gjorde dem til tokens, som cascader ud i hele
appen. Det virkede, og mønstret overføres. Det, der skal fastlægges her, er **hvilke
værdier** og **hvad der skal rettes undervejs**.

Mockuppens faktiske tokens (aflæst af koden, ikke af skærmbilledet):

```
navy #122647 · navy2 #1E3A66 · navySoft #24447A · accent #2E5FB7
ink #17202E · slate #5C6B84 · grey #8A97AB · line #E3E8F0
bg #F6F8FB · card #FFFFFF
green #1B8A57 / greenBg #E5F5EC · yellow #B27A10 / yellowBg #FBF1DA
red #C23A3A / redBg #FBE7E7 · grey #8A97AB / greyBg #EEF1F6 · blueBg #E9F0FB
aiViolet #4B5AA6 · aiBg #EDF0FB
radius 14 · Inter · ui-monospace
shadow 0 1px 2px rgba(18,38,71,.05), 0 4px 14px rgba(18,38,71,.05)
```

To ting i den palet er beslutninger værd at bevare bevidst:

1. **AI har sin egen farve.** `aiViolet`/`aiBg` bruges kun om AI-forslag og
   sikkerhedsniveauer — adskilt fra `accent` (handling) og fra status-farverne. Det er
   den visuelle halvdel af ADR-0004: et forslag ser anderledes ud end registret, før man
   læser et ord.
2. **Status er tre farver med hver sin bløde baggrund**, plus `grey` til *data mangler* —
   ikke som fejl, men som en fjerde, ligeværdig tilstand (KPI'ernes grå, N19).

Og én ting, mockuppen gør, som ikke skal med: `<meta name="viewport" content="width=1280">`.
Prototypen er låst til desktopbredde.

## Beslutning

### 1. Tokens først, komponenter derefter

`tokens.css` med CSS-variabler er **eneste kilde**; Tailwind-config og
komponentklasser læser fra dem. Ingen hex-værdi i en komponent. Samme disciplin som
bidflow ADR-0038, hvor tokennavnene forblev stabile, mens værdierne blev retunet.

Token-grupperne: `--navy*`, `--accent`, `--ink/--slate/--muted`, `--line`, `--bg/--card`,
status (`--ok/--warn/--crit/--none` med `-bg`-varianter), AI (`--ai/--ai-bg`),
`--radius: 14px`, `--shadow`, `--shadow-lg`.

### 2. Kontrasten rettes — fire værdier ændres

Paletten er målt mod WCAG 2.2 AA (4,5:1 for brødtekst, 3:1 for store tekster og
UI-komponenter). Resultatet af målingen:

| Kombination | Målt | Vurdering |
|---|---|---|
| `ink` på `card` / `bg` | 16,4 / 15,4 | fint |
| `slate` på `card` / `bg` | 5,40 / 5,07 | fint |
| `accent` på `card` | 6,11 | fint |
| hvid på `navy` / `accent` | 15,1 / 6,11 | fint |
| `aiViolet` på `aiBg` | 5,57 | fint |
| **`grey` på `card`** | **2,96** | **falder** |
| **`green` på `greenBg`** | **3,86** | under AA for tekst |
| **`yellow` på `yellowBg`** | **3,29** | under AA for tekst |
| **`red` på `redBg`** | **4,47** | lige under AA for tekst |

De fire rettes, med samme kulør, kun mørkere:

| Token | Mockup | Rettet | Ny kontrast |
|---|---|---|---|
| `grey` (sekundær tekst) | `#8A97AB` | **`#66728A`** | 4,84 på card · 4,55 på bg |
| `green` | `#1B8A57` | **`#136B44`** | 5,79 på greenBg · 6,53 på card |
| `yellow` | `#B27A10` | **`#7D540A`** | 5,95 på yellowBg · 6,68 på card |
| `red` | `#C23A3A` | **`#9B2727`** | 6,52 på redBg · 7,74 på card |

De oprindelige, lysere værdier beholdes som `--ok-mark`, `--warn-mark`, `--crit-mark` til
**ikke-tekstlige** markeringer (prikker, streger, diagramfarver), hvor 3:1 er kravet.
`line #E3E8F0` (1,23) beholdes uændret — en tabellinje er dekoration, ikke information.

Begrundelsen er ikke kun principiel: kunden er en offentligt ejet organisation, og
tilgængelighedskrav indgår i offentlige indkøb. Fire hex-værdier nu er billigere end en
udbedring senere.

### 3. Responsivitet: desktop først, men ikke desktop-låst

`width=1280` fjernes. Produktet designes til **desktop først** — det er et arbejdsværktøj
med brede tabeller — men layoutet skal kunne bruges ned til tabletbredde:

- Sidebaren kollapser til ikoner under 1100 px.
- Tabeller får `overflow-x: auto` i egen container, så siden aldrig scroller vandret.
- Detaljesider går fra to kolonner til én under 900 px.
- Ingen funktionalitet er kun tilgængelig via hover.

Fuld telefonunderstøttelse er **ikke** i v1 — men en låst viewport, der gør appen
ubrugelig på en iPad i et leverandørmøde, er en unødvendig begrænsning.

### 4. Komponentklasser, der bærer beslutningerne

Byggeklodserne oversætter arkitekturen til noget synligt:

- **`.ai-suggestion`** — violet venstrekant, agentnavn, sikkerhedsniveau, Godkend/Afvis.
  Bruges ens for forpligtelser, risici, RACI og fakturaafvigelser, fordi de deler
  tilstandsmaskine (ADR-0004).
- **`.citation-chip`** — `Hovedkontrakt · s. 12 · pkt. 8.2`, klikbar, med
  advarselsvariant når `verified = false` (ADR-0005).
- **`.calc-basis`** — beregningsgrundlaget som én linje med tabulære tal (ADR-0013).
- **`.status-dot` / `.pill`** — fire tilstande inkl. *data mangler*.
- **`.data-table`** — med bidflow ADR-0032's tastaturnavigation som standard, ikke som
  tilvalg.
- **`.kpi-tile`**, **`.kanban`** — som bidflow ADR-0038's tilsvarende klasser.

### 5. Tal og typografi

- **Inter** til brødtekst, **`ui-monospace`** til tal, id'er, beløb og datoer.
- `font-variant-numeric: tabular-nums` overalt hvor tal står i kolonner — beløb,
  procenter, datoer. En bodsopgørelse med proportionale cifre er svær at læse og let at
  fejllæse.
- Beløb formateres dansk (`1.211,60 kr.`), datoer `dd-mm-åååå` som i mockuppen.

## Diagram — bevidst fravalgt

Et designsystem vises bedst i det medie, det virker i. Beslutningens indhold er
konkrete værdier (tabellerne i §2), en klasseliste (§4) og et par regler — en
Mermaid-graf ville hverken vise farve, kontrast eller typografi og dermed ikke kunne
formidle det, beslutningen handler om. Den rigtige "illustration" er en levende
token-side i appen (`/design` i dev-miljøet), som viser hver token og hver
komponentklasse med sin målte kontrast. Den bygges sammen med tokens.

## Konsekvenser

- Mockuppen kan bygges 1:1 bortset fra fire farveværdier og den låste viewport — begge
  ændringer er målbare forbedringer, ikke fortolkninger.
- **AI-violetten bliver en regel, ikke en dekoration:** ser brugeren violet, er det et
  forslag, ingen har godkendt endnu. Den regel skal håndhæves i review, ellers mister
  farven sin betydning.
- Token-siden i dev-miljøet gør kontrastkravet til noget, man kan se, i stedet for noget,
  man husker.
- De to sæt statusfarver (tekst og markering) er en smule ekstra kompleksitet i
  paletten. Alternativet — én mørk farve til alt — ville gøre prikker og
  diagramflader mørkere og mere dominerende, end mockuppen er.
- Tilgængelighed ud over kontrast (tastaturnavigation, fokusmarkering, skærmlæser-labels,
  reduceret bevægelse) er **ikke** dækket af denne ADR. ADR-0032's overførsel dækker
  tabellerne; resten fortjener sin egen gennemgang før første kunde.
- Tests/tjek: en CI-kontrol, der fejler ved en hex-værdi uden for `tokens.css`; en
  kontrastkontrol over token-parrene i §2; token-siden rendres i dev.

## Alternativer overvejet

- **Byg mockuppens palet uændret.** Afvist: fire kombinationer falder under AA, og
  produktet sælges til en offentlig kunde. Rettelsen koster fire hex-værdier nu.
- **Skift til et færdigt komponentbibliotek** (MUI, Ant, shadcn). Afvist: mockuppen *er*
  designsproget, og en tilpasning af et bibliotek til den ville koste mere end at bygge
  de ti klasser, produktet består af.
- **Fuld responsivitet inkl. telefon i v1.** Fravalgt som scope: et kontraktværktøj med
  brede tabeller og PDF-visning bruges ikke på telefon. Tablet er grænsen.
- **Beholde `width=1280`.** Afvist: gør appen ubrugelig på enheder, den ellers ville
  fungere fint på, uden at give noget igen.
- **Én mørk statusfarve til både tekst og markering.** Afvist: gør prikker og
  diagramflader tungere og fjerner mockuppens lette udtryk.

## Afklaringer (2026-09-03, besluttet af project owner)

1. **De fire rettede farveværdier vedtages:** `grey #66728A`, `green #136B44`,
   `yellow #7D540A`, `red #9B2727`. Samme kulør, målt over AA; de lyse originaler
   beholdes som `--ok-mark`, `--warn-mark`, `--crit-mark` til ikke-tekstlige markeringer.
2. **Tabletgrænse ved 900–1100 px**, ikke fuld responsivitet. `width=1280` fjernes.
3. **Intet mørkt tema i v1.** Tokens struktureres, så det kan tilføjes uden omskrivning.
