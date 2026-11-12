/* One answer to "is this fan on?" for every page.
   The source of truth is what the smart plug last reported (switch.confirmed), the same value
   the history records as fans_on. The controller's intent (group is_on, switch.commanded) and
   shadow/practice decisions are never shown as the fan's state. */
import { Json } from "./api";

export const plugOn = (sw: Json) => !!(sw && sw.confirmed === true);

/** The plug was asked for a state it has not confirmed yet (and it is still answering). */
export const plugPending = (sw: Json) =>
  !!(sw && sw.reachable !== false && sw.commanded != null && sw.confirmed != null && sw.commanded !== sw.confirmed);

export type ExhaustState = { fans: Json[]; on: number; total: number; anyOn: boolean; pending: boolean; cfm: number };

export function exhaustState(status: Json): ExhaustState {
  const fans: Json[] = [];
  ((status && status.groups) || []).forEach((g: Json) => (g.fans || []).forEach((f: Json) => fans.push(f)));
  const onFans = fans.filter((f) => plugOn(f.switch));
  return {
    fans, on: onFans.length, total: fans.length, anyOn: onFans.length > 0,
    pending: fans.some((f) => plugPending(f.switch)),
    cfm: onFans.reduce((s, f) => s + (f.actual_cfm || f.rated_cfm || 0), 0),
  };
}

/** Circulation fans: the plug's report when there is one, else the controller's verified state. */
export function circOn(c: Json) {
  const sw = c && c.switch;
  return sw && sw.confirmed != null ? sw.confirmed === true : !!(c && c.is_on);
}
