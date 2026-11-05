// The node name in an envelope follows the naming preset (ADR-018), and the preset
// is the workplace's own choice — the extension never hardcodes one. PERSONA.md is
// the only workplace file that already carries the node's own name, in the
// template's anchor line `Your name: {node_name}.` (prompt_template.md): any
// present or future preset renders there as `<prefix> <serial:05d>`, so the prefix
// and the own serial are read from that line alone; without it the envelope falls
// back to the bare serial.

export interface Naming {
  /** The preset's name prefix («узел» / «Мисака» / whatever the next preset is). */
  prefix: string | null;
  /** This node's own serial, or null when the anchor is missing. */
  serial: number | null;
}

const ANCHOR_RE = /Your name:\s*([^.]+)\./;
const NAME_RE = /^(.+?)\s*0*(\d{1,5})$/;

/** Extract the naming preset and own serial from the node's PERSONA.md text. */
export function parseNaming(personaText: string): Naming {
  const anchor = ANCHOR_RE.exec(personaText);
  if (anchor === null) return { prefix: null, serial: null };
  const name = NAME_RE.exec(anchor[1].trim());
  if (name === null) return { prefix: null, serial: null };
  return { prefix: name[1].trim(), serial: parseInt(name[2], 10) };
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
