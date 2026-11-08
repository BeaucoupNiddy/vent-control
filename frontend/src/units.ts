import { createContext, useContext } from "react";

export type TempUnit = "F" | "C";
export const UnitContext = createContext<TempUnit>("F");
export const useUnit = () => useContext(UnitContext);

export const cToF = (c: number) => (c * 9) / 5 + 32;

export function temp(c: number | null | undefined, u: TempUnit, digits = 1): string {
  if (c === null || c === undefined || Number.isNaN(c)) return "—";
  return `${(u === "F" ? cToF(c) : c).toFixed(digits)}°${u}`;
}

export function tempVal(c: number | null | undefined, u: TempUnit): number | null {
  if (c === null || c === undefined) return null;
  return u === "F" ? cToF(c) : c;
}

export function deltaTemp(dF: number | null | undefined, u: TempUnit, digits = 1): string {
  if (dF === null || dF === undefined) return "—";
  const v = u === "F" ? dF : (dF * 5) / 9;
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}°${u}`;
}

export const pct = (v: number | null | undefined, d = 0) => (v === null || v === undefined ? "—" : `${v.toFixed(d)}%`);
export const num = (v: number | null | undefined, d = 1) => (v === null || v === undefined ? "—" : v.toFixed(d));

/** Moisture transfer: positive = removal, negative = addition (spec: never show negative removal as removal). */
export function moisture(gPerH: number | null | undefined): { text: string; kind: "remove" | "add" | "none" } {
  if (gPerH === null || gPerH === undefined) return { text: "—", kind: "none" };
  const galDay = (Math.abs(gPerH) * 24) / 3785.411784;
  if (Math.abs(gPerH) < 1) return { text: "≈ 0", kind: "none" };
  const t = `${galDay.toFixed(2)} gal/day (${Math.abs(gPerH).toFixed(0)} g/h)`;
  return gPerH > 0 ? { text: `removes ${t}`, kind: "remove" } : { text: `adds ${t}`, kind: "add" };
}

export function duration(s: number | null | undefined): string {
  if (s === null || s === undefined) return "—";
  const m = Math.floor(s / 60);
  if (m < 60) return `${m} min`;
  return `${Math.floor(m / 60)} h ${m % 60} min`;
}

export function hours(h: number | null | undefined, d = 1) {
  return h === null || h === undefined ? "—" : `${h.toFixed(d)} h`;
}

export function localTime(iso: string | null | undefined, withDate = false) {
  if (!iso) return "—";
  const d = new Date(iso);
  return d.toLocaleString(undefined, withDate
    ? { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }
    : { hour: "numeric", minute: "2-digit" });
}
