# Carpool Hunter — design system

> Stamp: `design-system: design.md` · `designed-as-app` · genre: **modern-minimal**
> Verb: `hallmark redesign` · mood: **professional** · scope: **app**

## Genre

**modern-minimal** — the polished product register: precise alignment, quiet
surfaces, one signal colour, numbers that line up, nothing decorative. The app
is a tool; the design's job is to make the answer (price, time, next step)
findable in under two seconds.

## Macrostructure family

**Workbench (app shell)** — icon rail · index of searches · canvas.
Within the family the canvas is always a **ruled plane**: a toolbar head, a KPI
strip bracketed by hairlines, one recommendation panel, a tab bar, then a list
of rows. No hero, no card grid, no marketing sections.

## Theme — graphite + mint (locked)

Anchored on the product's existing signal colour. Dark by intent: the app is
used at a station, at night, on a phone.

| Token | Value | Role |
| --- | --- | --- |
| `--ground` | `oklch(0.185 0.006 265)` | page |
| `--surface` | `oklch(0.152 0.007 265)` | recessed panels |
| `--raised` | `oklch(0.228 0.007 265)` | hover, nested panels |
| `--border` | `oklch(0.272 0.007 265)` | 1px structural rules |
| `--rule` | `oklch(0.236 0.006 265)` | inner hairlines |
| `--control` | `oklch(0.46 0.008 265)` | input / control borders |
| `--text` | `oklch(0.975 0.002 265)` | primary ink (tinted, never `#fff`) |
| `--muted` | `oklch(0.80 0.004 265)` | secondary ink |
| `--meta` | `oklch(0.655 0.005 265)` | meta, labels |
| `--accent` | `oklch(0.86 0.135 165)` | selection, focus, savings |
| `--negative` / `--warning` | `oklch(0.64 0.19 15)` / `oklch(0.78 0.12 78)` | state |

Accent budget: **≤ 5 % of the viewport**. The accent is a 1px line under the
active route, a 1px underline on the active tab, the focus ring, and the
positive delta. It is never a filled surface except the primary action's ink.

## Typography — one family, two voices

- **Family:** `"Plus Jakarta Sans"` (400 / 500 / 600 / 700). One family across
  the whole app: the display voice is weight 600-700 with tight tracking, the
  body voice is 400-500. Two typefaces were noise here — the app is one
  surface, not a document.
- **Display use:** headings, route names, prices, counts, labels. Tracking
  `-0.02em`, line-height 1.15.
- **Body use:** prose, labels, metadata, controls. Line-height 1.5 (1.55 for
  prose).
- **Outlier:** `ui-monospace` for `<code>` only. No mono in the UI.
- Fallback stack: `system-ui, -apple-system, "Segoe UI", sans-serif`.

Scale (five sizes, no more):

| Token | Size | Use |
| --- | --- | --- |
| `--text-xs` | 12px | meta, labels, tags, tab counts |
| `--text-sm` | 14px | body, controls, rows |
| `--text-md` | 18px | panel titles, offer price, secondary KPIs |
| `--text-lg` | 27px | page title (23px ≤ 760) |
| `--text-xl` | 34px | the answer: the recommended price, best price |

All numeric columns carry `font-variant-numeric: tabular-nums`.

## Space and shape

4pt scale, named: `2 4 8 12 16 24 32 48 64`. Layout: rail 52 · index 240 ·
canvas gutter 32 (24 ≤ 1200, 16 ≤ 760) · panel padding 24 · toolbar control
height 34 · form fields and the buttons beside them 44.

Radii: `3px` tags and code · `4px` controls, chips, buttons · `8px` panels ·
`12px` modal and auth card. **No pill shapes.** The instrument voice is soft
rectangles, not capsules.

## Surface logic

- The canvas is the ground. Panels are `--surface` with a 1px `--rule` border;
  the app never stacks a bordered card inside a bordered card.
