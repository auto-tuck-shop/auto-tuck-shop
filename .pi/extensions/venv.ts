import { createBashTool, type ExtensionAPI } from "@mariozechner/pi-coding-agent";

export default function (pi: ExtensionAPI) {
  const cwd = process.cwd();

  pi.registerTool(
    createBashTool(cwd, {
      spawnHook: ({ command, cwd, env }) => ({
        command: `source .venv/bin/activate\n${command}`,
        cwd,
        env,
      }),
    })
  );
}
