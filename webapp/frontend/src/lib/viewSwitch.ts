/**
 * viewSwitch — should a phase change pull the operator's screen to Display?
 *
 * problems.txt Area C1: "When a leak actually starts it should send to
 * Display." This is an EDGE trigger, not a level check: it fires exactly
 * once on the transition into LEAKING, so an operator who navigates back to
 * Control mid-leak is not yanked back to Display again for the same leak.
 */

export function shouldSwitchToDisplayOnLeak(
  previousPhase: string | null | undefined,
  currentPhase: string | null | undefined,
): boolean {
  return currentPhase === 'LEAKING' && previousPhase !== 'LEAKING'
}
