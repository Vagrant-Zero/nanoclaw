import * as fs from "node:fs/promises";
import { join } from "node:path";
import { Config } from "./types";

const CONFIG_DIR = join(process.cwd(), ".nanoclaw")
const CONFIG_PATH = join(CONFIG_DIR, "config.json")

const DEFAULT_CONFIG: Config = {
    baseUrl: "http://127.0.0.1:8420",
    default_model: "deepseek-v4-pro",
    apiKey: undefined,
}

export async function loadConfig(): Promise<Config> {
    try {
        const content = await fs.readFile(CONFIG_PATH, "utf-8")
        return {...DEFAULT_CONFIG, ...JSON.parse(content)}
    } catch (err: any) {
        if (err?.code === "ENOENT") {
            await fs.mkdir(CONFIG_DIR, {recursive: true})
            await fs.writeFile(CONFIG_PATH, JSON.stringify(DEFAULT_CONFIG, null, 2), "utf-8")
            return DEFAULT_CONFIG
        }
        throw err
    }
}