- The KPI strip is **not** a card: it is a ruled band with vertical hairlines.
  Three cells (offerte, ultima ricerca, prossima ricerca); the price cell
  appears only when there is no plan, because the plan already shows the price.
- The offers list is **not** a card: flush rows on the ground, 1px rules,
  hover paints the row.
- Shadows exist only on overlays (modal, drawer, toast, auth).

## Density rules (the anti-clutter contract)

- **One price on screen.** The plan panel owns it; the strip, the step row and
  the alternatives never repeat the same figure.
- **One verdict per offer.** The badge is a single chip; the other qualifiers
  ("Nuovo", "Più veloce", "Più vicino") are plain meta text, not chips.
- **One primary action per view.** "Cerca ora" is the only filled button; the
  secondary actions (pause, edit, delete) are 34px icon buttons with
  `aria-label` + `title`.
- **Data before reasoning.** In the plan panel the structured row (partenza,
  arrivo, durata, cambi) and the journey sit on the left, the explanation on
  the right, separated by a hairline; comparisons run full width below.
- **One way out of the app.** Every external page opens with the same quiet
  text link — `apri ↗` (`.ext-link`), muted, accent underline on hover —
  both in the plan's step and in the offer rows. The "Link utili" cards keep
  their own pattern because there the whole card is the link.
- **No empty right side.** A zone that ends halfway leaves the panel
  unbalanced: either it goes two columns or it ends at the measure.

## Motion

- Three durations: `--dur-micro 120ms` · `--dur-minor 200ms` · `--dur-major 320ms`.
- One ease: `--ease-out cubic-bezier(0.16, 1, 0.3, 1)`.
- **One orchestrated entrance, once per watch change**: the detail sheet fades
  and rises 4px in 320ms. It does **not** replay on data refreshes.
- Tab change: opacity only, 200ms. No spatial movement.
- Overlays: backdrop fade 200ms; modal scale 0.995→1; drawer translateX 8px.
- Offers and skeleton bars: no animation.
- Reduced motion: all spatial motion collapses; durations ≤ 150ms (effectively
  0.01ms), opacity only.

## Microinteractions stance

- **Silent success.** No toast for delete, pause/resume, mark-all-read, or any
  action whose result is already on screen.
- Toasts remain for failures and for background jobs whose effect is invisible
  (GTFS update, push registration, test messages).
- Destructive confirmation happens in the app's own dialog, never `confirm()`.
  An **irreversible** delete asks you to type the route: a click-OK is too easy
  to press by mistake.
- Hover delay 0ms on pointer devices; transitions on background, border-color,
  color only. No lifts, no scale on hover.

## CTA voice

- **Primary:** solid paper (`--paper oklch(0.955 0.003 265)`) with `--paper-ink`,
  4px radius, 34px height, 14px/600, sentence case ("Cerca ora", "Salva").
- **Secondary / ghost:** transparent with `--control` border, or text-only.
- **Icon actions (`.icon-btn`):** 34px square, 1px `--rule` border, `--meta`
  ink that warms to `--text` on hover; the destructive one keeps `--ink-neg`
  and tints only on hover. Used for the head's secondary actions.
- **Destructive:** ghost with `--ink-neg` text (the row action), solid
  `--negative` with `--danger-ink` inside the confirmation dialog.

## Responsive floor

320 · 375 · 414 · 768 · 1440 CSS px.
No horizontal scroll (`overflow-x: clip` on html and body); tabs wrap on two
rows on mobile instead of scrolling; bottom rail labels hide at ≤ 360px; long
place names wrap with `overflow-wrap: anywhere`; the KPI strip is 4 columns on
the desktop, 2 on tablet and phone (the third cell spans the row when the
price cell is hidden).
Touch floors on the phone (≤ 760px): icon actions and tabs are 44px, the small
modal buttons 36px, form fields 44px.

## The index is grouped by cartella

