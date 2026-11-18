import strings from "../i18n.json";

type Lang = "es" | "en";
const table = strings as Record<Lang, Record<string, string | string[]>>;
let lang: Lang = navigator.language.toLowerCase().startsWith("es") ? "es" : "en";

export function setLang(l: string) {
  if (l === "es" || l === "en") {
    lang = l;
    document.documentElement.lang = l;
  }
}

export function getLang(): Lang {
  return lang;
}

/** Texto traducido; {nombre} se sustituye por el valor de `args`. */
export function tr(key: string, args: Record<string, string | number> = {}): string {
  const raw = table[lang][key] ?? table.es[key] ?? key;
  const text = Array.isArray(raw) ? raw.join(", ") : raw;
  return text.replace(/\{(\w+)\}/g, (m, k) => (k in args ? String(args[k]) : m));
}

export function trList(key: string): string[] {
  const raw = table[lang][key] ?? table.es[key];
  return Array.isArray(raw) ? raw : [];
}

/** día de la semana abreviado (lunes = 0, como en Python) */
export function dayName(d: Date): string {
  return trList("days")[(d.getDay() + 6) % 7] ?? "";
}
