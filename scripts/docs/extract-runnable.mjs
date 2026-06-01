// Extract the source of every runnable-tagged fenced code block from markdown.
// Marker (Task 2): an opening fence whose info string contains `.run`,
// e.g. ```python { .run }  or  ```python {.run}
export function extractRunnableBlocks(markdown) {
  const lines = markdown.split("\n");
  const blocks = [];
  let inBlock = false;
  let tagged = false;
  let buf = [];
  const fenceOpen = /^```+\s*(.*)$/;

  for (const line of lines) {
    if (!inBlock) {
      const m = line.match(fenceOpen);
      if (m) {
        inBlock = true;
        tagged = /(^|[\s{.])\.?run(\b|})/.test(m[1]) || /\.run\b/.test(m[1]);
        buf = [];
      }
      continue;
    }
    if (/^```+\s*$/.test(line)) {
      if (tagged) blocks.push(buf.join("\n"));
      inBlock = false;
      tagged = false;
      continue;
    }
    buf.push(line);
  }
  return blocks;
}
