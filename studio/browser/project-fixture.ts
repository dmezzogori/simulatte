/** A fresh wheel install and real project server; no source-tree imports or mocked socket. */
import { spawn, execFileSync, type ChildProcess } from "node:child_process";
import {
  mkdtemp,
  rm,
  mkdir,
  copyFile,
  readFile,
  writeFile,
  readdir,
} from "node:fs/promises";
import { tmpdir } from "node:os";
import { join, resolve, dirname } from "node:path";
export class ProjectFixture {
  root = "";
  directory = "";
  executable = "";
  python = "";
  wheel = "";
  child: ChildProcess | null = null;
  links: string[] = [];
  diagnostic = "";
  async setup() {
    this.directory = await mkdtemp(
      join(tmpdir(), "simulatte-project-browser-"),
    );
    this.root = join(this.directory, "project");
    await mkdir(this.root);
    const wheels = (await readdir(resolve("../dist"))).filter((x) =>
      x.endsWith(".whl"),
    );
    if (wheels.length !== 1)
      throw Error(
        "Build exactly one wheel with packaged Studio assets before the project browser suite",
      );
    this.wheel = resolve("../dist", wheels[0]!);
    if (process.env.SIMULATTE_PROJECT_EXECUTABLE) {
      this.executable = resolve(process.env.SIMULATTE_PROJECT_EXECUTABLE);
      this.python = join(
        dirname(this.executable),
        process.platform === "win32" ? "python.exe" : "python",
      );
      return;
    }
    const env = join(this.directory, "launcher");
    execFileSync("uv", ["venv", env, "--python", "3.14"], { stdio: "pipe" });
    this.python = join(
      env,
      process.platform === "win32" ? "Scripts/python.exe" : "bin/python",
    );
    this.executable = join(
      env,
      process.platform === "win32" ? "Scripts/simulatte.exe" : "bin/simulatte",
    );
    execFileSync(
      "uv",
      ["pip", "install", "--python", this.python, this.wheel + "[studio]"],
      { stdio: "pipe" },
    );
  }
  async launch(target = this.root) {
    this.links = [];
    this.child = spawn(
      this.executable,
      ["studio", target, "--port", "0", "--no-browser"],
      {
        cwd: this.root,
        env: { ...process.env, PYTHONUNBUFFERED: "1" },
        stdio: ["ignore", "pipe", "pipe"],
      },
    );
    let buffer = "";
    this.child.stdout!.on("data", (data) => {
      buffer = (buffer + data.toString()).slice(-65536);
      let m: RegExpExecArray | null;
      const re = /http:\/\/127\.0\.0\.1:\d+\/bootstrap\?token=[\w-]+/g;
      while ((m = re.exec(buffer))) {
        if (!this.links.includes(m[0])) this.links.push(m[0]);
      }
    });
    this.child.stderr!.on("data", (data) => {
      this.diagnostic = (this.diagnostic + data.toString()).slice(-10000);
    });
    return this.link(0);
  }
  async link(index: number) {
    const deadline = Date.now() + 45000;
    while (!this.links[index]) {
      if (Date.now() > deadline || this.child?.exitCode !== null)
        throw Error(
          "Project startup/handoff failed: " +
            this.diagnostic.replace(/token=[\w-]+/g, "token=[redacted]"),
        );
      await new Promise((r) => setTimeout(r, 100));
    }
    return this.links[index]!;
  }
  async useDevelopmentWheel() {
    // Explicit project-relative source declaration for the not-yet-released wheel.
    const wheel = this.wheel.split(/[\\/]/).pop()!;
    await mkdir(join(this.root, "vendor"));
    await copyFile(this.wheel, join(this.root, "vendor", wheel));
    const path = join(this.root, "pyproject.toml");
    let text = await readFile(path, "utf8");
    text = text.replace(
      'include = ["models/*.py"]',
      'include = ["models/*.py", "vendor/*.whl"]',
    );
    text +=
      '\n[tool.uv.sources]\nsimulatte = {path = "vendor/' + wheel + '"}\n';
    await writeFile(path, text);
  }
  async stop() {
    const child = this.child;
    if (!child || child.exitCode !== null) return;
    const done = new Promise<void>((r) => child.once("exit", () => r()));
    child.kill("SIGINT");
    await Promise.race([done, new Promise((r) => setTimeout(r, 15000))]);
    if (child.exitCode === null) {
      child.kill("SIGKILL");
      await done;
    }
    this.child = null;
  }
  async close() {
    await this.stop();
    await rm(this.directory, { recursive: true, force: true });
  }
}
