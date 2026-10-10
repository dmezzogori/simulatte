/**
 * Delta operations (spec §6.2): the reference semantics are `apply_deltas` in `simulatte/events.py`.
 *
 * A state maps an entity id to its fields; `create` stores the entity kind under `"$kind"` next to the initial
 * fields. A `set` on a field the entity does not hold yet creates it (ruling R12). Collections are replaced by
 * updated copies, never changed in place, so values can be shared between states. `remove` and `move` find the first
 * item whose canonical encoding equals the value's ({@link wireEquals}, ruling R31); list copies keep the record of
 * which items were float-encoded.
 */

import { defineKey, isFloatAt, markFloats, newMap, wireEquals } from "./wire";

/** A delta operation: its name followed by its arguments. */
export type Op = readonly unknown[];

/** Replay state: entity id to field values, each with the entity kind under `"$kind"`. */
export type State = Record<string, Record<string, unknown>>;

/** A delta operation that cannot be applied to the state (the Python reader raises KeyError, ValueError, TypeError). */
export class DeltaError extends Error {
  override readonly name = "DeltaError";
}

/** Apply `deltas` in order to `state`. Throws {@link DeltaError} for an operation that does not fit the state. */
/** The number of items of each operation, its name included. */
const ARITY: Readonly<Record<string, number>> = Object.freeze(
  Object.assign(Object.create(null), { set: 4, insert: 5, remove: 4, move: 5, put: 5, delete: 4, create: 4, retire: 2 }),
);

export function applyDeltas(state: State, deltas: Iterable<Op>): void {
  for (const op of deltas) {
    const name = op[0];
    const arity = typeof name === "string" ? ARITY[name] : undefined;
    if (arity === undefined) throw new DeltaError(`unknown delta operation ${JSON.stringify(name)}`);
    if (op.length !== arity) throw new DeltaError(`${name}: an operation of ${arity} items, got ${op.length}`);
    if (name === "create") {
      const entity = text(op[1], "entity");
      if (Object.hasOwn(state, entity)) throw new DeltaError(`create: entity ${JSON.stringify(entity)} already exists`);
      const fields = newMap();
      for (const [key, value] of Object.entries(mapOf(op[3], "create state"))) defineKey(fields, key, value);
      defineKey(fields, "$kind", text(op[2], "kind"));
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
      defineKey(fields, field, inserted(entries(listOf(current, field)), integer(op[3]), [op[4], isFloatAt(op, 4)]));
    } else if (name === "remove") {
      defineKey(fields, field, list(without(listOf(current, field), op[3], isFloatAt(op, 3))));
    } else if (name === "move") {
      const rest = without(listOf(current, field), op[3], isFloatAt(op, 3));
      defineKey(fields, field, inserted(rest, integer(op[4]), [op[3], isFloatAt(op, 3)]));
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

/** An item of a list with whether it was float-encoded (see `isFloatAt`). */
type Entry = readonly [unknown, boolean];

function entries(items: readonly unknown[]): Entry[] {
  return items.map((item, i) => [item, isFloatAt(items, i)]);
}

/** A new list of `items`, with their float encodings recorded. */
function list(items: readonly Entry[]): unknown[] {
  const result = items.map(([item]) => item);
  markFloats(
    result,
    items.map(([, float]) => float),
  );
  return result;
}

function inserted(items: readonly Entry[], index: number, entry: Entry): unknown[] {
  return list([...items.slice(0, index), entry, ...items.slice(index)]);
}

/** `items` without the first one whose canonical encoding equals `value`'s (ruling R31). */
function without(items: readonly unknown[], value: unknown, valueFloat: boolean): Entry[] {
  const index = items.findIndex((item, i) => wireEquals(item, value, isFloatAt(items, i), valueFloat));
  if (index < 0) throw new DeltaError("remove or move of a value the list does not hold");
  const rest = entries(items);
  rest.splice(index, 1);
  return rest;
}
