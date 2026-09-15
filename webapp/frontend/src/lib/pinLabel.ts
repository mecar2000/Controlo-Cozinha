/**
 * pinLabel — encode a raw DataAcquisition pin integer the way its own
 * dashboard displays it (dashboard/app/conversion.py::_pin_label). Base
 * board pins 0..7 are "A0".."A7"; expansion pins are
 * 100*(expansionIndex+1) + channel, decoded back to
 * "E{expansionIndex}:CH{channel}". Mirrors the backend's app.daq.pin_label
 * — this is the only place this app's frontend implements the encoding.
 */
export function pinLabel(pin: number): string {
  if (pin >= 100) {
    const expIdx = Math.floor(pin / 100) - 1
    const channel = pin % 100
    return `E${expIdx}:CH${channel}`
  }
  return `A${pin}`
}
