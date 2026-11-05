// The node name in an envelope follows the naming preset (ADR-018), and the preset
// is the workplace's own choice — the extension never hardcodes one. PERSONA.md is
// the only workplace file that already carries the preset's name format
// («узел 00007» / «Мисака 00007»), so the prefix is read from there at session
// start; without it the envelope falls back to the bare serial.

export interface Naming {
  /** The preset's name prefix («узел» / «Мисака») or null when unknown. */
  prefix: string | null;
}

const NAME_RE = /(узел|Мисака)\s+0*(\d{1,5})\b/;

/** Extract the naming preset from the node's own PERSONA.md text. */
export function parseNaming(personaText: string): Naming {
  const match = NAME_RE.exec(personaText);
  return { prefix: match ? match[1] : null };
}

/** The name of one board/comms actor in an envelope line. */
export function makeNameOf(naming: Naming): (actor: unknown) => string {
  return (actor: unknown): string => {
    const text = actor === null || actor === undefined ? "" : String(actor);
    if (/^\d+$/.test(text)) {
      if (naming.prefix === null) return text;
      return `${naming.prefix} ${String(parseInt(text, 10)).padStart(5, "0")}`;
    }
    return text || "?";
  };
}
