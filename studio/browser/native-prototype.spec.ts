import { test, expect } from "@playwright/test";
import { resolve } from "node:path";

test("native trace opens, steps both directions, seeks, plays and renders in the production viewer", async ({page}) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await page.goto("/");
  await page.locator("input[type=file]").setInputFiles(resolve("../prototypes/native_engine_dynamic/fixtures/native.simtrace"));
  await expect(page.locator(".status")).toHaveText("paused");
  await expect(page.locator("output")).toContainText("activation");
  for (let i = 0; i < 9; i++) await page.getByRole("button", {name: "Next event", exact: true}).click();
  const after = await page.locator("output").textContent();
  await page.getByRole("button", {name: "Previous event", exact: true}).click();
  await expect(page.locator("output")).not.toHaveText(after!);
  await page.getByRole("button", {name: "Next event", exact: true}).click();
  await expect(page.locator("output")).toHaveText(after!);
  await expect(page.locator("canvas")).toBeVisible();
  await page.locator("input[type=range]").fill("3");
  await expect(page.locator("output")).toContainText("t = 3");
  await page.getByRole("button", {name: "Play", exact: true}).click();
  await page.waitForTimeout(150);
  await page.getByRole("button", {name: "Pause", exact: true}).click({delay: 100});
  await expect(page.locator(".status")).toHaveText("paused");
  expect(errors).toEqual([]);
});
