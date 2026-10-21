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

## Typography — pairing

- **Display:** `"Geist"`, 500/600. Headings, route names, prices, counts.
  Tracking `-0.02em`, line-height 1.15.
- **Body:** `"Public Sans"`, 400/500. Prose, labels, metadata, controls.
  Line-height 1.5 (1.55 for prose).
- **Outlier:** `ui-monospace` for `<code>` only. No mono in the UI.

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
  and rises 4px in 320ms. It does **not** replay on data refreshes.- Tab change: opacity only, 200ms. No spatial movement.
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
place names wrap with `overflow-wrap: anywhere`; the KPI strip is 4 columns →
2 → 2.

## Content rules

Italian copy, sentence case. No emoji as UI. No invented numbers: every figure
comes from the API. Split journeys ("spezzatura") are always shown as a
comparison, even when more expensive.

## What pages MUST share

The graphite/mint palette and accent placement, the Geist + Public Sans pairing,
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

  --font-display: "Geist", "Public Sans", system-ui, sans-serif;
  --font-body: "Public Sans", system-ui, -apple-system, "Segoe UI", sans-serif;

  --text-xs: 12px; --text-sm: 14px; --text-md: 18px;
  --text-lg: 27px; --text-xl: 34px;

  --space-3xs: 2px; --space-2xs: 4px; --space-xs: 8px; --space-sm: 12px;
  --space-md: 16px; --space-lg: 24px; --space-xl: 32px; --space-2xl: 48px;
  --space-3xl: 64px;

  --radius-sm: 3px; --radius-md: 4px; --radius-lg: 8px; --radius-xl: 12px;

  --ease-out: cubic-bezier(0.16, 1, 0.3, 1);
  --dur-micro: 120ms; --dur-minor: 200ms; --dur-major: 320ms;

  --ctl: 34px; --ctl-form: 44px;
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
    "display": { "$value": "Geist", "$type": "fontFamily" },
    "body": { "$value": "Public Sans", "$type": "fontFamily" }
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