The searches are not a flat list: they are grouped in **cartelle** (a trip, a
context, a person). The index in the sidebar shows one section per cartella —
the name, the number of searches, a `⋯` menu with *Rinomina* and *Elimina* —
and the searches that belong to no cartella under **"Altre ricerche"**.

- A cartella is created from the index (**+ Nuova cartella**) or from the
  search form (**Cartella → Nuova cartella…**). Renaming happens in place: the
  title becomes a field, Invio saves, Esc cancels.
- **Deleting a cartella never deletes a search**: the searches inside simply
  return to "Altre ricerche". The dialog says so.
- Cartelle and searches are both scoped to the account: two users of the same
  instance see their own. The data model is per-user from the start, so
  internal accounts need only a management screen, not a migration.

## The map is part of the system

The map tab is not an island: it uses **dark tiles** (Esri *World Dark Gray
Canvas*: a base layer plus a label layer, no API key), the graphite ground
behind them, and the popup / controls / attribution in the system colours.
Every colour it draws comes from a token (`--map-train`, `--map-bus`,
`--map-origin`, `--map-dest`, `--map-zone`, `--map-tile-url`…), read at runtime
by `token()` in `app.js` — no hex written in the script. The tile layers are
nudged down with a small `brightness()` filter so they sit inside the panel.
Leaflet's stylesheet loads **before** the app's, so the app's rules win at
equal specificity.

## Content rules

Italian copy, sentence case. No emoji as UI. No invented numbers: every figure
comes from the API. Split journeys ("spezzatura") are always shown as a
comparison, even when more expensive.

### The vocabulary (one word per thing)

| Thing | Word | Never |
| --- | --- | --- |
| the saved search | **ricerca** | "viaggio", "monitoraggio" |
| a group of searches | **cartella** | "categoria", "tag", "gruppo" |
| the agent's pass over a search | **controllo** | "esecuzione", "run", "ricerca" |
| the verb for that pass | **cerca** (the button: "Cerca ora") | "forza", "lancia" |
| a result row | **offerta** | "opzione", "combinazione" |
| a leg of the journey | **tratta** | "leg", "segmento" |
| the recommended journey | **piano** | "soluzione", "proposta" |
| the point of departure / arrival | **da** / **a** (chips) | "ritrovo" / "arrivo" (arrivo also means the arrival *time*) |
| the agent | **agente** | "AI", "assistente", "bot" |

### The formats (one way to write each)

- **Day:** `gio 22 ott` everywhere — in the chips, in the facts row, in the
  offer rows, in the index.
- **Moment:** `gio 22 ott, 07:21` (day + time) in the chips and the facts row;
  bare `07:21` in the rows, where the day sits next to it.
