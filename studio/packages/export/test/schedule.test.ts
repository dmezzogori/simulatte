import { expect, it } from "vitest";
import { frameSchedule, offlineHtml } from "../src/index";
it("includes exactly one terminal hold and strictly increasing microsecond timestamps", () => {
  const schedule = frameSchedule(10, 11, 30, 1);
  expect(schedule.count).toBe(31);
  expect(schedule.frame(30)).toEqual({
    time: 11,
    timestamp: 1000000,
    duration: 33333,
  });
  expect(frameSchedule(0, 0, 24, 1).count).toBe(1);
  expect(() => frameSchedule(0, 100000, 30, 1)).toThrow("100,000");
  expect(() => frameSchedule(1e16, 1e16 + 2, 30, 1)).toThrow("precision");
});
it("embeds only base64 trace data and hashes its escaped executable script", async () => {
  const html = await offlineHtml(
    new Blob(["</script><script>alert(1)</script>"]),
    `console.log('</script>')`,
  );
  const text = await html.text();
  expect(text).not.toContain("<script>alert");
  expect(text).toContain("connect-src");
  expect(text).toContain("sha256-");
  expect(text.match(/<script/g)).toHaveLength(2);
});

it("enforces HTML size and cancellation before reading source bytes", async()=>{
 await expect(offlineHtml({size:100*1024*1024+1} as Blob," ")).rejects.toThrow("100 MiB");
 const abort=new AbortController();abort.abort();
 await expect(offlineHtml(new Blob(["data"]),"", "",abort.signal)).rejects.toMatchObject({name:"AbortError"});
 const text=await (await offlineHtml(new Blob(["safe"]),"", "body{color:red}")).text();
 expect(text).toContain("form-action 'none'");expect(text).not.toContain("unsafe-inline");expect(text).toContain("style-src 'sha256-");
});
