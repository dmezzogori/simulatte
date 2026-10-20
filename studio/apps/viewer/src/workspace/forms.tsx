/** Shared form primitives: labelled fields with units and hints, compact controls, grouped sections. */
import { cloneElement, useId, type ReactElement, type ReactNode } from "react";
interface ControlProps {
  id?: string;
  "aria-describedby"?: string;
}
/** A labelled control. The unit stays outside the label so the accessible name is the label alone. */
export function Field({
  label,
  unit,
  hint,
  required = false,
  wide = false,
  children,
}: {
  label: ReactNode;
  unit?: string;
  hint?: ReactNode;
  required?: boolean;
  wide?: boolean;
  children: ReactElement<ControlProps>;
}) {
  const id = useId();
  const hintId = hint || required ? id + "-hint" : undefined;
  return (
    <div className={"ws-field" + (wide ? " ws-wide" : "")}>
      <label htmlFor={id}>{label}</label>
      <div className="ws-control">
        {cloneElement(children, {
          id,
          ...(hintId ? { "aria-describedby": hintId } : {}),
        })}
        {unit && (
          <span className="ws-unit" aria-hidden="true">
            {unit}
          </span>
        )}
      </div>
      {hintId && (
        <small id={hintId}>
          {hint}
          {hint && required ? " · " : ""}
          {required ? "Required" : ""}
        </small>
      )}
    </div>
  );
}
/** A checkbox or radio with its label as the accessible name. */
export function CheckField({
  label,
  hint,
  children,
}: {
  label: ReactNode;
  hint?: ReactNode;
  children: ReactElement;
}) {
  return (
    <label className="ws-check">
      {children}
      <span>
        {label}
        {hint && <small>{hint}</small>}
      </span>
    </label>
  );
}
/** A titled group of fields on a two-column grid; disables all controls together. */
export function FieldGroup({
  legend,
  hint,
  disabled = false,
  children,
}: {
  legend: ReactNode;
  hint?: ReactNode;
  disabled?: boolean;
  children: ReactNode;
}) {
  return (
    <fieldset className="ws-fieldset" disabled={disabled}>
      <legend>{legend}</legend>
      {hint && <p className="ws-hint">{hint}</p>}
      <div className="ws-form-grid">{children}</div>
    </fieldset>
  );
}
/** A row of actions with an explanatory note on the left. */
export function Actions({
  note,
  children,
}: {
  note?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="ws-actions">
      {note && <small className="ws-note">{note}</small>}
      {children}
    </div>
  );
}

/** Props for a control that may be disabled: the first reason given wins and becomes the
 * control's ``title``, so no disabled action is left unexplained. */
export function disabledWhen(
  ...reasons: (string | false | null | undefined)[]
): { disabled?: true; title?: string } {
  const reason = reasons.find((r) => typeof r === "string" && r);
  return reason ? { disabled: true, title: reason } : {};
}
export const NOT_CONNECTED = "Studio is not connected; the action resumes when the connection returns";
export const READ_ONLY = "This session is read-only";
export const WORKING = "Working; wait for the current action to finish";