- **Duration:** `2h 29m` in the interface (the agent's prose may spell it out).
- **Price:** `€13,10` — always the symbol first, always two decimals.
- **Confidence:** `fiducia 95/100` (the plan) and `punteggio 91/100` (the
  offer): the same scale, never `95%` next to `91/100`.
- **Quotes:** `«…»` (Italian usage). Never straight or curly double quotes.
- **Voice:** second person singular ("cerca", "scegli"), short sentences, no
  "Grazie:" prefixes, no exclamation marks, no "Ciao".

### How much text is on screen

- The plan panel shows the **answer** (price, facts, journey) and keeps the
  agent's reasoning behind **"Perché questo piano"**. The caveats stay visible
  under **"Da tenere d'occhio"** — two short lines, never a wall.
- One line of prose per zone, 72 characters of measure at most.
- A chip states a filter, never a sentence.

## What pages MUST share

The graphite/mint palette and accent placement, the Plus Jakarta Sans family,
the five-size scale, the CTA voice, the 3/4/8/12 radius set, the ruled-plane
surface logic, the single entrance, and the Workbench shell.

## What pages MAY differ on

Only the composition inside the canvas (which panels are present for a screen).
Theme, type, radii, motion and CTA voice are locked.

## Exports

### tokens.css

The runtime file is `static/tokens.css` (linked before `styles.css`). Canonical
values:

```css
:root {
  --ground:  oklch(0.185 0.006 265);
  --surface: oklch(0.152 0.007 265);
  --raised:  oklch(0.228 0.007 265);
  --border:  oklch(0.272 0.007 265);
  --rule:    oklch(0.236 0.006 265);
  --control: oklch(0.46 0.008 265);
  --text:    oklch(0.975 0.002 265);
  --muted:   oklch(0.80 0.004 265);
  --meta:    oklch(0.655 0.005 265);
  --accent:  oklch(0.86 0.135 165);
  --negative: oklch(0.64 0.19 15);
  --warning:  oklch(0.78 0.12 78);
  --paper:      oklch(0.955 0.003 265);
  --paper-ink:  oklch(0.205 0.006 265);

  --font-display: "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", sans-serif;
  --font-body: "Plus Jakarta Sans", system-ui, -apple-system, "Segoe UI", sans-serif;

  --text-xs: 12px; --text-sm: 14px; --text-md: 18px;
  --text-lg: 27px; --text-xl: 34px;

  --space-3xs: 2px; --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px;
  --space-md: 16px; --space-lg: 24px; --space-xl: 32px; --space-2xl: 48px;
  --space-3xl: 64px;

  --radius-sm: 3px; --radius-md: 4px; --radius-lg: 8px; --radius-xl: 12px;

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-micro: 120ms; --dur-minor: 200ms; --dur-major: 320ms;

  --ctl: 34px; --ctl-form: 44px;

  --shadow: 0 8px 20px oklch(0.05 0.004 265 / 0.35);

  --map-train: oklch(0.72 0.12 225);
  --map-bus: oklch(0.80 0.11 75);
  --map-tram: oklch(0.76 0.12 340);
  --map-rideshare: oklch(0.78 0.13 55);
  --map-flight: oklch(0.80 0.10 200);
  --map-carsharing: oklch(0.82 0.12 95);
  --map-transit: oklch(0.66 0.02 265);
  --map-origin: oklch(0.86 0.135 165);
  --map-dest: oklch(0.73 0.16 15);
  --map-zone: oklch(0.84 0.11 78);
  --map-tile-url: "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Base/MapServer/tile/{z}/{y}/{x}";
  --map-tile-label-url: "https://services.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Reference/MapServer/tile/{z}/{y}/{x}";
  --map-attribution: "Esri, HERE, Garmin, © OpenStreetMap contributors";
}
```

### DTCG tokens.json

```json
{
  "color": {
    "ground": { "$value": "oklch(0.185 0.006 265)", "$type": "color" },
    "surface": { "$value": "oklch(0.152 0.007 265)", "$type": "color" },
    "ink": { "$value": "oklch(0.975 0.002 265)", "$type": "color" },
    "accent": { "$value": "oklch(0.86 0.135 165)", "$type": "color" },
    "rule": { "$value": "oklch(0.236 0.006 265)", "$type": "color" }
  },
  "font": {
    "display": { "$value": "Plus Jakarta Sans", "$type": "fontFamily" },
    "body": { "$value": "Plus Jakarta Sans", "$type": "fontFamily" }
  },
  "space": {
    "md": { "$value": "16px", "$type": "dimension" },
    "lg": { "$value": "24px", "$type": "dimension" }
  }
}
```

### shadcn/ui variables

```css
:root {
  --background: 0.185 0.006 265;
  --foreground: 0.975 0.002 265;
  --primary: 0.86 0.135 165;
  --primary-foreground: 0.205 0.006 265;
  --muted: 0.228 0.007 265;
  --muted-foreground: 0.655 0.005 265;
  --border: 0.272 0.007 265;
  --input: 0.46 0.008 265;
  --ring: 0.86 0.135 165;
  --radius: 4px;
}
```
