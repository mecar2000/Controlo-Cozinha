/**
 * Unit — the unit suffix that follows a number.
 *
 * One component rather than a repeated pair of classes, because a unit has to
 * look identical in the rail, on the wall display and in a modal, and three
 * hand-written copies of "text-label text-ink-dim" drift apart the moment one
 * of them is edited.
 *
 * The unit is set a step below its number and in the dim ink: "%v/v" is not
 * data, it is what the data is measured in. Sizing it like the value makes
 * every reading look twice as long as it is.
 */

export function Unit({ children }: { children: React.ReactNode }) {
  return (
    <span className="ml-1 font-sans text-label font-normal text-ink-dim">{children}</span>
  )
}
