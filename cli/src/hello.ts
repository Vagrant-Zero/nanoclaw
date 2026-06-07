import { checkHealth } from "./client.js"

try {
  const data = await checkHealth()
  console.log(`Server status: ${data.status} (v${data.version})`)
} catch (error) {
  console.error("Failed to connect to Nanoclaw backend.")
  console.error("Make sure the server is running: cd backend && uv run python -m nanoclaw.main")
  console.error(`Error: ${error}`)
}
