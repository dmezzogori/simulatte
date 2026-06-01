import { test } from "node:test";
import assert from "node:assert/strict";
import { extractRunnableBlocks } from "./extract-runnable.mjs";

test("extracts only blocks whose fence info contains .run", () => {
  const md = [
    "```python { .run }",
    "print('A')",
    "```",
    "",
    "```python",
    "print('B not tagged')",
    "```",
  ].join("\n");
  const blocks = extractRunnableBlocks(md);
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].trim(), "print('A')");
});

test("returns empty array when nothing is tagged", () => {
  assert.deepEqual(extractRunnableBlocks("```python\nx=1\n```"), []);
});

test("handles multiple tagged blocks", () => {
  const md = "```python {.run}\na=1\n```\ntext\n```python { .run }\nb=2\n```";
  assert.equal(extractRunnableBlocks(md).length, 2);
});
