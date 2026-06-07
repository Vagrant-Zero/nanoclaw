import { createInterface } from "node:readline/promises"
import { stdin, stdout } from "node:process"
import { sendMessage, sendMessageStream } from "./client.js"
import { loadConfig } from "./config";

const config = await loadConfig()
const command = process.argv[2]

if (command === "config") {
  // nanoclaw config --show
  console.log(JSON.stringify(config, null, 2))
  process.exit(0)
}

// 默认：nanoclaw chat
const rl = createInterface({ input: stdin, output: stdout })
const history: string[] = []

console.log("╔════════════════════════╗")
console.log("║      Nanoclaw CLI      ║")
console.log("╚════════════════════════╝")
console.log('Type /exit to quit\n')

while (true) {
  const input = await rl.question("> ")
  if (!input || input === "/exit") break
  if (input === "/history") {
    console.log(`\n${history}\n`)
    continue
  }

  history.push(input)
  try {
    const response = await sendMessageStream(config.baseUrl, { message: input })
    history.push(response)
  } catch (error) {
    console.error(`\nError: ${error}\n`)
  }
}

rl.close()
console.log("Goodbye!")
