/**
 * Delta operations (spec §6.2): the reference semantics are `apply_deltas` in `simulatte/events.py`.
 *
 * A state maps an entity id to its fields; `create` stores the entity kind under `"$kind"` next to the initial
 * fields. A `set` on a field the entity does not hold yet creates it (ruling R12). Collections are replaced by
 * updated copies, never changed in place, so values can be shared between states.
 */

import { defineKey, newMap, wireEquals } from "./wire";

/** A delta operation: its name followed by its arguments. */
export type Op = readonly unknown[];

/** Replay state: entity id to field values, each with the entity kind under `"$kind"`. */
export type State = Record<string, Record<string, unknown>>;

/** A delta operation that cannot be applied to the state (the Python reader raises KeyError, ValueError, TypeError). */
export class DeltaError extends Error {
  override readonly name = "DeltaError";
}

/** Apply `deltas` in order to `state`. Throws {@link DeltaError} for an operation that does not fit the state. */
export function applyDeltas(state: State, deltas: Iterable<Op>): void {
  for (const op of deltas) {
    const name = op[0];
    if (name === "create") {
      const entity = text(op[1], "entity");
      if (Object.hasOwn(state, entity)) throw new DeltaError(`create: entity ${JSON.stringify(entity)} already exists`);
      const fields = newMap();
      for (const [key, value] of Object.entries(mapOf(op[3], "create state"))) defineKey(fields, key, value);
      defineKey(fields, "$kind", op[2]);
      defineKey(state, entity, fields);
      continue;
    }
    if (name === "retire") {
      const entity = text(op[1], "entity");
      entityFields(state, entity);
      delete state[entity];
      continue;
    }
    const fields = entityFields(state, text(op[1], "entity"));
    const field = text(op[2], "field");
    if (name === "set") {
      defineKey(fields, field, op[3]);
      continue;
    }
    if (!Object.hasOwn(fields, field)) throw new DeltaError(`${String(name)}: missing field ${JSON.stringify(field)}`);
    const current = Object.getOwnPropertyDescriptor(fields, field)?.value;
    if (name === "insert") {
      const items = listOf(current, field);
      const index = integer(op[3]);
      defineKey(fields, field, [...items.slice(0, index), op[4], ...items.slice(index)]);
    } else if (name === "remove") {
      defineKey(fields, field, without(listOf(current, field), op[3]));
    } else if (name === "move") {
      const rest = without(listOf(current, field), op[3]);
      const index = integer(op[4]);
      defineKey(fields, field, [...rest.slice(0, index), op[3], ...rest.slice(index)]);
    } else if (name === "put") {
      const updated = copyOf(mapOf(current, field));
      defineKey(updated, text(op[3], "key"), op[4]);
      defineKey(fields, field, updated);
    } else if (name === "delete") {
      const key = text(op[3], "key");
      const source = mapOf(current, field);
      if (!Object.hasOwn(source, key)) throw new DeltaError(`delete: missing key ${JSON.stringify(key)}`);
      const updated = copyOf(source);
      delete updated[key];
      defineKey(fields, field, updated);
    } else {
      throw new DeltaError(`unknown delta operation ${JSON.stringify(name)}`);
    }
  }
}

function entityFields(state: State, entity: string): Record<string, unknown> {
  const fields = Object.hasOwn(state, entity) ? Object.getOwnPropertyDescriptor(state, entity)?.value : undefined;
  if (fields === undefined || fields === null || typeof fields !== "object") {
    throw new DeltaError(`unknown entity ${JSON.stringify(entity)}`);
  }
  return fields as Record<string, unknown>;
}

function text(value: unknown, what: string): string {
  if (typeof value !== "string") throw new DeltaError(`${what} must be a string`);
  return value;
}

function integer(value: unknown): number {
  if (typeof value !== "number" || !Number.isInteger(value)) throw new DeltaError("index must be an integer");
  return value;
}

function listOf(value: unknown, field: string): readonly unknown[] {
  if (!Array.isArray(value)) throw new DeltaError(`field ${JSON.stringify(field)} is not a list`);
  return value;
}

function mapOf(value: unknown, what: string): Record<string, unknown> {
  if (value === null || typeof value !== "object" || Array.isArray(value)) {
    throw new DeltaError(`${what} is not a map`);
  }
  return value as Record<string, unknown>;
}

function copyOf(source: Record<string, unknown>): Record<string, unknown> {
  const copy = newMap();
  for (const key of Object.keys(source)) defineKey(copy, key, Object.getOwnPropertyDescriptor(source, key)?.value);
  return copy;
}

function without(items: readonly unknown[], value: unknown): unknown[] {
  const index = items.findIndex((item) => wireEquals(item, value));
  if (index < 0) throw new DeltaError("remove or move of a value the list does not hold");
  return [...items.slice(0, index), ...items.slice(index + 1)];
}
