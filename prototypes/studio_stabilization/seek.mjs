/** Use the live slider's current bounds in the same browser task as the input. */
export function seekMidpoint(range) {
  return range.evaluate(input => {
    const min = Number(input.min), max = Number(input.max);
    if (input.disabled || !Number.isFinite(min) || !Number.isFinite(max) || max <= min) return false;
    // The native numeric setter lets React observe the changed value on input.
    input.valueAsNumber = min / 2 + max / 2;
    input.dispatchEvent(new Event('input', {bubbles: true}));
    return true;
  });
}
