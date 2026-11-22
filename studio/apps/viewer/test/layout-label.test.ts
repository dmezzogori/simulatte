import { expect, it } from "vitest";
import { layoutLabel } from "../src/layout-label";

it("labels 1.0 and 1.1 recordings as the recorded layout and anything else as the fallback", () => {
  expect(layoutLabel({ schema_version: "1.0" })).toBe("recorded layout");
  expect(layoutLabel({ schema_version: "1.1" })).toBe("recorded layout");
  expect(layoutLabel({ schema_version: "9.0" })).toBe("viewer fallback layout");
  expect(layoutLabel(null)).toBe("viewer fallback layout");
});
